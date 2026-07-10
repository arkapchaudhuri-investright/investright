"""Monthly strategy screen — computes 3–5 rule-based picks per strategy and
market for /strategies ("Otto's current matches").

Cron writes, web reads (§3): the nightly refresh calls run() only when the
latest batch is 30+ days old, so the page shifts roughly monthly. Universe =
the curated tickers in static/tickers.js (~100 US + India large/mid caps) —
NOT the whole market, and the page says so.

Every rule is arithmetic on yfinance data, and every pick carries a why-line
generated from the very numbers that selected it — the calculation is the
explanation. No AI anywhere in here.

Run manually:  .venv/bin/python strategy_screen.py [--limit N]
"""
import json
import re
import sys
import time
from datetime import date

import yfinance as yf

from db import get_conn, init_db

TOP_N = 5      # picks per (strategy, market)
MIN_N = 3      # below this, the page falls back to the hand-written examples
REFRESH_DAYS = 30

_TICKERS_JS = "static/tickers.js"


def universe():
    """[(symbol, name, market)] from the autocomplete's curated list —
    the one place the app already keeps a cross-market ticker universe."""
    src = open(_TICKERS_JS, encoding="utf-8").read()
    rows = re.findall(r'\["([^"]+)","([^"]+)","([^"]+)"\]', src)
    return [(sym, name, "IN" if exch in ("NSE", "BSE") else "US")
            for sym, name, exch in rows]


def _pct(a, b):
    """% change from b to a, or None."""
    if a is None or not b:
        return None
    return 100.0 * (a - b) / b


def measure(symbol):
    """One ticker's rule inputs: fundamentals from Ticker.info, price shape
    from 5y of closes. Every field may be None — rules must tolerate gaps
    (same lesson as metrics.axis_scores: a None axis is data, not an error)."""
    t = yf.Ticker(symbol)
    info = t.info or {}
    if info.get("regularMarketPrice") is None:
        return None
    hist = t.history(period="5y", interval="1d", auto_adjust=True)
    closes = list(hist["Close"].dropna()) if hist is not None and not hist.empty else []
    if len(closes) < 130:            # need ~6 months of trading days
        return None
    px = closes[-1]
    m = {
        "price": px,
        "cap": info.get("marketCap"),
        "pe": info.get("trailingPE"),
        "sector": info.get("sector") or "",
        "industry": info.get("industry") or "",
        "margins": info.get("profitMargins"),          # 0..1
        "roe": info.get("returnOnEquity"),             # 0..1
        "rev_growth": info.get("revenueGrowth"),       # 0..1, y/y
        "eps_q_growth": info.get("earningsQuarterlyGrowth"),  # 0..1, y/y
        "eps_growth": info.get("earningsGrowth"),      # 0..1
        "div_yield": info.get("dividendYield"),        # % on new yf, 0..1 on old — used only in text
        "r3m": _pct(px, closes[-63] if len(closes) >= 63 else None),
        "r6m": _pct(px, closes[-126] if len(closes) >= 126 else None),
        "off_52w": None, "off_ath": None,
        "above_200d": None, "vol20": None, "vol60": None,
    }
    hi52 = max(closes[-252:]) if len(closes) >= 60 else None
    if hi52:
        m["off_52w"] = max(0.0, 100.0 * (hi52 - px) / hi52)
    ath = max(closes)
    m["off_ath"] = max(0.0, 100.0 * (ath - px) / ath)
    if len(closes) >= 200:
        m["above_200d"] = px > sum(closes[-200:]) / 200
    # Volatility contraction (SEPA): recent 20-day daily-move stdev vs the
    # 60 days before it. Tighter recent range = the coil Minervini looks for.
    if len(closes) >= 81:
        rets = [_pct(closes[i], closes[i - 1]) for i in range(1, len(closes))]
        def _sd(xs):
            mu = sum(xs) / len(xs)
            return (sum((x - mu) ** 2 for x in xs) / len(xs)) ** 0.5
        m["vol20"] = _sd(rets[-20:])
        m["vol60"] = _sd(rets[-80:-20])
    return m


# ── formatting helpers for the why-lines ────────────────────────────────────
def _cap_h(cap, market):
    if not cap:
        return "n/a"
    sym = "₹" if market == "IN" else "$"
    for div, unit in ((1e12, "T"), (1e9, "B")):
        if cap >= div:
            return f"{sym}{cap / div:,.1f}{unit}"
    return f"{sym}{cap:,.0f}"


def _p(x, dp=0):
    return "n/a" if x is None else f"{x:+.{dp}f}%" if dp else f"{x:+.0f}%"


_INFRA_RE = re.compile(
    r"aerospace|defense|defence|construction|engineer|electrical|machin|rail|"
    r"infrastructure|power|utilit|conglomerate|building", re.I)


# ── one rule per strategy id (ids match strategies.py) ──────────────────────
# Each returns None (not eligible) or (score, why). The why cites the numbers
# that did the selecting — that's the "how we calculated" the page shows.
def _capex(m, market):
    # Sector gate first ("Consumer Defensive" once matched a looser regex on
    # "defen…"); the industry regex only widens within plausible sectors.
    if m["sector"] not in ("Industrials", "Utilities", "Energy", "Basic Materials"):
        if not _INFRA_RE.search(m["industry"]):
            return None
    if m["r6m"] is None or m["r6m"] <= 0:
        return None
    why = f"{m['sector'] or 'Industrial'} — {m['industry'] or 'infrastructure'}; {_p(m['r6m'])} over 6 months"
    if m["rev_growth"] is not None:
        why += f"; revenue {_p(m['rev_growth'] * 100)} y/y"
    return m["r6m"], why + "."


def _quality(m, market):
    floor = 2.5e12 if market == "IN" else 2e11        # ₹2.5T / $200B ≈ mega-cap
    if not m["cap"] or m["cap"] < floor:
        return None
    marg = (m["margins"] or 0) * 100
    grow = (m["rev_growth"] or 0) * 100
    if marg < 10 or grow <= 0:
        return None
    return (marg + grow,
            f"Mega-cap ({_cap_h(m['cap'], market)}) still compounding: revenue "
            f"{_p(grow)} y/y on {marg:.0f}% profit margins.")


def _momentum(m, market):
    if m["r6m"] is None or m["r6m"] < 15 or m["off_52w"] is None or m["off_52w"] > 15:
        return None
    score = 0.7 * m["r6m"] + 0.3 * (m["r3m"] or 0)
    return (score,
            f"{_p(m['r6m'])} in 6 months ({_p(m['r3m'])} in 3); trading "
            f"{m['off_52w']:.0f}% below its 52-week high.")


def _value(m, market):
    pe_cap = 18 if market == "IN" else 15
    if not m["pe"] or m["pe"] <= 0 or m["pe"] > pe_cap:
        return None
    if m["r6m"] is None or m["r6m"] <= 0:
        return None                                    # cheap AND being re-priced
    return (m["r6m"] / m["pe"] * 10,
            f"Still cheap at {m['pe']:.1f}× earnings, and the market is "
            f"re-rating it: {_p(m['r6m'])} in 6 months.")


def _smartbeta(m, market):
    q = max((m["roe"] or 0), (m["margins"] or 0)) * 100
    if q < 15 or m["r6m"] is None or m["r6m"] <= 0:
        return None
    qlabel = f"ROE {m['roe'] * 100:.0f}%" if m["roe"] else f"margins {q:.0f}%"
    return (q + m["r6m"],
            f"Passes a quality + momentum blend: {qlabel} with {_p(m['r6m'])} "
            f"over 6 months — the factors MTUM/QUAL-style rules buy.")


def _canslim(m, market):
    g = m["eps_q_growth"]
    if g is None or g * 100 < 25 or m["off_52w"] is None or m["off_52w"] > 10:
        return None
    return (g * 100 + (m["r6m"] or 0),
            f"O'Neil's C: quarterly earnings {_p(g * 100)} y/y; price "
            f"{m['off_52w']:.0f}% off the 52-week high — the breakout zone.")


def _sepa(m, market):
    if not m["above_200d"] or m["vol20"] is None or m["vol60"] is None:
        return None
    if m["vol60"] <= 0 or m["vol20"] > 0.75 * m["vol60"] or (m["off_52w"] or 99) > 12:
        return None
    return ((m["vol60"] - m["vol20"]) * 10 + (m["r6m"] or 0),
            f"A live volatility contraction: daily swings tightened to "
            f"±{m['vol20']:.1f}% from ±{m['vol60']:.1f}%, above the 200-day "
            f"trend, {m['off_52w']:.0f}% off the high.")


def _zulu(m, market):
    g = m["eps_growth"] if m["eps_growth"] is not None else m["eps_q_growth"]
    # 15–60% growth only: Slater distrusted spectacular growth numbers — a
    # +900% year is a cyclical recovery, not a compounding rate, and it makes
    # any PEG meaninglessly tiny.
    if g is None or not (15 <= g * 100 <= 60) or not m["pe"] or m["pe"] <= 0:
        return None
    peg = m["pe"] / (g * 100)
    if peg > 1.0:
        return None
    return (1.0 / peg,
            f"Slater's PEG test: {m['pe']:.1f}× earnings ÷ {g * 100:.0f}% "
            f"earnings growth = PEG {peg:.2f} (under 1 = growth going cheap).")


def _darvas(m, market):
    if m["off_ath"] is None or m["off_ath"] > 3 or (m["r3m"] or 0) <= 0:
        return None
    return (-m["off_ath"] + (m["r6m"] or 0) / 10,
            f"Within {m['off_ath']:.1f}% of its highest close in our 5-year "
            f"window — the top of a fresh Darvas box ({_p(m['r3m'])} in 3 months).")


RULES = {
    "capex": _capex, "quality": _quality, "momentum": _momentum,
    "value": _value, "smartbeta": _smartbeta,
    "canslim": _canslim, "sepa": _sepa, "zulu": _zulu, "darvas": _darvas,
}

# Plain-English rule descriptions the page shows next to the picks.
METHODS = {
    "capex": "Screen: infrastructure/defence/utility sectors with a positive 6-month trend, ranked by that trend.",
    "quality": "Screen: mega-caps (≥$200B / ₹2.5T) with 10%+ profit margins and growing revenue, ranked by margins + growth.",
    "momentum": "Screen: +15% or better over 6 months and within 15% of the 52-week high, ranked by blended 3/6-month return.",
    "value": "Screen: under 15× earnings (18× in India) AND rising over 6 months — cheap alone isn't enough, ranked by re-rating speed per unit of P/E.",
    "smartbeta": "Screen: a quality factor (ROE or margins ≥15%) blended with positive 6-month momentum — what rules-based factor funds buy.",
    "canslim": "Screen: quarterly earnings up 25%+ year-on-year with price within 10% of the 52-week high, per O'Neil's C and N.",
    "sepa": "Screen: above the 200-day trend with recent daily volatility at least 25% tighter than before, near the high — Minervini's contraction.",
    "zulu": "Screen: PEG under 1.0 on 15–60% earnings growth, per Slater — spectacular growth is excluded as cyclical recovery, not compounding.",
    "darvas": "Screen: within 3% of the highest close in our 5-year window and rising over 3 months — the top of the box.",
}


def sweep(limit=None):
    """Measure the universe once, apply every rule, return pick rows."""
    uni = universe()
    if limit:
        uni = uni[:limit]
    measured = []
    for i, (sym, name, market) in enumerate(uni):
        try:
            m = measure(sym)
            if m:
                measured.append((sym, name, market, m))
        except Exception as e:      # one bad ticker never aborts the sweep
            print(f"  measure failed for {sym}: {e}")
        time.sleep(0.3)             # be polite to Yahoo
    print(f"measured {len(measured)}/{len(uni)} tickers")

    rows = []
    for strat, rule in RULES.items():
        for market in ("US", "IN"):
            scored = []
            for sym, name, mkt, m in measured:
                if mkt != market:
                    continue
                hit = rule(m, market)
                if hit:
                    scored.append((hit[0], sym, name, hit[1]))
            scored.sort(reverse=True)
            for rank, (_, sym, name, why) in enumerate(scored[:TOP_N], 1):
                rows.append(dict(strategy=strat, market=market, rank=rank,
                                 ticker=sym, name=name, why=why))
    return rows


def run(limit=None):
    """Full monthly job: sweep, then replace the saved batch atomically."""
    batch = date.today().isoformat()
    rows = sweep(limit)
    with get_conn() as conn:
        conn.execute("DELETE FROM strategy_picks")     # latest batch only; git-able history lives in refresh.log
        conn.executemany(
            "INSERT INTO strategy_picks (batch_date, strategy, market, rank, ticker, name, why) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(batch, r["strategy"], r["market"], r["rank"], r["ticker"], r["name"], r["why"])
             for r in rows])
    print(f"saved {len(rows)} picks for batch {batch}")
    return len(rows)


def is_stale(conn):
    """True when there's no batch or the latest is REFRESH_DAYS+ old."""
    row = conn.execute("SELECT MAX(batch_date) AS d FROM strategy_picks").fetchone()
    if not row or not row["d"]:
        return True
    return (date.today() - date.fromisoformat(row["d"])).days >= REFRESH_DAYS


if __name__ == "__main__":
    init_db()
    lim = None
    if "--limit" in sys.argv:
        lim = int(sys.argv[sys.argv.index("--limit") + 1])
    run(lim)
