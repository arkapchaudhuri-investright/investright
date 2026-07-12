"""Pure metric functions for InvestRight — no I/O, unit-testable (DESIGN.md §8.1).

The nightly refresh calls these with data it fetched; results persist to SQLite
and the web page only reads. Tier A implements the *numeric* checks computable
from yfinance's ~4yr statements + current ratios; judgment-y checks (earnings
quality, "top 25% of payers") return `passed=None` ("n/a") rather than guess
(§8.3). Axis score = passed / applicable(non-None); missing data never fails.
"""
import json
import math
import re

# Transparent benchmarks — labelled in the UI, not licensed analyst data (§1).
MARKET_GROWTH = 0.09      # ~ long-run nominal earnings growth of a broad index
MARKET_DIV_YIELD = 1.5    # % ~ broad-market average dividend yield

AXES = [("value", "Value"), ("future", "Future"), ("past", "Past"),
        ("health", "Health"), ("dividend", "Dividend")]

# DCF defaults (§8.4) — single transparent set, overridable per request.
DEFAULT_DISCOUNT = 0.09
DEFAULT_TERMINAL = 0.025
GROWTH_CAP = 0.15         # clamp historical CAGR to ±15%

# Hand-curated peer map (§8.5 Tier C sub-decision): there's no free "similar
# companies" API, so peers are hardcoded for watchlist tickers — not discovered.
PEERS = {
    "AAPL": ["MSFT", "GOOGL", "HPQ", "DELL"],
    "MSFT": ["AAPL", "GOOGL", "ORCL", "CRM"],
    "GOOGL": ["MSFT", "META", "AAPL", "AMZN"],
    "HPQ": ["DELL", "AAPL", "MSFT"],
    "DELL": ["HPQ", "MSFT", "AAPL"],
    "RELIANCE.NS": ["ONGC.NS", "IOC.NS", "BPCL.NS"],
    "ONGC.NS": ["RELIANCE.NS", "IOC.NS", "BPCL.NS"],
    "IOC.NS": ["RELIANCE.NS", "ONGC.NS", "BPCL.NS"],
    "BPCL.NS": ["RELIANCE.NS", "ONGC.NS", "IOC.NS"],
    "TCS.NS": ["INFY.NS", "WIPRO.NS", "HCLTECH.NS"],
    "INFY.NS": ["TCS.NS", "WIPRO.NS", "HCLTECH.NS"],
}


# --- small helpers ---------------------------------------------------------
def _chk(axis, cid, label, passed, detail):
    return {"axis": axis, "check_id": cid, "label": label,
            "passed": passed, "detail": detail}


def _asc(funds):
    """Fundamentals oldest→newest; each is a dict with a fiscal_year key."""
    return sorted(funds, key=lambda f: f["fiscal_year"])


def _series(funds, key):
    """Non-null values for `key`, oldest→newest."""
    return [f[key] for f in _asc(funds) if f.get(key) is not None]


def _cagr(first, last, years):
    if first is None or last is None or first <= 0 or last <= 0 or years <= 0:
        return None
    return (last / first) ** (1 / years) - 1


def _pct(x, digits=1):
    return None if x is None else round(x * 100, digits)


# --- health checks (~21 numeric; §4 / §8.3) --------------------------------
def compute_checks(funds, ratios, dcf):
    """Return the full ordered list of check dicts (incl. n/a rows for honesty)."""
    funds = _asc(funds)
    latest = funds[-1] if funds else {}
    n = len(funds)
    span = n - 1  # year gaps in the series
    r = ratios or {}
    out = []

    # ---- VALUE (6) ----
    up = dcf.get("upside_pct") if dcf else None
    out.append(_chk("value", "below_fair_value", "Trading below our fair-value estimate",
                    None if up is None else up > 0,
                    None if up is None else f"{'%+.0f' % up}% vs our DCF estimate"))
    out.append(_chk("value", "significantly_below", "More than 20% below fair value",
                    None if up is None else up > 20,
                    None if up is None else f"{'%+.0f' % up}% margin of safety"))
    pe, ind_pe = r.get("pe"), r.get("industry_pe")
    out.append(_chk("value", "pe_industry", "P/E below industry",
                    (pe < ind_pe) if (pe and ind_pe) else None,
                    f"P/E {pe:.1f} vs industry {ind_pe:.1f}" if (pe and ind_pe)
                    else "industry P/E not on the free feed"))
    peer_pe = r.get("peer_pe")
    out.append(_chk("value", "pe_peer", "P/E below peer average",
                    (pe < peer_pe) if (pe and peer_pe) else None,
                    f"P/E {pe:.1f} vs peers {peer_pe:.1f}" if (pe and peer_pe)
                    else "peer P/Es land after the next refresh"))
    out.append(_chk("value", "pb_industry", "P/B below industry", None,
                    "industry P/B not on the free feed"))
    out.append(_chk("value", "ps_history", "P/S sane vs its own history", None,
                    "needs multi-year P/S (Tier B)"))

    # ---- FUTURE (5, historical-trend) ----
    rev = _series(funds, "revenue")
    ni = _series(funds, "net_income")
    rev_cagr = _cagr(rev[0], rev[-1], span) if len(rev) >= 2 else None
    ni_cagr = _cagr(ni[0], ni[-1], span) if len(ni) >= 2 else None
    out.append(_chk("future", "rev_trend_up", "Revenue trending up",
                    None if rev_cagr is None else rev_cagr > 0,
                    None if rev_cagr is None else f"{_pct(rev_cagr)}%/yr over {span}yr"))
    out.append(_chk("future", "earnings_trend_up", "Earnings trending up",
                    (ni[-1] > ni[0]) if len(ni) >= 2 else None,
                    f"{_pct(ni_cagr)}%/yr over {span}yr" if ni_cagr is not None
                    else ("latest above earliest" if len(ni) >= 2 else "n/a")))
    out.append(_chk("future", "growth_over_market", "Earnings growth beats the market",
                    None if ni_cagr is None else ni_cagr > MARKET_GROWTH,
                    None if ni_cagr is None
                    else f"{_pct(ni_cagr)}%/yr vs market ~{_pct(MARKET_GROWTH,0)}%"))
    out.append(_chk("future", "growth_over_industry", "Growth beats the industry", None,
                    "industry growth not on the free feed"))
    roe_first = _roe(funds[0]) if n else None
    roe_last = _roe(latest)
    out.append(_chk("future", "roe_trend", "Return on equity improving",
                    (roe_last > roe_first) if (roe_first is not None and roe_last is not None) else None,
                    f"ROE {_pct(roe_last)}% vs {_pct(roe_first)}% {span}yr ago"
                    if (roe_first is not None and roe_last is not None) else "n/a"))

    # ---- PAST (5) ----
    out.append(_chk("past", "earnings_grew_5yr", "Earnings grew over the period",
                    (ni[-1] > ni[0]) if len(ni) >= 2 else None,
                    f"{_money0(ni[0])} → {_money0(ni[-1])}" if len(ni) >= 2 else "n/a"))
    accel = _accelerating(ni, span)
    out.append(_chk("past", "growth_accelerating", "Growth accelerating vs its average",
                    accel[0], accel[1]))
    out.append(_chk("past", "earnings_quality", "High-quality earnings (few one-offs)", None,
                    "one-off detection deferred — needs statement detail"))
    out.append(_chk("past", "revenue_positive", "Revenue higher than five years ago",
                    (rev[-1] > rev[0]) if len(rev) >= 2 else None,
                    f"{_money0(rev[0])} → {_money0(rev[-1])}" if len(rev) >= 2 else "n/a"))
    roe = r.get("roe")
    out.append(_chk("past", "roe_over_20", "Return on equity above 20%",
                    None if roe is None else roe > 0.20,
                    None if roe is None else f"ROE {_pct(roe)}%"))

    # ---- HEALTH (6) ----
    ca, cl = latest.get("current_assets"), latest.get("current_liab")
    tl = latest.get("total_liab")
    out.append(_chk("health", "sta_gt_stl", "Short-term assets cover short-term bills",
                    (ca > cl) if (ca and cl) else None,
                    f"{_money0(ca)} vs {_money0(cl)}" if (ca and cl) else "n/a"))
    ltl = (tl - cl) if (tl is not None and cl is not None) else None
    out.append(_chk("health", "sta_gt_ltl", "Short-term assets cover long-term debt",
                    (ca > ltl) if (ca and ltl is not None) else None,
                    f"{_money0(ca)} vs {_money0(ltl)}" if (ca and ltl is not None) else "n/a"))
    de = r.get("debt_to_equity")  # yfinance reports debt/equity × 100
    out.append(_chk("health", "de_under_40", "Debt is under 40% of equity",
                    None if de is None else de < 40,
                    None if de is None else f"debt/equity {de:.0f}%"))
    de_fall = _de_falling(funds, span)
    out.append(_chk("health", "de_falling", "Debt/equity falling over time",
                    de_fall[0], de_fall[1]))
    ocf, td = latest.get("op_cash_flow"), latest.get("total_debt")
    if td is not None and td <= 0:
        out.append(_chk("health", "debt_covered_ocf", "Debt well covered by cash flow",
                        True, "essentially no debt"))
    else:
        cov = (ocf / td) if (ocf is not None and td) else None
        out.append(_chk("health", "debt_covered_ocf", "Debt well covered by cash flow",
                        None if cov is None else cov > 0.20,
                        None if cov is None else f"operating cash flow covers {_pct(cov,0)}% of debt"))
    ebit, ie = latest.get("ebit"), latest.get("interest_expense")
    if ie is not None and ie <= 0:
        out.append(_chk("health", "interest_cover_3x", "Interest comfortably covered by profit",
                        True, "negligible interest cost"))
    else:
        icov = (ebit / ie) if (ebit is not None and ie) else None
        out.append(_chk("health", "interest_cover_3x", "Interest comfortably covered by profit",
                        None if icov is None else icov > 3,
                        None if icov is None else f"EBIT covers interest {icov:.1f}×"))

    # ---- DIVIDEND (6) — n/a across the board if it doesn't pay one ----
    dy = r.get("div_yield")
    divs = _series(funds, "dividends_paid")
    pays = (dy and dy > 0) or (divs and divs[-1] > 0)
    if not pays:
        for cid, label in (("yield_gt_market", "Yield beats the market"),
                           ("yield_top25", "Yield in the top quartile of payers"),
                           ("payout_under_75", "Payout ratio under 75%"),
                           ("div_stable", "No dividend cut in recent years"),
                           ("div_growing", "Dividend growing over time"),
                           ("div_covered", "Dividend covered by earnings and cash flow")):
            out.append(_chk("dividend", cid, label, None, "pays no dividend"))
    else:
        out.append(_chk("dividend", "yield_gt_market", "Yield beats the market",
                        None if dy is None else dy > MARKET_DIV_YIELD,
                        None if dy is None else f"yield {dy:.2f}% vs market ~{MARKET_DIV_YIELD}%"))
        # Ranked against every payer Otto tracks (watchlist + peers) — the
        # screener universe, from saved snapshots (Phase 4). Small but honest.
        payer_yields = sorted(r.get("payer_yields") or [])
        if dy is not None and len(payer_yields) >= 4:
            cut = payer_yields[max(0, math.ceil(0.75 * len(payer_yields)) - 1)]
            out.append(_chk("dividend", "yield_top25", "Yield in the top quartile of payers",
                            dy >= cut,
                            f"yield {dy:.2f}% vs {cut:.2f}% cut among the "
                            f"{len(payer_yields)} payers Otto tracks"))
        else:
            out.append(_chk("dividend", "yield_top25", "Yield in the top quartile of payers", None,
                            "needs a few more saved payers to rank against"))
        po = r.get("payout_ratio")
        out.append(_chk("dividend", "payout_under_75", "Payout ratio under 75%",
                        None if po is None else po < 0.75,
                        None if po is None else f"payout {_pct(po)}% of earnings"))
        stable = _no_cut(divs)
        out.append(_chk("dividend", "div_stable", "No dividend cut in recent years",
                        stable[0], stable[1]))
        out.append(_chk("dividend", "div_growing", "Dividend growing over time",
                        (divs[-1] > divs[0]) if len(divs) >= 2 else None,
                        f"over {len(divs)}yr on record" if len(divs) >= 2 else "n/a"))
        d_ni = latest.get("net_income")
        d_fcf = latest.get("fcf")
        d_paid = latest.get("dividends_paid")
        covered = (None if not (d_paid and d_ni and d_fcf)
                   else d_paid <= d_ni and d_paid <= d_fcf)
        out.append(_chk("dividend", "div_covered", "Dividend covered by earnings and cash flow",
                        covered,
                        "covered by both earnings and free cash flow" if covered
                        else ("not fully covered" if covered is False else "n/a")))
    return out


def _roe(f):
    ni, eq = f.get("net_income"), f.get("equity")
    return (ni / eq) if (ni is not None and eq and eq > 0) else None


def _accelerating(ni, span):
    if len(ni) < 3:
        return (None, "n/a")
    recent = _cagr(ni[-2], ni[-1], 1)
    avg = _cagr(ni[0], ni[-1], span)
    if recent is None or avg is None:
        return (None, "n/a (loss years)")
    return (recent > avg, f"latest {_pct(recent)}%/yr vs {_pct(avg)}% average")


def _de_falling(funds, span):
    ltd = [f.get("long_term_debt") for f in funds]
    eq = [f.get("equity") for f in funds]
    pairs = [(d / e) for d, e in zip(ltd, eq) if d is not None and e and e > 0]
    if len(pairs) < 2:
        return (None, "n/a")
    return (pairs[-1] < pairs[0], f"long-term debt/equity {_pct(pairs[-1])}% vs {_pct(pairs[0])}%")


def _no_cut(divs):
    if len(divs) < 2:
        return (None, "n/a")
    cut = any(divs[i] < divs[i - 1] * 0.98 for i in range(1, len(divs)))
    return (not cut, f"steady over {len(divs)}yr on record" if not cut
            else "a cut shows in the record")


# --- axis scores + snowflake geometry (§8.3) -------------------------------
def axis_scores(checks):
    scores = {}
    for key, _ in AXES:
        applic = [c for c in checks if c["axis"] == key and c["passed"] is not None]
        scores[key] = (sum(1 for c in applic if c["passed"]) / len(applic)
                       if applic else None)
    return scores


def axis_detail(checks):
    """Per-axis {passed, applicable} counts for the UI summary."""
    out = {}
    for key, _ in AXES:
        applic = [c for c in checks if c["axis"] == key and c["passed"] is not None]
        out[key] = {"passed": sum(1 for c in applic if c["passed"]),
                    "applicable": len(applic)}
    return out


def overall_score(scores):
    vals = [v for v in scores.values() if v is not None]
    return sum(vals) / len(vals) if vals else None


def mood_for(score):
    if score is None:
        return "neutral"
    if score >= 0.6:
        return "happy"
    if score >= 0.4:
        return "neutral"
    return "concerned"


def snowflake(scores, cx=100, cy=100, R=78):
    """Pentagon geometry computed in Python (§8.3); template just draws it."""
    poly, axes = [], []
    for i, (key, label) in enumerate(AXES):
        ang = math.radians(-90 + i * 72)          # top vertex, then clockwise
        s = scores.get(key)
        rr = R * (s if s is not None else 0)
        poly.append(f"{cx + rr * math.cos(ang):.1f},{cy + rr * math.sin(ang):.1f}")
        lx, ly = cx + (R + 18) * math.cos(ang), cy + (R + 14) * math.sin(ang)
        anchor = "middle" if abs(lx - cx) < 6 else ("start" if lx > cx else "end")
        axes.append({"label": label, "x": round(lx, 1), "y": round(ly, 1),
                     "anchor": anchor, "pct": None if s is None else round(s * 100)})
    rings = []
    for frac in (0.25, 0.5, 0.75, 1.0):
        rings.append(" ".join(
            f"{cx + R * frac * math.cos(math.radians(-90 + i * 72)):.1f},"
            f"{cy + R * frac * math.sin(math.radians(-90 + i * 72)):.1f}"
            for i in range(len(AXES))))
    return {"polygon": " ".join(poly), "rings": rings, "axes": axes,
            "cx": cx, "cy": cy, "R": R}


# --- DCF (§8.4) ------------------------------------------------------------
def compute_dcf(funds, price, shares, growth=None, discount=None, terminal=None):
    """2-stage DCF on FCF (fallback: net income). Returns None if too thin.

    Clearly an *estimate from historical trend*, not an analyst forecast (§1).
    """
    funds = _asc(funds)
    discount = DEFAULT_DISCOUNT if discount is None else discount
    terminal = DEFAULT_TERMINAL if terminal is None else terminal
    if discount <= terminal or not shares or shares <= 0 or not price or price <= 0:
        return None

    basis = "free cash flow"
    flows = _series(funds, "fcf")
    if len(flows) < 2:
        flows, basis = _series(funds, "net_income"), "owner earnings (net income)"
    if len(flows) < 2 or flows[-1] <= 0:
        return None

    if growth is None:
        g = _cagr(flows[0], flows[-1], len(flows) - 1)
        growth = 0.0 if g is None else max(-GROWTH_CAP, min(GROWTH_CAP, g))

    cash, pv = flows[-1], 0.0
    for yr in range(1, 6):                          # stage 1: years 1–5 at `growth`
        cash *= (1 + growth)
        pv += cash / (1 + discount) ** yr
    terminal_val = cash * (1 + terminal) / (discount - terminal)   # Gordon tail
    pv += terminal_val / (1 + discount) ** 5
    fair = pv / shares
    upside = (fair - price) / price * 100

    assumptions = {"basis": basis, "base_flow": round(flows[-1]),
                   "years_of_history": len(flows), "shares": shares}
    return {"fair_value": round(fair, 2), "upside_pct": round(upside, 1),
            "growth_used": round(growth, 4), "discount_rate": round(discount, 4),
            "terminal_growth": round(terminal, 4), "basis": basis,
            "assumptions_json": json.dumps(assumptions)}


# --- header takeaway (serif "voice" line, §5) ------------------------------
def _short_name(name):
    short = name.split(",")[0].split(" Inc")[0].split(" Ltd")[0].strip() or name
    if short.isupper() and len(short) > 6:   # Yahoo yells some names (BHARAT PETROLEUM CORP LT)
        short = short.title()
    return short


def takeaway(name, dcf, scores):
    short = _short_name(name)
    if dcf and dcf.get("upside_pct") is not None:
        up = dcf["upside_pct"]
        if up > 15:
            lead = f"{short} looks about {round(up)}% cheap"
        elif up < -15:
            lead = f"{short} looks about {round(-up)}% expensive"
        else:
            lead = f"{short} looks roughly fairly priced"
    else:
        lead = f"Here's how {short} stacks up"
    health, val = scores.get("health"), scores.get("value")
    if health is not None and health < 0.4:
        return lead + ", but the balance sheet needs a closer look."
    if val is not None and val >= 0.6 and dcf and dcf.get("upside_pct", 0) > 0:
        return lead + " — and the value checks agree."
    return lead + "."


# --- past-performance bar charts (Tier B, §8.5) ----------------------------
def sparkline(closes, width=88, height=26, pad=2):
    """Tiny inline-SVG polyline for a watchlist row — last ~N daily closes.
    Returns {points, dir} or None. `dir` colours it up/down/flat vs the first
    close, matching the row's change pill."""
    closes = [c for c in closes if c is not None]
    if len(closes) < 2:
        return None
    lo, hi = min(closes), max(closes)
    span = (hi - lo) or (hi or 1) * 0.01
    n = len(closes)
    xs = lambda i: pad + (width - 2 * pad) * i / (n - 1)
    ys = lambda c: pad + (height - 2 * pad) * (1 - (c - lo) / span)
    pts = " ".join(f"{xs(i):.1f},{ys(c):.1f}" for i, c in enumerate(closes))
    d = "up" if closes[-1] > closes[0] else "down" if closes[-1] < closes[0] else "flat"
    return {"points": pts, "dir": d, "width": width, "height": height}


def trend_chart(points, width=560, height=150, pad=5):
    """Geometry for the deep-dive price trend line (inline SVG, like every
    chart here). `points` = [(label, close)] oldest-first, any cadence —
    daily closes for 1M…Max, 5-minute bars for the live 1D tab.
    Returns polyline + area-fill coordinates and the window's change."""
    points = [(d, c) for d, c in points if c is not None]
    if len(points) < 2:
        return None
    closes = [c for _, c in points]
    lo, hi = min(closes), max(closes)
    span = (hi - lo) or (hi or 1) * 0.01          # flat series still draws
    n = len(points)
    xs = lambda i: pad + (width - 2 * pad) * i / (n - 1)
    ys = lambda c: pad + (height - 2 * pad) * (1 - (c - lo) / span)
    pts = " ".join(f"{xs(i):.1f},{ys(c):.1f}" for i, (_, c) in enumerate(points))
    change = 100.0 * (closes[-1] - closes[0]) / closes[0] if closes[0] else 0.0
    # Per-point coords + label + price, so the client can draw a crosshair that
    # reads out the date and price under the cursor (no chart lib).
    series = [{"x": round(xs(i), 1), "y": round(ys(c), 1), "d": d, "c": c}
              for i, (d, c) in enumerate(points)]

    # X-axis date ticks: four interior points (the legend already labels the
    # ends). Daily labels compress to "Apr '26" (or "20 Apr" inside ~6 months);
    # intraday labels (HH:MM) pass through untouched.
    span_days = None
    try:
        from datetime import date as _date
        d0 = _date.fromisoformat(str(points[0][0])[:10])
        d1 = _date.fromisoformat(str(points[-1][0])[:10])
        span_days = (d1 - d0).days
    except Exception:
        pass

    def _tick_label(raw):
        raw = str(raw)
        if span_days is None:
            return raw            # intraday times etc.
        try:
            from datetime import date as _date
            dd = _date.fromisoformat(raw[:10])
            return dd.strftime("%-d %b") if span_days <= 200 else dd.strftime("%b '%y")
        except Exception:
            return raw

    ticks = []
    for k in range(1, 5):
        i = round((n - 1) * k / 5)
        if 0 < i < n - 1:
            ticks.append({"x": round(xs(i), 1), "label": _tick_label(points[i][0])})

    return {
        "ticks": ticks,
        "width": width, "height": height, "points": pts,
        "area": f"{xs(0):.1f},{height - pad} {pts} {xs(n - 1):.1f},{height - pad}",
        "lo": lo, "hi": hi, "series": series,
        "first": points[0], "last": points[-1],
        "change_pct": round(change, 2),
        "dir": "up" if change >= 0 else "down",
    }


def bar_chart(years, values, benchmark_growth=None, width=280, height=90, pad=14,
              n_projected=0):
    """Geometry for a revenue/earnings/FCF bar chart, computed in Python so the
    template just draws <rect>s (§8.1: no JS chart libs). Bars can dip below a
    zero baseline for loss years. `benchmark_growth`, if given, overlays a
    dashed reference line compounding from the first year at that rate — a
    free stand-in for "vs index" since there's no free per-ticker index series.
    The last `n_projected` bars are flagged proj=True (Tier C future card) and
    a `split_x` divider marks where history ends. Returns None if there isn't
    enough history to plot (<2 points).
    """
    pts = [(y, v) for y, v in zip(years, values) if v is not None]
    if len(pts) < 2:
        return None
    yrs = [y for y, _ in pts]
    vals = [v for _, v in pts]

    bench = None
    if benchmark_growth is not None and vals[0] > 0:
        bench = [vals[0] * (1 + benchmark_growth) ** i for i in range(len(vals))]

    span_vals = vals + (bench or [])
    vmax, vmin = max(span_vals + [0]), min(span_vals + [0])
    span = (vmax - vmin) or abs(vmax) or 1

    def y_of(v):
        return pad + (height - 2 * pad) * (vmax - v) / span

    y0 = y_of(0)
    n = len(pts)
    cell = (width - 2 * pad) / n
    bw = cell * 0.56

    bars = []
    for i, (yr, v) in enumerate(pts):
        cx = pad + i * cell + cell / 2
        top, bottom = sorted((y_of(v), y0))
        bars.append({"year": yr, "value": v, "x": round(cx - bw / 2, 1),
                     "y": round(top, 1), "w": round(bw, 1),
                     "h": round(max(bottom - top, 1), 1), "pos": v >= 0,
                     "proj": bool(n_projected) and i >= n - n_projected})

    bench_points = None
    if bench:
        bench_points = " ".join(
            f"{pad + i * cell + cell / 2:.1f},{y_of(b):.1f}" for i, b in enumerate(bench))

    split_x = None
    if 0 < n_projected < n:
        split_x = round(pad + (n - n_projected) * cell, 1)

    return {"bars": bars, "y0": round(y0, 1), "width": width, "height": height,
            "first_year": yrs[0], "last_year": yrs[-1],
            "first_label": _money0(vals[0]), "last_label": _money0(vals[-1]),
            "bench_points": bench_points, "split_x": split_x}


def performance_charts(funds):
    """Revenue / earnings / FCF chart geometry for the Past-performance card
    (§4.5). Works with any fundamentals series (EDGAR 10yr or yfinance ~4yr) —
    fewer years just means a shorter chart, not a missing one."""
    funds = _asc(funds)
    years = [f["fiscal_year"] for f in funds]
    out = {}
    for key, label in (("revenue", "Revenue"), ("net_income", "Earnings"),
                       ("fcf", "Free cash flow")):
        vals = [f.get(key) for f in funds]
        chart = bar_chart(years, vals,
                          benchmark_growth=MARKET_GROWTH if key != "fcf" else None)
        if chart:
            out[key] = {"label": label, **chart}
    return out


def _money0(v):
    """Compact magnitude for check details — currency-agnostic (T/B/M)."""
    if v is None:
        return "—"
    a = abs(v)
    for div, unit in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if a >= div:
            return f"{v / div:,.1f}{unit}"
    return f"{v:,.0f}"


# --- future-projection card (Tier C, §4.6) ----------------------------------
def future_projection(funds, years_out=3):
    """Revenue/earnings bars extended `years_out` ahead at the capped historical
    CAGR. Arithmetic on the *past* — the template labels it "trend, not analyst
    forecast" (§1). {} when there's too little history (<3 points) to extend."""
    funds = _asc(funds)
    out = {}
    for key, label in (("revenue", "Revenue"), ("net_income", "Earnings")):
        pts = [(f["fiscal_year"], f[key]) for f in funds if f.get(key) is not None]
        if len(pts) < 3:
            continue
        years = [y for y, _ in pts]
        vals = [v for _, v in pts]
        g = _cagr(vals[0], vals[-1], years[-1] - years[0])
        if g is None:
            continue  # loss years — no honest trend to extend
        g = max(-GROWTH_CAP, min(GROWTH_CAP, g))
        proj = [(years[-1] + i, vals[-1] * (1 + g) ** i) for i in range(1, years_out + 1)]
        chart = bar_chart(years + [y for y, _ in proj], vals + [v for _, v in proj],
                          n_projected=years_out)
        if chart:
            out[key] = {"label": label, "growth_pct": _pct(g),
                        "end_year": proj[-1][0], "end_label": _money0(proj[-1][1]),
                        **chart}
    return out


# --- Revenue & Expenses breakdown (income-statement flow) -------------------
def _flow_parts(row):
    """Decompose one income_flow row into conserved flow parts, or None.

    Detailed tree (when Yahoo served an operating-income line that decomposes
    cleanly — the usual case):
      Revenue → Cost of sales + Gross profit
      Gross profit → Operating profit + Operating expenses
      Operating expenses → R&D + SG&A + other operating costs
      Operating profit → Net profit + Tax + Interest & other
    Fallback (missing/unclean operating data — e.g. heavy non-operating income
    pushing net above operating): the original three-way split, with
    "detailed": False.
    """
    if not row:
        return None
    rev, net = row.get("revenue"), row.get("net_income")
    gp, cost = row.get("gross_profit"), row.get("cost_of_rev")
    if not rev or rev <= 0 or net is None:
        return None
    if gp is None and cost is not None:
        gp = rev - cost
    elif cost is None and gp is not None:
        cost = rev - gp
    if gp is None or cost is None:
        return None
    rd, sga = row.get("rd") or 0.0, row.get("sga") or 0.0
    p = {"rev": rev, "cost": cost, "gross": gp, "rd": rd, "sga": sga,
         "net": net, "detailed": False}

    op, tax = row.get("operating_inc"), row.get("tax")
    tol = 0.005 * rev
    if op is not None and 0 < op <= gp + tol:
        opex = gp - op
        int_other = op - (tax or 0.0) - net     # non-op costs; <0 = non-op income
        opex_other = opex - rd - sga
        if int_other >= -tol and opex_other >= -tol and rd + sga <= opex + tol:
            p.update({"detailed": True, "op": op, "opex": max(opex, 0.0),
                      "tax": tax or 0.0, "int_other": max(int_other, 0.0),
                      "opex_other": max(opex_other, 0.0)})
    if not p["detailed"]:
        expenses = gp - net                     # gross profit not kept as earnings
        p.update({"expenses": expenses, "other": expenses - rd - sga})
    return p


def income_flow_view(row, prior=None):
    """Shape a saved income_flow row (money already in the display currency)
    into the Revenue & Expenses widget's data — flow parts, margins, and a
    table of lines as a % of revenue, each with a Y/Y delta when `prior` (the
    comparable period one year earlier, same currency) is given. None when too
    thin to be meaningful."""
    p = _flow_parts(row)
    if not p:
        return None
    pp = _flow_parts(prior) or {}
    rev = p["rev"]

    def pct(v):
        return round(100 * v / rev, 1)

    def yoy(key):
        prev = pp.get(key)
        if not prev:
            return None
        return round(100 * (p.get(key, 0) - prev) / abs(prev), 1)

    def line(label, key, kind):
        return {"label": label, "amount": p[key], "pct": pct(p[key]),
                "kind": kind, "yoy": yoy(key)}

    rows = [line("Revenue", "rev", "revenue"),
            line("Cost of sales", "cost", "cost"),
            line("Gross profit", "gross", "subtotal")]
    if p["detailed"]:
        if p["rd"]:
            rows.append(line("Research & development", "rd", "expense"))
        if p["sga"]:
            rows.append(line("Sales, general & admin", "sga", "expense"))
        if p["opex_other"] >= 0.005 * rev:
            rows.append(line("Other operating costs", "opex_other", "expense"))
        rows.append(line("Operating profit", "op", "subtotal"))
        if p["tax"]:
            rows.append(line("Tax", "tax", "expense"))
        if p["int_other"] >= 0.005 * rev:
            rows.append(line("Interest & other", "int_other", "expense"))
    else:
        if p["rd"]:
            rows.append(line("Research & development", "rd", "expense"))
        if p["sga"]:
            rows.append(line("Sales, general & admin", "sga", "expense"))
        if abs(p["other"]) >= 0.005 * rev:
            rows.append(line("Tax & other", "other", "expense"))
    rows.append(line("Net income", "net", "earnings"))

    period = row.get("period") or row.get("period_label")
    if period and "Q" in str(period) and not str(period).startswith("FY"):
        yr, q = str(period).split("Q")
        period = f"Q{q} {yr}"

    view = {"period": period, "ptype": row.get("ptype"),
            "revenue": rev, "cost": p["cost"], "gross_profit": p["gross"],
            "rd": p["rd"], "sga": p["sga"], "net_income": p["net"],
            "margin_pct": pct(p["net"]), "rows": rows, "detailed": p["detailed"],
            "margins": {"gross": pct(p["gross"]), "net": pct(p["net"])},
            "rev_yoy": yoy("rev")}
    if p["detailed"]:
        view.update({"operating": p["op"], "opex": p["opex"], "tax": p["tax"],
                     "int_other": p["int_other"], "opex_other": p["opex_other"]})
        view["margins"]["operating"] = pct(p["op"])
    else:
        view.update({"expenses": p["expenses"], "other": p["other"]})
    return view


def income_sankey(view, width=620, height=300, nw=13):
    """Pure-Python Sankey geometry for the Revenue & Expenses flow (§ no JS libs).

    A conserved tree, so it lays out cleanly without crossings: Revenue splits
    into Gross profit + Cost of sales; Gross profit into Earnings + Expenses;
    Expenses into R&D + SG&A + Tax & other. Each column is centred vertically,
    which fans the ribbons out for the classic Sankey look. Returns nodes (thin
    bars + label positions) and ribbon paths, plus a fitted viewBox; None when
    `view` is too thin. Money is whatever currency `view` is already in."""
    if not view:
        return None
    rev = view["revenue"]
    if not rev or rev <= 0:
        return None
    ys = height / rev                       # pixels per currency unit
    gap = 30                                # vertical node padding (room for labels)

    # Columns, each a list of (label, value, kind), top→bottom. Drop empty flows.
    def col(*items):
        return [(l, v, k) for (l, v, k) in items if v and v > 0]
    if view.get("detailed"):
        # PepsiCo-style depth: the operating stage + tax/interest leaves.
        cols = [
            col(("Revenue", rev, "revenue")),
            col(("Gross profit", view["gross_profit"], "subtotal"),
                ("Cost of sales", view["cost"], "cost")),
            col(("Operating profit", view["operating"], "subtotal"),
                ("Operating expenses", view["opex"], "expense")),
            col(("Net profit", view["net_income"], "earnings"),
                ("Tax", view["tax"], "expense"),
                ("Interest & other", view["int_other"], "expense"),
                ("R&D", view["rd"], "expense"),
                ("SG&A", view["sga"], "expense"),
                ("Other op. costs", view["opex_other"], "expense")),
        ]
        tree = [("Revenue", "Gross profit"), ("Revenue", "Cost of sales"),
                ("Gross profit", "Operating profit"),
                ("Gross profit", "Operating expenses"),
                ("Operating profit", "Net profit"), ("Operating profit", "Tax"),
                ("Operating profit", "Interest & other"),
                ("Operating expenses", "R&D"), ("Operating expenses", "SG&A"),
                ("Operating expenses", "Other op. costs")]
    else:
        cols = [
            col(("Revenue", rev, "revenue")),
            col(("Gross profit", view["gross_profit"], "subtotal"),
                ("Cost of sales", view["cost"], "cost")),
            col(("Earnings", view["net_income"], "earnings"),
                ("Expenses", view["expenses"], "expense")),
            col(("R&D", view["rd"], "expense"),
                ("SG&A", view["sga"], "expense"),
                ("Tax & other", view["other"], "expense")),
        ]
        tree = [("Revenue", "Gross profit"), ("Revenue", "Cost of sales"),
                ("Gross profit", "Earnings"), ("Gross profit", "Expenses"),
                ("Expenses", "R&D"), ("Expenses", "SG&A"),
                ("Expenses", "Tax & other")]
    col_x = [i * (width - nw) / 3 for i in range(4)]

    nodes = {}
    for ci, column in enumerate(cols):
        stack_h = sum(v * ys for _, v, _ in column) + gap * max(0, len(column) - 1)
        y = (height - stack_h) / 2          # centre the column
        for (label, val, kind) in column:
            h = val * ys
            nodes[label] = {"label": label, "x": col_x[ci], "y": y, "w": nw, "h": h,
                            "value": val, "pct": round(100 * val / rev, 1),
                            "kind": kind, "col": ci, "_out": y}
            y += h + gap

    def ribbon(x1, sy0, sy1, x2, ty0, ty1):
        xm = (x1 + x2) / 2
        return (f"M{x1:.1f},{sy0:.1f} C{xm:.1f},{sy0:.1f} {xm:.1f},{ty0:.1f} {x2:.1f},{ty0:.1f} "
                f"L{x2:.1f},{ty1:.1f} C{xm:.1f},{ty1:.1f} {xm:.1f},{sy1:.1f} {x1:.1f},{sy1:.1f} Z")

    links = []
    def link(src, dst):
        s, t = nodes.get(src), nodes.get(dst)
        if not s or not t:
            return
        sy0 = s["_out"]
        sy1 = sy0 + t["h"]                   # ribbon carries the whole child flow
        s["_out"] = sy1
        # green ribbons for the "kept" side (profit/earnings), warm for costs/expenses
        flow = "in" if t["kind"] in ("subtotal", "earnings") else "out"
        links.append({"d": ribbon(s["x"] + s["w"], sy0, sy1, t["x"], t["y"], t["y"] + t["h"]),
                      "flow": flow})

    for src, dst in tree:
        link(src, dst)

    # Margin / Y-o-Y sub-labels on the key nodes (kept to one short line each).
    margins = view.get("margins") or {}
    subs = {"Revenue": (f"{view['rev_yoy']:+.0f}% Y/Y"
                        if view.get("rev_yoy") is not None else None),
            "Gross profit": (f"{margins['gross']:.0f}% margin"
                             if margins.get("gross") is not None else None),
            "Operating profit": (f"{margins['operating']:.0f}% margin"
                                 if margins.get("operating") is not None else None),
            "Net profit": (f"{margins['net']:.0f}% margin"
                           if margins.get("net") is not None else None),
            "Earnings": (f"{margins['net']:.0f}% margin"
                         if margins.get("net") is not None else None)}
    for n in nodes.values():
        n["sub"] = subs.get(n["label"])

    # Label placement: rightmost column beside the bar, the rest above it.
    for n in nodes.values():
        if n["col"] == 3:
            n["lx"], n["ly"], n["anchor"], n["above"] = n["x"] + nw + 8, n["y"] + n["h"] / 2, "start", False
        else:
            lift = 32 if n["sub"] else 20
            n["lx"], n["ly"], n["anchor"], n["above"] = n["x"], n["y"] - lift, "start", True

    ys_all = [n["y"] for n in nodes.values()] + [n["y"] + n["h"] for n in nodes.values()]
    vb_y = min(ys_all) - 46                  # headroom for up-to-three-line labels
    vb_h = (max(ys_all) + 10) - vb_y
    return {"nodes": list(nodes.values()), "links": links,
            "vb": f"-6 {vb_y:.1f} {width + 116:.0f} {vb_h:.1f}"}


# --- Leadership grid ---------------------------------------------------------
_TIER1 = re.compile(r"\b(chairman|chairperson|chair|chief exec|ceo|founder|"
                    r"managing director|md)\b", re.I)
_TIER2 = re.compile(r"\b(chief|president|cfo|coo|cto|cio|cmo)\b", re.I)


def exec_tiers(execs):
    """Group officers into [top, C-suite, others] by *title* — no data source
    publishes reporting lines, so this is an honest rank, not a fake org tree.
    Order within a tier follows Yahoo's listing order (CEO first)."""
    tiers = [[], [], []]
    for e in execs:
        t = e.get("title") or ""
        if _TIER1.search(t):
            tiers[0].append(e)
        elif _TIER2.search(t):
            tiers[1].append(e)
        else:
            tiers[2].append(e)
    return [t for t in tiers if t]


def initials(name):
    """"Mr. Timothy D. Cook" → "TC" (first + last of the real name parts)."""
    parts = [p for p in re.sub(r"^\s*(mr|ms|mrs|dr|prof|sir)\.?\s+", "", name or "",
                               flags=re.I).split() if p and p[0].isalpha()]
    if not parts:
        return "?"
    return (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper()


# --- dividend card (Tier C, §4.7) -------------------------------------------
def dividend_card(funds, div_yield):
    """Payout history bars, payout-ratio gauge and no-cut streak from cash
    actually paid (§4.7). Charts *total* cash paid, not per-share — statement
    share counts aren't split-adjusted, so per-share history would show phantom
    cuts (e.g. AAPL's 2020 4:1). {'pays': False} for non-payers so the card can
    say so instead of guessing."""
    funds = _asc(funds)
    hist = [(f["fiscal_year"], f["dividends_paid"]) for f in funds
            if f.get("dividends_paid")]
    if not hist and not (div_yield and div_yield > 0):
        return {"pays": False}

    years = [y for y, _ in hist]
    vals = [d for _, d in hist]
    chart = bar_chart(years, vals) if len(vals) >= 2 else None

    latest = funds[-1] if funds else {}
    d, ni = latest.get("dividends_paid"), latest.get("net_income")
    payout = (d / ni) if (d and ni and ni > 0) else None

    divs = vals
    streak = 0
    for i in range(len(divs) - 1, 0, -1):
        if divs[i] >= divs[i - 1] * 0.98:
            streak += 1
        else:
            break
    return {"pays": True, "yield": div_yield, "market_yield": MARKET_DIV_YIELD,
            "chart": chart, "payout_pct": _pct(payout),
            "streak": (streak + 1) if divs else 0,
            "growing": (divs[-1] > divs[0]) if len(divs) >= 2 else None}


# --- "Today" screener (Phase 4, §4 / §7) -------------------------------------
# The score is a transparent blend of things the cron already saved — no AI in
# the ranking itself, and no fetching: refresh.py assembles the inputs from
# SQLite, this ranks them, and /today only reads the persisted result (§8.0).
SCREEN_WEIGHTS = {"fundamentals": 50, "value_gap": 30, "insiders": 10, "dividend": 10}
UPSIDE_CAP = 50           # DCF upside beyond +50% stops adding points — the
                          # trend model is stretching out there; it still earns
                          # its chip, just not ever more score
NET_BUYS_CAP = 3          # 3+ net open-market insider buys in 90d maxes the part


def _passed(checks, cid):
    return next((c["passed"] for c in checks if c.get("check_id") == cid), None)


def screen(candidates):
    """Rank tickers by how *interesting* they look tonight.

    candidates: [{ticker, is_watchlist, checks, upside_pct,
                  insider_buys, insider_sells, div_yield}] — checks as saved in
    health_checks (axis/check_id/passed), insider counts already windowed to
    the last 90 days by the caller (dates are I/O; this stays pure).

    Returns rows best-first, rank filled in: {ticker, is_watchlist, score 0–100,
    rank, components, reasons: [{kind, label}] strongest-first}.
    """
    rows = []
    for c in candidates:
        checks = c.get("checks") or []
        scores = axis_scores(checks)
        overall = overall_score(scores)
        up = c.get("upside_pct")
        net_buys = (c.get("insider_buys") or 0) - (c.get("insider_sells") or 0)

        parts = {
            "fundamentals": SCREEN_WEIGHTS["fundamentals"] * (overall or 0),
            "value_gap": SCREEN_WEIGHTS["value_gap"]
                         * min(max(up or 0, 0), UPSIDE_CAP) / UPSIDE_CAP,
            "insiders": SCREEN_WEIGHTS["insiders"]
                        * min(max(net_buys, 0), NET_BUYS_CAP) / NET_BUYS_CAP,
            "dividend": SCREEN_WEIGHTS["dividend"] * (scores.get("dividend") or 0),
        }
        rows.append({"ticker": c["ticker"],
                     "is_watchlist": bool(c.get("is_watchlist")),
                     "score": round(sum(parts.values()), 1),
                     "components": {k: round(v, 1) for k, v in parts.items()},
                     "reasons": _screen_reasons(scores, up, net_buys, checks)})
    rows.sort(key=lambda r: (-r["score"], r["ticker"]))
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    return rows


def _screen_reasons(scores, up, net_buys, checks):
    """Plain-English chips explaining a rank — greens first, cautions last (§5)."""
    out = []
    if up is not None and up > 15:
        out.append({"kind": "value", "label": f"{round(up)}% below our DCF estimate"})
    if net_buys > 0:
        out.append({"kind": "insider",
                    "label": f"{net_buys} net insider buy{'s' if net_buys != 1 else ''} in 90 days"})
    health = scores.get("health")
    if health is not None and health >= 0.99:
        out.append({"kind": "health", "label": "every health check passed"})
    elif health is not None and health >= 0.8:
        out.append({"kind": "health", "label": "strong balance sheet"})
    past, future = scores.get("past") or 0, scores.get("future") or 0
    if past >= 0.75 and future >= 0.75:
        out.append({"kind": "growth", "label": "growing, with the record to show it"})
    elif future >= 0.75:
        out.append({"kind": "growth", "label": "growth checks mostly pass"})
    if _passed(checks, "div_growing") and _passed(checks, "div_stable"):
        out.append({"kind": "dividend", "label": "dividend grower, no cuts on record"})
    elif (scores.get("dividend") or 0) >= 0.8:
        out.append({"kind": "dividend", "label": "dividend checks mostly pass"})
    if up is not None and up < -15:
        out.append({"kind": "caution", "label": f"{round(-up)}% above our DCF estimate"})
    if health is not None and health < 0.4:
        out.append({"kind": "caution", "label": "balance sheet needs a look"})
    return out


def today_market_read(picks, market):
    """A deterministic 'Otto's read' for the /today market selection, built from
    the (already market-filtered) screener picks — so the read changes the
    moment you switch US / India / Both, with no AI call. The nightly AI digest
    still appears below as the fuller note. Honest: it's our own ranking."""
    if not picks:
        return None
    label = {"US": "US", "IN": "Indian"}.get(market, "tracked")
    top = picks[0]
    name = _short_name(top.get("name") or top["ticker"])
    reasons = top.get("reasons") or []
    lead = f"{name} ({top['ticker']}) tops tonight's {label} screen"
    if reasons and reasons[0].get("label"):
        lead += f" — {reasons[0]['label'][0].lower()}{reasons[0]['label'][1:]}"
    lead += "."
    rest = [p["ticker"] for p in picks[1:4]]
    if rest:
        lead += f" Close behind: {', '.join(rest)}."
    return lead + " That's a rank of our own rules on the tickers you track — not advice."


def today_takeaway(rows):
    """Serif one-liner for the top of /today (§5). rows = screener rows with a
    display name attached, best-first."""
    if not rows:
        return "Nothing to screen yet — add a stock or two and Otto will rank them overnight."
    top = _short_name(rows[0].get("name") or rows[0]["ticker"])
    hot = sum(1 for r in rows if r["score"] >= 55)
    if hot >= 2:
        return f"{hot} of {len(rows)} tickers look interesting tonight — {top} leads the list."
    if hot == 1:
        return f"{top} stands out tonight; the rest of the list is quieter."
    return f"A quiet night — {top} tops the list, but nothing screams."


# ── Graph explainers (Phase 7) ──────────────────────────────────────────────
# Plain-English "what this shows" for each deep-dive graph, plus an Investopedia
# link to go deeper. Rendered by the 💡 bulb under each chart (templates/
# _explain.html). Keyed by the card; keep the body to ~2 sentences, jargon-light.
GRAPH_EXPLAINERS = {
    "snowflake": {
        "title": "The snowflake",
        "body": "Five quick health scores — Value, Future, Past, Health and "
                "Dividend — each 0–100%. A bigger, more even shape means a stock "
                "that scores well across the board; a spiky one is strong on some "
                "axes and weak on others.",
        "url": "https://www.investopedia.com/terms/f/fundamentalanalysis.asp",
    },
    "fair_value": {
        "title": "Fair value",
        "body": "Our estimate of what one share is worth based on the cash the "
                "business is expected to generate (a discounted-cash-flow model), "
                "next to today's price. Below fair value hints undervalued, above "
                "hints expensive — it's an estimate, not a guarantee.",
        "url": "https://www.investopedia.com/terms/d/dcf.asp",
    },
    "health": {
        "title": "Health checks",
        "body": "Pass/fail rules on the company's finances — debt levels, "
                "profitability, cash cover and so on. More greens means a sturdier "
                "balance sheet; an n/a just means we didn't have that data point.",
        "url": "https://www.investopedia.com/terms/r/ratioanalysis.asp",
    },
    "past_performance": {
        "title": "Past performance",
        "body": "How revenue, earnings and free cash flow have grown over the "
                "years. Bars rising left-to-right show a growing business; the "
                "dashed line is a market-average pace for comparison.",
        "url": "https://www.investopedia.com/terms/f/freecashflow.asp",
    },
    "future": {
        "title": "Future",
        "body": "Solid bars are history; dashed bars simply extend the past "
                "growth trend a few years forward — capped so it stays sane. "
                "It's a trend line, not an analyst forecast.",
        "url": "https://www.investopedia.com/terms/c/cagr.asp",
    },
    "dividend": {
        "title": "Dividend",
        "body": "The share of profit paid back to shareholders as cash. Yield is "
                "that cash as a % of the price; the payout gauge shows how much of "
                "earnings is paid out (over ~75% can be hard to sustain).",
        "url": "https://www.investopedia.com/terms/d/dividendyield.asp",
    },
    "competitors": {
        "title": "Competitors",
        "body": "A few peers in the same business, each with its own mini "
                "snowflake, so you can see how this company stacks up rather than "
                "judging it in isolation.",
        "url": "https://www.investopedia.com/terms/c/comparable-company-analysis-cca.asp",
    },
    "ownership": {
        "title": "Insider activity",
        "body": "Buys and sells by the company's own directors and officers, from "
                "their SEC filings. Insiders sell for many reasons, but clusters "
                "of open-market buying can signal confidence.",
        "url": "https://www.investopedia.com/terms/i/insidertrading.asp",
    },
}
