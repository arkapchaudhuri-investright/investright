"""Nightly refresh — the cron side of DESIGN.md §3: cron writes, the web app reads.

Updates every watchlist snapshot, the USD/INR rate, and (Phase 3) each stock's
deep data — fundamentals, news, health checks and DCF — into SQLite. fetch.py
drops failed fetches, so last-good rows always survive a Yahoo outage.
Phase 4 tail: re-rank the screener from the saved rows, then ask the free LLM
for tonight's digest (any failure just keeps the last saved one).

Run manually:  .venv/bin/python refresh.py
Installed as:  0 22 * * *  cd ~/Desktop/InvestRight && .venv/bin/python refresh.py >> data/refresh.log 2>&1
(10 PM Central = after US close at 3 PM and India close at ~5 AM the same day.)
"""
import sys
from datetime import date, datetime

import digest
import edgar
import fetch
import logos
import metrics
from db import (get_conn, init_db, save_checks, save_dcf, save_digest,
                save_fundamentals, save_insiders, save_news, save_screener,
                save_snapshot)


def peer_symbols(symbols):
    """Peers of `symbols` from the hand-curated map (§8.5 Tier C), deduped and
    minus the watchlist itself — their data feeds the mini-snowflakes."""
    out = []
    for s in symbols:
        for p in metrics.PEERS.get(s, []):
            if p not in out and p not in symbols:
                out.append(p)
    return out


def ensure_stock(conn, ticker):
    """Give a (peer) ticker a stocks row so snapshots/fundamentals can FK to it
    and /stock/<ticker> resolves. True if the row exists afterwards."""
    if conn.execute("SELECT 1 FROM stocks WHERE ticker=?", (ticker,)).fetchone():
        return True
    meta = fetch.lookup(ticker)
    if not meta:
        return False
    conn.execute("INSERT INTO stocks (ticker,name,exchange,sector,currency,added_at) "
                 "VALUES (?,?,?,?,?,?)",
                 (meta["ticker"], meta["name"], meta["exchange"], meta["sector"],
                  meta["currency"], datetime.now().isoformat(timespec="seconds")))
    try:
        logos.ensure(meta["ticker"], meta.get("website"))
    except Exception:
        pass
    return True


def save_deep(conn, ticker):
    """Fetch + persist one stock's fundamentals, news, checks and DCF.

    Each piece is best-effort: whatever Yahoo won't serve is simply skipped so the
    page keeps its last-good rows. Returns True if any fundamentals landed.

    US tickers (no exchange suffix) try SEC EDGAR first for a 10-yr statement
    series (§8.5 Tier B); India (.NS/.BO) has no EDGAR equivalent and stays on
    the yfinance ~4yr path (§1, §8.0).
    """
    data = fetch.deep(ticker)
    funds, ratios, news = data["fundamentals"], data["ratios"], data["news"]
    source = "yfinance"

    try:  # cache the company logo (best-effort; find() short-circuits if cached)
        logos.ensure(ticker, ratios.get("website"))
    except Exception:
        pass

    if "." not in ticker:
        try:
            edgar_funds = edgar.fundamentals(ticker)
        except Exception:
            edgar_funds = []
        if edgar_funds:
            funds, source = edgar_funds, "edgar"

    if funds:
        save_fundamentals(conn, ticker, funds, source=source)
    if news:
        save_news(conn, ticker, news)

    if "." not in ticker:                 # Form 4 is US-only (§4.8, Tier C)
        try:
            tx = edgar.insider_transactions(ticker)
        except Exception:
            tx = []
        if tx:
            save_insiders(conn, ticker, tx)

    if not funds or not ratios:
        return bool(funds)

    # Peer-average P/E from saved peer snapshots → the pe_peer value check.
    peers = metrics.PEERS.get(ticker, [])
    if peers:
        marks = ",".join("?" * len(peers))
        pes = [r["pe"] for r in conn.execute(
            f"SELECT pe FROM snapshots WHERE ticker IN ({marks})", peers) if r["pe"]]
        if pes:
            ratios["peer_pe"] = round(sum(pes) / len(pes), 1)

    # Every saved payer's yield → the yield_top25 dividend check (Phase 4).
    ratios["payer_yields"] = [r["div_yield"] for r in conn.execute(
        "SELECT div_yield FROM snapshots WHERE div_yield > 0")]

    dcf = metrics.compute_dcf(funds, ratios.get("price"), ratios.get("shares"))
    if dcf:
        save_dcf(conn, ticker, dcf)
    checks = metrics.compute_checks(funds, ratios, dcf or {})
    if checks:
        save_checks(conn, ticker, checks)
    return True


def run_screener(conn):
    """Re-rank every saved ticker for /today from rows already in SQLite — pure
    DB→DB, no fetching (Phase 4, §4). Cheap enough that ↻ and /add run it too,
    so the ranking never lags the deep data it's built from."""
    watch = {r["ticker"] for r in conn.execute("SELECT ticker FROM watchlist")}
    cands = []
    for s in conn.execute("SELECT ticker FROM stocks ORDER BY ticker"):
        t = s["ticker"]
        checks = [{"axis": r["axis"], "check_id": r["check_id"],
                   "passed": None if r["passed"] is None else bool(r["passed"])}
                  for r in conn.execute(
                      "SELECT axis, check_id, passed FROM health_checks WHERE ticker=?", (t,))]
        if not checks:
            continue          # nothing computed yet (brand-new peer) — skip, don't zero-score
        d = conn.execute("SELECT upside_pct FROM dcf WHERE ticker=?", (t,)).fetchone()
        ins = conn.execute(
            "SELECT SUM(action='buy') AS buys, SUM(action='sell') AS sells "
            "FROM insider_tx WHERE ticker=? AND filed_at >= date('now','-90 day')",
            (t,)).fetchone()
        snap = conn.execute("SELECT div_yield FROM snapshots WHERE ticker=?", (t,)).fetchone()
        cands.append({"ticker": t, "is_watchlist": t in watch, "checks": checks,
                      "upside_pct": d["upside_pct"] if d else None,
                      "insider_buys": ins["buys"] or 0,
                      "insider_sells": ins["sells"] or 0,
                      "div_yield": snap["div_yield"] if snap else None})
    rows = metrics.screen(cands)
    save_screener(conn, rows)
    return rows


def run_digest(conn, rows, top_n=5):
    """Tonight's AI note on the screener's top picks (§4 "Today"). Best-effort
    like every fetcher: no key / quota / network trouble ⇒ the page keeps the
    last saved digest, honestly dated. Returns a status string for the log."""
    picks = []
    for r in rows[:top_n]:
        srow = conn.execute("SELECT name FROM stocks WHERE ticker=?", (r["ticker"],)).fetchone()
        d = conn.execute("SELECT upside_pct FROM dcf WHERE ticker=?", (r["ticker"],)).fetchone()
        news = [n["title"] for n in conn.execute(
            "SELECT title FROM news WHERE ticker=? ORDER BY published_at DESC LIMIT 2",
            (r["ticker"],))]
        picks.append({"ticker": r["ticker"],
                      "name": srow["name"] if srow else r["ticker"],
                      "score": r["score"],
                      "upside_pct": d["upside_pct"] if d else None,
                      "reasons": [x["label"] for x in r["reasons"]],
                      "news": news})
    if not picks:
        return "skipped (nothing to summarize)"
    try:
        body, model = digest.generate(picks, date.today().strftime("%-d %B %Y"))
        save_digest(conn, body, model, [p["ticker"] for p in picks])
        return f"ok ({model})"
    except Exception as e:
        return f"skipped ({e})"


def main():
    init_db()
    with get_conn() as conn:
        symbols = [r["ticker"] for r in conn.execute("SELECT ticker FROM watchlist")]

    with get_conn() as conn:  # peers ride along so mini-snowflakes have scores (Tier C)
        peers = [p for p in peer_symbols(symbols) if ensure_stock(conn, p)]
    everyone = symbols + peers

    snaps = fetch.snapshot_many(everyone)
    with get_conn() as conn:
        for snap in snaps:
            save_snapshot(conn, snap)
    failed = sorted(set(symbols) - {s["ticker"] for s in snaps})

    deep_ok = 0
    for sym in everyone:
        with get_conn() as conn:
            try:
                if save_deep(conn, sym):
                    deep_ok += 1
            except Exception as e:                       # never let one bad ticker abort the run
                print(f"  deep refresh failed for {sym}: {e}")

    rate = fetch.fx_rate("USDINR=X")
    if rate:
        with get_conn() as conn:
            conn.execute("INSERT OR REPLACE INTO fx_rates (pair, rate, fetched_on) "
                         "VALUES ('USDINR', ?, ?)", (rate, date.today().isoformat()))

    # Phase 4 tail — rank what just landed, then let the free LLM sum it up.
    with get_conn() as conn:
        ranked = run_screener(conn)
    with get_conn() as conn:
        digest_status = run_digest(conn, ranked)

    # /strategies monthly picks — re-sweep only when the batch is 30+ days old
    # (strategy_screen measures ~100 tickers, a few minutes; failure here must
    # never dent the nightly refresh itself).
    picks_status = "fresh"
    try:
        import strategy_screen
        with get_conn() as conn:
            stale = strategy_screen.is_stale(conn)
        if stale:
            picks_status = f"reswept {strategy_screen.run()} picks"
    except Exception as e:
        picks_status = f"failed: {e}"
        print(f"  strategy screen failed: {e}")

    stamp = datetime.now().isoformat(timespec="seconds")
    print(f"{stamp}  snapshots {len(snaps)}/{len(everyone)} (incl. {len(peers)} peers)"
          + (f" (failed: {', '.join(failed)})" if failed else "")
          + f" · deep {deep_ok}/{len(everyone)}"
          + f" · USDINR {rate if rate else 'fetch failed, kept last-good'}"
          + f" · screener {len(ranked)} ranked · digest {digest_status}"
          + f" · strategy picks {picks_status}")
    return 1 if (symbols and not snaps) or not rate else 0


if __name__ == "__main__":
    sys.exit(main())
