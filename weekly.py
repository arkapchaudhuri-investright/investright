"""Weekly email digest — the Sunday cron side of spec 12.

For each user who opted in (users.weekly_email=1) build a short, plain-text note
about THEIR watchlist — this week's prices and moves — written by the free LLM
(digest.py's provider), with an honest rule-based fallback when the AI is
unavailable. Sending goes through mailer.send(), which is a no-op while SMTP is
unset, so this whole job is safe to run before email is configured.

Run manually:  .venv/bin/python weekly.py
Installed as:  a systemd timer, Sun 08:00 (see systemd/investright-weekly.*).
"""
import sys
from datetime import date, datetime

import digest
import mailer
import metrics
from db import get_conn, init_db


def _week_move(conn, ticker, price):
    """This week's % move: latest price vs the close ~7 days ago (nearest saved
    close at or before the 7-days-ago mark). None when we can't compute it."""
    if price is None:
        return None
    row = conn.execute(
        "SELECT close FROM price_history WHERE ticker=? AND d <= date('now','-7 days') "
        "ORDER BY d DESC LIMIT 1", (ticker,)).fetchone()
    if not row or not row["close"]:
        return None
    return (price / row["close"] - 1) * 100


def _usdinr(conn):
    """Stored USD→INR rate (1 USD = N INR), or None. Read-only — no fetch in the
    cron; last-good is fine for a rough cross-currency portfolio total."""
    row = conn.execute("SELECT rate FROM fx_rates WHERE pair='USDINR'").fetchone()
    return row["rate"] if row and row["rate"] else None


def _to_usd(amount, ccy, rate):
    """Best-effort convert a native amount to USD for the portfolio total. USD is
    itself; INR needs the rate; anything else returns None (left out of the sum)."""
    if amount is None:
        return None
    if ccy == "USD":
        return amount
    if ccy == "INR" and rate:
        return amount / rate
    return None


def _earnings_days(next_earnings):
    if not next_earnings:
        return None
    try:
        d = (date.fromisoformat(next_earnings) - date.today()).days
        return d if 0 <= d <= 60 else None
    except (ValueError, TypeError):
        return None


def _portfolio_section(conn, uid, rate):
    """Section 1 — the user's holdings: per-position P&L% + week move, a rough
    USD total P&L, the biggest mover, and any spec-16 review flags. '' when the
    user holds nothing. Returns (text, held_tickers)."""
    rows = conn.execute(
        "SELECT h.qty, h.avg_price, s.ticker, s.name, s.currency, s.sector, "
        "       s.next_earnings, n.price, n.change_pct "
        "FROM holdings h JOIN stocks s ON s.ticker=h.ticker "
        "LEFT JOIN snapshots n ON n.ticker=h.ticker WHERE h.user_id=? "
        "ORDER BY h.added_at", (uid,)).fetchall()
    if not rows:
        return "", set()

    held = {r["ticker"] for r in rows}
    inv_usd = val_usd = 0.0
    priced = []
    for r in rows:
        price, avg, qty = r["price"], r["avg_price"], r["qty"]
        if price is not None and avg:
            iv = _to_usd(avg * qty, r["currency"], rate)
            vv = _to_usd(price * qty, r["currency"], rate)
            if iv is not None and vv is not None:
                inv_usd += iv
                val_usd += vv
            priced.append((r, (price / avg - 1) * 100,
                           _to_usd(price * qty, r["currency"], rate)))

    lines = []
    if val_usd and inv_usd:
        pnl = val_usd - inv_usd
        lines.append(f"Portfolio value ~${val_usd:,.0f} (invested ~${inv_usd:,.0f}), "
                     f"P&L {pnl:+,.0f} USD ({(pnl / inv_usd * 100):+.1f}%).")

    # Biggest mover this week (by |week move|, else |day change|).
    movers = []
    for r in rows:
        wk = _week_move(conn, r["ticker"], r["price"])
        movers.append((r, wk if wk is not None else r["change_pct"]))
    movers = [m for m in movers if m[1] is not None]
    if movers:
        top = max(movers, key=lambda m: abs(m[1]))
        lines.append(f"Biggest mover: {top[0]['ticker']} {top[1]:+.1f}% this week.")

    # Per-holding P&L% snapshot (native ratio — currency-agnostic).
    for r, pnl_pct, _ in priced:
        wk = _week_move(conn, r["ticker"], r["price"])
        wk_txt = f", week {wk:+.1f}%" if wk is not None else ""
        lines.append(f"  {r['ticker']} {r['name']}: P&L {pnl_pct:+.1f}%{wk_txt}.")

    # Review flags (spec 16) — "worth a look", never buy/sell.
    total_val = val_usd or sum(v for _, _, v in priced if v) or 0
    flags = []
    for r in rows:
        checks = [dict(c) for c in conn.execute(
            "SELECT axis, passed FROM health_checks WHERE ticker=?", (r["ticker"],))]
        dcf_row = conn.execute(
            "SELECT fair_value FROM dcf WHERE ticker=?", (r["ticker"],)).fetchone()
        dcf = ({"fair_value": dcf_row["fair_value"]}
               if dcf_row and dcf_row["fair_value"] else None)
        vv = _to_usd((r["price"] or 0) * r["qty"], r["currency"], rate)
        alloc = (vv / total_val * 100) if (vv and total_val) else None
        sigs = metrics.review_signals(
            {"avg_price": r["avg_price"], "qty": r["qty"]},
            {"price": r["price"]}, checks, dcf, alloc, _earnings_days(r["next_earnings"]))
        for s in sigs:
            flags.append(f"  ⚑ {r['ticker']}: {s['text']}")
    if flags:
        lines.append("Worth a look (not advice — just numbers):")
        lines.extend(flags)

    return "Your portfolio's week:\n" + "\n".join(lines), held


def _watchlist_section(conn, uid, exclude):
    """Section 2 — watched-but-not-held stocks and how they moved this week."""
    rows = conn.execute(
        "SELECT s.ticker, s.name, s.currency, n.price, n.change_pct "
        "FROM user_watchlist w JOIN stocks s ON s.ticker=w.ticker "
        "LEFT JOIN snapshots n ON n.ticker=w.ticker WHERE w.user_id=? "
        "ORDER BY w.added_at", (uid,)).fetchall()
    rows = [r for r in rows if r["ticker"] not in exclude]
    if not rows:
        return "", set()
    watched = {r["ticker"] for r in rows}
    lines = []
    for r in rows:
        wk = _week_move(conn, r["ticker"], r["price"])
        price = f"{r['price']:.2f} {r['currency']}" if r["price"] is not None else "price n/a"
        wk_txt = f"week {wk:+.1f}%" if wk is not None else "week n/a"
        lines.append(f"  {r['ticker']} {r['name']}: {price}, {wk_txt}")
    return "Your watchlist's week:\n" + "\n".join(lines), watched


def _earnings_section(conn, tickers):
    """Section 3 — the user's holdings/watchlist reporting in the next 7 days."""
    if not tickers:
        return ""
    today = date.today().isoformat()
    horizon = date.fromordinal(date.today().toordinal() + 7).isoformat()
    ph = ",".join("?" * len(tickers))
    rows = conn.execute(
        f"SELECT ticker, name, next_earnings FROM stocks "
        f"WHERE ticker IN ({ph}) AND next_earnings IS NOT NULL "
        f"AND next_earnings >= ? AND next_earnings <= ? "
        f"ORDER BY next_earnings, ticker",
        (*tickers, today, horizon)).fetchall()
    if not rows:
        return ""
    lines = []
    for r in rows:
        d = _earnings_days(r["next_earnings"])
        when = "today" if d == 0 else "tomorrow" if d == 1 else f"in {d} days"
        lines.append(f"  {r['ticker']} {r['name']}: reports {when} ({r['next_earnings']}).")
    return "Earnings this week:\n" + "\n".join(lines)


def _movers_section(conn):
    """Section 4 — the week's biggest day movers across the tracked universe.
    Native % (a ratio), so currency-agnostic and the same for everyone."""
    gainers = conn.execute(
        "SELECT s.ticker, s.name, n.change_pct FROM snapshots n "
        "JOIN stocks s ON s.ticker=n.ticker "
        "WHERE n.change_pct IS NOT NULL AND n.change_pct > 0 "
        "ORDER BY n.change_pct DESC LIMIT 3").fetchall()
    losers = conn.execute(
        "SELECT s.ticker, s.name, n.change_pct FROM snapshots n "
        "JOIN stocks s ON s.ticker=n.ticker "
        "WHERE n.change_pct IS NOT NULL AND n.change_pct < 0 "
        "ORDER BY n.change_pct ASC LIMIT 3").fetchall()
    if not gainers and not losers:
        return ""
    lines = []
    for r in gainers:
        lines.append(f"  ▲ {r['ticker']} {r['name']}: {r['change_pct']:+.1f}%")
    for r in losers:
        lines.append(f"  ▼ {r['ticker']} {r['name']}: {r['change_pct']:+.1f}%")
    return "Top movers across tracked stocks:\n" + "\n".join(lines)


def build_note(conn, user):
    """A plain-text weekly note for one user (spec 17), or None when they have
    NOTHING to say about (no holdings AND no watchlist).

    Assembles four layers — portfolio, watchlist, upcoming earnings, market
    movers — then asks the free LLM to weave them into a calm note. On any AI
    failure (no key, quota, network) it falls back to the labelled sections
    themselves, so a real note always goes out."""
    rate = _usdinr(conn)
    uid = user["id"]
    port_text, held = _portfolio_section(conn, uid, rate)
    watch_text, watched = _watchlist_section(conn, uid, held)
    if not held and not watched:
        return None
    earn_text = _earnings_section(conn, held | watched)
    movers_text = _movers_section(conn)

    sections = [t for t in (port_text, watch_text, earn_text, movers_text) if t]
    context = "\n\n".join(sections)

    try:
        return digest.ask(context,
            "Write a calm 8-12 sentence weekly note for an investor, walking "
            "through their portfolio's week, their watchlist, any earnings in the "
            "next week, and the broader movers. Plain text, no markdown. Never "
            "give buy/sell/hold advice — frame flagged holdings as 'worth a "
            "closer look'. Keep it factual and grounded in the numbers given.")
    except Exception:
        return "Your week at a glance:\n\n" + context      # honest fallback


def main():
    init_db()
    sent = 0
    with get_conn() as conn:
        users = conn.execute("SELECT * FROM users WHERE weekly_email=1").fetchall()
        for u in users:
            try:
                note = build_note(conn, u)
            except Exception as e:                        # never let one user abort the run
                print(f"  build_note failed for user {u['id']}: {e}")
                continue
            if not note:
                continue
            ok = mailer.send(u["email"], "Otto's weekly note — InvestRight",
                             note + "\n\nManage: https://investright.us/account"
                             "\nNot investment advice.")
            if ok:
                sent += 1
    stamp = datetime.now().isoformat(timespec="seconds")
    print(f"{stamp}  weekly notes sent {sent}/{len(users)} opted-in"
          + ("" if mailer.enabled() else " (email disabled — nothing sent)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
