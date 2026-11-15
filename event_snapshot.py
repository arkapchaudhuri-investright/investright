"""Event snapshot — the deep-dive's lead widget.

One card at the top of every stock page that re-frames the valuation around
the stock's latest event: a forward P/E comparison with its peers, three
bull/base/bear weightings, and the full range of outcomes against the price.

Where each part comes from (nothing here is invented):

* Forward P/E + analyst targets — Yahoo consensus, saved by the snapshot fetch
  into `forward_est`.
* The range — the SAME cash-flow model as the Fair value card
  (metrics.dcf_fair), re-run at four growth rates across a band of discount
  rates. Arithmetic on reported results, not a forecast.
* The weights — three fixed mixes. The event never changes a dollar figure; it
  only decides which mix Otto leans to.
* The event — hard triggers computed on every view from saved rows (sharp
  one-day move, earnings just reported / about to be, 52-week extreme), plus
  the week's most consequential headline as read nightly by the free LLM
  (digest.py's provider — never Claude). The most recent frames the card; the
  reader can switch to any other live one. The model writes a label and one sentence; it is never asked for,
  and never allowed to supply, a number.

Cron writes (refresh_events, note_reported); the web only reads (load, view).
"""
import hashlib
import json
import re
import time
from datetime import date, datetime, timedelta

import metrics

# Bull / base / bear weights, in percent. The outer two mirror each other so a
# good event and a bad one move the lean by the same amount.
MIXES = (("bull", "Bull-leaning", (40, 45, 15)),
         ("even", "Balanced", (25, 50, 25)),
         ("bear", "Bear-leaning", (15, 45, 40)))
LEAN = {"positive": "bull", "neutral": "even", "negative": "bear"}

# Scenario = the stored DCF's growth rate shifted by this many points a year.
SCENARIOS = (("stress", "Stress", -0.08), ("bear", "Bear", -0.04),
             ("base", "Base", 0.0), ("bull", "Bull", 0.04))
BAND = 0.01          # each bar spans the stored discount rate ± this

MOVE_PCT = 5.0       # a one-day move at least this big is an event
EARN_DAYS = 7        # earnings within / since this many days is an event
NEAR_52W = 0.01      # within 1% of the 52-week high/low
REACT_PCT = 2.0      # post-earnings move beyond this reads as good/bad
NEWS_DAYS = 7        # headlines the AI may read
NEWS_SHOW_DAYS = 10  # an AI-read event older than this is no longer "latest"
# The free tier is metered per minute AND per day, and the same key serves the
# nightly digest and Ask Otto. Production answered 429 after five calls four
# seconds apart, so: stay under five a minute, wait out one 429, and keep the
# nightly total small enough that reader-facing features are never starved.
AI_CAP = 40          # LLM calls per nightly run
AI_PACE = 13.0       # seconds between calls
AI_BACKOFF = 65.0    # one wait after a 429 before giving up for the night

_ADVICE = re.compile(r"\b(buy|sell|hold|should|must|recommend\w*)\b", re.I)


# --- numbers ----------------------------------------------------------------
def scenarios(dcf):
    """Stress/bear/base/bull per-share values from the page's DCF, each as
    lo/mid/hi across the discount band. None when there is no DCF.

    The DCF is linear in its base cash flow, so each scenario is the fair
    value scaled by how the same formula moves under the shifted growth and
    discount rates. That needs only the four published figures — and makes
    Base exactly the Fair value card's number, in whatever currency it is in."""
    if not dcf:
        return None
    fair, g = dcf.get("fair_value"), dcf.get("growth_used")
    d, tg = dcf.get("discount_rate"), dcf.get("terminal_growth")
    if (not fair or fair <= 0 or g is None or d is None or tg is None
            or d - BAND <= tg):
        return None

    def unit(growth, discount):
        return metrics.dcf_fair(1.0, 1.0, growth, discount, tg)

    k = fair / unit(g, d)
    out = []
    for key, label, shift in SCENARIOS:
        gg = g + shift
        out.append({"key": key, "label": label, "growth": gg,
                    "lo": k * unit(gg, d + BAND), "mid": k * unit(gg, d),
                    "hi": k * unit(gg, d - BAND)})
    return out


def weighted(scen):
    """The three MIXES applied to bull/base/bear → [{key,label,weights,lo,mid,hi}]."""
    by = {s["key"]: s for s in scen}
    rows = []
    for key, label, (wb, wm, wr) in MIXES:
        def mix(k):
            return (wb * by["bull"][k] + wm * by["base"][k] + wr * by["bear"][k]) / 100
        rows.append({"key": key, "label": label, "weights": f"{wb}/{wm}/{wr}",
                     "lo": mix("lo"), "mid": mix("mid"), "hi": mix("hi")})
    return rows


def peg(pe, forward_pe):
    """Forward P/E per point of consensus next-year EPS growth, where that
    growth is what the two P/Es imply (trailing ÷ forward − 1). None unless
    earnings are positive and expected to grow."""
    if not pe or not forward_pe or forward_pe <= 0 or pe <= forward_pe:
        return None
    return forward_pe / ((pe / forward_pe - 1) * 100)


# --- the event --------------------------------------------------------------
def _days_word(n):
    return "today" if n == 0 else "tomorrow" if n == 1 else f"in {n} days"


def triggers(snap, next_earnings=None, last_earnings=None, react_pct=None,
             today=None):
    """Every hard, data-backed event that applies to a stock right now, most
    recent first: a sharp move in the latest session, earnings just reported,
    earnings about to land, a 52-week extreme. Pure; [] when none."""
    today = today or date.today()
    snap = snap or {}
    out = []
    chg = snap.get("change_pct")
    if chg is not None and abs(chg) >= MOVE_PCT:
        up = chg > 0
        word = "jump" if up else "drop"
        out.append({"kind": "move", "direction": "positive" if up else "negative",
                    "chip": f"{abs(chg):.1f}% {word}",
                    "lead": f"After a {abs(chg):.1f}% {word} in the latest session",
                    "why": f"The shares moved {chg:+.1f}% in one session — a move that "
                           "size usually means new information."})

    def _d(iso):
        try:
            return date.fromisoformat(iso[:10])
        except (TypeError, ValueError):
            return None

    le = _d(last_earnings)
    if le and 0 < (today - le).days <= EARN_DAYS:
        ago = (today - le).days
        when = "yesterday" if ago == 1 else f"{ago} days ago"
        if react_pct is None:
            direction, why = "neutral", "The company reported results this week."
        else:
            direction = ("positive" if react_pct >= REACT_PCT
                         else "negative" if react_pct <= -REACT_PCT else "neutral")
            why = (f"The shares are {react_pct:+.1f}% since the results — "
                   + ("the market liked them." if direction == "positive"
                      else "the market did not like them." if direction == "negative"
                      else "little changed."))
        out.append({"kind": "earnings_reported", "direction": direction,
                    "chip": f"Earnings {when}",
                    "lead": f"After earnings {when}", "why": why})

    ne = _d(next_earnings)
    if ne and 0 <= (ne - today).days <= EARN_DAYS:
        n = (ne - today).days
        out.append({"kind": "earnings_ahead", "direction": "neutral",
                    "chip": f"Earnings {_days_word(n)}",
                    "lead": f"Ahead of earnings {_days_word(n)}",
                    "why": f"Results land {_days_word(n)} ({ne.strftime('%-d %b')}) and "
                           "will reset these numbers."})

    price, hi, lo = snap.get("price"), snap.get("wk52_high"), snap.get("wk52_low")
    if price and hi and price >= hi * (1 - NEAR_52W):
        out.append({"kind": "high52", "direction": "positive", "chip": "52-week high",
                    "lead": "At a 52-week high",
                    "why": "The shares are at the top of their one-year range."})
    elif price and lo and price <= lo * (1 + NEAR_52W):
        out.append({"kind": "low52", "direction": "negative", "chip": "52-week low",
                    "lead": "At a 52-week low",
                    "why": "The shares are at the bottom of their one-year range."})
    return out


def live_events(snap, next_earnings, ev, react_pct=None, today=None):
    """Every event the widget can be framed around, default first: the hard
    triggers, then the AI-read headline while it is still fresh. When there is
    nothing at all, one honest baseline entry — so this never returns []."""
    today = today or date.today()
    ev = ev or {}
    out = [{**t, "ai": False, "src": None}
           for t in triggers(snap, next_earnings, ev.get("last_earnings"), react_pct, today)]
    if ev.get("label") and ev.get("direction"):
        stamp = (ev.get("src_published") or ev.get("computed_at") or "")[:10]
        try:
            fresh = (today - date.fromisoformat(stamp)).days <= NEWS_SHOW_DAYS
        except ValueError:
            fresh = False
        if fresh:
            label = ev["label"]
            out.append({"kind": "news", "direction": ev["direction"],
                        "chip": label[:1].upper() + label[1:],
                        "lead": f"After {label}", "why": ev.get("why") or "",
                        "ai": True,
                        "src": {"title": ev.get("src_title"), "url": ev.get("src_url"),
                                "publisher": ev.get("src_publisher")}})
    if not out:
        out.append({"kind": "baseline", "direction": "neutral", "ai": False, "src": None,
                    "chip": "No event", "lead": "No single event this week",
                    "why": "No results, sharp move or standout headline in the last week."})
    return out


# --- formatting -------------------------------------------------------------
def _sym(ccy):
    return "₹" if ccy == "INR" else "$" if ccy == "USD" else f"{ccy} "


def _num(v):
    """A figure without its symbol: whole units from 20 up, a decimal or two
    below, and thousands as '19.5K' from 10,000 — a ₹-denominated range has to
    fit the same narrow column a $ one does."""
    a = abs(v)
    if a >= 10000:
        return f"{v / 1000:,.1f}K"
    return f"{v:,.{0 if a >= 20 else 1 if a >= 2 else 2}f}"


def fmt(v, ccy):
    return f"{_sym(ccy)}{_num(v)}"


def fmt_range(lo, hi, ccy):
    """'~$145–175' — or '~$166' when both ends round to the same figure."""
    a, b = _num(lo), _num(hi)
    return f"~{_sym(ccy)}{a}" if a == b else f"~{_sym(ccy)}{a}–{b}"


def _x(v):
    """'~22x', '~10.3x', '~15x' — one decimal under 20, and never a bare '.0'."""
    return f"~{v:.0f}x" if v >= 20 or round(v, 1) == round(v) else f"~{v:.1f}x"


# --- request-time: read saved rows, build the template's view ---------------
def load(conn, ticker, peer_tickers=()):
    """Everything the widget needs from the DB for one stock (read-only)."""
    fwd = conn.execute("SELECT * FROM forward_est WHERE ticker=?", (ticker,)).fetchone()
    ev = conn.execute("SELECT * FROM event_snapshot WHERE ticker=?", (ticker,)).fetchone()
    pe = conn.execute("SELECT pe, price FROM snapshots WHERE ticker=?", (ticker,)).fetchone()
    peers = []
    for p in peer_tickers:
        r = conn.execute(
            "SELECT f.ticker, f.forward_pe, n.pe FROM forward_est f "
            "LEFT JOIN snapshots n ON n.ticker = f.ticker WHERE f.ticker=?", (p,)).fetchone()
        if r and r["forward_pe"] and r["forward_pe"] > 0:
            peers.append(dict(r))
        if len(peers) == 3:
            break
    react = None
    if ev and ev["last_earnings"] and pe and pe["price"]:
        before = conn.execute(
            "SELECT close FROM price_history WHERE ticker=? AND d < ? "
            "ORDER BY d DESC LIMIT 1", (ticker, ev["last_earnings"])).fetchone()
        if before and before["close"]:
            react = (pe["price"] / before["close"] - 1) * 100
    return {"fwd": dict(fwd) if fwd else None, "ev": dict(ev) if ev else None,
            "pe": pe["pe"] if pe else None, "peers": peers, "react_pct": react}


def _bars(rows, price):
    """Place lo–hi bars and the price line on one shared 0–100% scale."""
    lo = min([r["lo"] for r in rows] + [price])
    hi = max([r["hi"] for r in rows] + [price])
    pad = (hi - lo) * 0.04 or 1
    lo, span = lo - pad, (hi - lo) + 2 * pad
    for r in rows:
        r["left"] = round((r["lo"] - lo) / span * 100, 2)
        r["width"] = round(max((r["hi"] - r["lo"]) / span * 100, 1.2), 2)
    return round((price - lo) / span * 100, 2)


def view(ticker, raw, snap, next_earnings, dcf, factor, ccy, selected=None,
         today=None):
    """Template-ready dict for the widget, or None when there is nothing real
    to show (no forward P/E, no cash-flow range, no analyst targets).

    `snap` and `dcf` are the page's own, already in the display currency
    `ccy`; `factor` converts the natively-stored analyst targets to it."""
    snap = snap or {}
    price = snap.get("price")
    fwd = raw.get("fwd") or {}
    fpe = fwd.get("forward_pe") if fwd.get("forward_pe") and fwd["forward_pe"] > 0 else None
    scen = scenarios(dcf) if price else None
    t_lo, t_hi = fwd.get("target_low"), fwd.get("target_high")
    targets = ({"lo": t_lo * factor, "hi": t_hi * factor}
               if price and t_lo and t_hi and t_hi >= t_lo else None)
    if not fpe and not scen and not targets:
        return None

    # The latest event frames the card by default; `selected` (a kind from the
    # chip row) re-frames it around another live one. Only the wording and
    # Otto's lean follow the choice — every figure below is the same either way.
    events = live_events(snap, next_earnings, raw.get("ev"), raw.get("react_pct"), today)
    event = next((e for e in events if e["kind"] == selected), events[0])
    lean = LEAN[event["direction"]]
    out = {"event": event, "fpe": None, "mix": None, "range": None, "stats": [],
           "events": [{"kind": e["kind"], "chip": e["chip"], "ai": e["ai"],
                       "active": e is event, "latest": e is events[0]}
                      for e in events] if len(events) > 1 else []}

    # 01 · forward P/E against peers
    deck = []
    self_peg = peg(raw.get("pe"), fpe) if fpe else None
    if fpe:
        peers = raw.get("peers") or []
        top = max([fpe] + [p["forward_pe"] for p in peers])
        rows = [{"label": ticker, "txt": _x(fpe), "pct": round(fpe / top * 100, 1), "self": True}]
        rows += [{"label": p["ticker"], "txt": _x(p["forward_pe"]),
                  "pct": round(p["forward_pe"] / top * 100, 1), "self": False} for p in peers]
        pegs = [g for g in (peg(p.get("pe"), p["forward_pe"]) for p in peers) if g]
        note = "Next fiscal year's consensus earnings."
        if self_peg:
            note += f" P/E ÷ growth: {ticker} ~{self_peg:.2f}"
            if pegs:
                note += (f", peers ~{min(pegs):.2f}–{max(pegs):.2f}" if len(pegs) > 1
                         else f", {peers[0]['ticker']} ~{pegs[0]:.2f}")
            note += "."
        out["fpe"] = {"rows": rows, "note": note}
        if peers:
            others = [p["forward_pe"] for p in peers]
            rank = ("is the cheapest of its peers" if fpe < min(others)
                    else "is the priciest of its peers" if fpe > max(others)
                    else "sits mid-pack against its peers")
            deck.append(f"On consensus {ticker} {rank}, at {_x(fpe)} next year's earnings.")
        else:
            deck.append(f"On consensus {ticker} trades at {_x(fpe)} next year's earnings.")
        out["stats"].append({"value": _x(fpe), "label": "Forward P/E, consensus"})
        if self_peg:
            out["stats"].append({"value": f"~{self_peg:.2f}",
                                 "label": "P/E per point of EPS growth"})

    # 02 + 03 · the weights and the range
    lean_label = next(label for key, label, _ in MIXES if key == lean)
    if scen:
        mixes = weighted(scen)
        w_lo = min(m["mid"] for m in mixes)
        w_hi = max(m["mid"] for m in mixes)
        d = dcf["discount_rate"]
        band = f"{(d - BAND) * 100:.0f}–{(d + BAND) * 100:.0f}%"
        out["mix"] = {
            "band": band,
            "rows": [{"label": m["label"], "weights": m["weights"],
                      "mid": f"~{fmt(m['mid'], ccy)}",
                      "span": fmt_range(m["lo"], m["hi"], ccy),
                      "lean": m["key"] == lean} for m in mixes]}
        pos = "below" if price < w_lo else "above" if price > w_hi else "inside"
        bars = [{"label": s["label"], "lo": s["lo"], "hi": s["hi"], "kind": s["key"],
                 "txt": fmt_range(s["lo"], s["hi"], ccy)} for s in scen]
        if targets:
            bars.append({"label": "Analyst targets", "kind": "targets", **targets,
                         "txt": fmt_range(targets["lo"], targets["hi"], ccy)})
        bars.append({"label": "Weighted", "lo": w_lo, "hi": w_hi, "kind": "weighted",
                     "txt": fmt_range(w_lo, w_hi, ccy)})
        out["range"] = {"rows": bars, "price_left": _bars(bars, price),
                        "price": fmt(price, ccy), "band": band}
        full = fmt_range(scen[0]["lo"], scen[-1]["hi"], ccy)
        clause = (f"{fmt_range(w_lo, w_hi, ccy)} a share on Otto's scenarios, "
                  "depending on the weights.")
        deck.append(f"Otto's scenarios weight out to {fmt_range(w_lo, w_hi, ccy)} as the mix "
                    f"shifts from bear-leaning to bull-leaning; the price, {fmt(price, ccy)}, "
                    f"sits {pos} that. Outcomes run {full.replace('–', ' to ~' + _sym(ccy))}.")
        out["stats"] += [
            {"value": full, "label": "Full range, stress to bull"},
            {"value": fmt(price, ccy), "label": f"Price: {pos} the weighted range"},
            {"value": fmt_range(w_lo, w_hi, ccy), "accent": True,
             "label": "Weighted, bear- to bull-leaning"}]
        out["weighted"] = fmt_range(w_lo, w_hi, ccy)     # the landing page's
        out["position"] = pos                            # one-line version
        g_lo, g_hi = scen[0]["growth"] * 100, scen[-1]["growth"] * 100
        out["method"] = (f"Scenarios: Otto's cash-flow model with growth from {g_lo:+.0f}% to "
                         f"{g_hi:+.0f}% a year and a discount rate of {band} — arithmetic on "
                         "reported results, not a forecast.")
    elif targets:
        bars = [{"label": "Analyst targets", "kind": "targets", **targets,
                 "txt": fmt_range(targets["lo"], targets["hi"], ccy)}]
        out["range"] = {"rows": bars, "price_left": _bars(bars, price),
                        "price": fmt(price, ccy), "band": None}
        t_txt = fmt_range(targets["lo"], targets["hi"], ccy)
        pos = ("below" if price < targets["lo"] else "above" if price > targets["hi"]
               else "inside")
        clause = f"analyst targets span {t_txt} a share."
        deck.append("Otto can't build a cash-flow range for this one yet, so the range "
                    f"shown is the analysts' own; the price, {fmt(price, ccy)}, sits {pos} it.")
        out["stats"] += [
            {"value": fmt(price, ccy), "label": f"Price: {pos} the target range"},
            {"value": t_txt, "accent": True, "label": "Analyst target range"}]
        out["method"] = ("No cash-flow range: the company's reported cash flows don't "
                         "support one yet.")
    else:
        clause = f"{_x(fpe)} next year's earnings."
        out["method"] = ("No cash-flow range or analyst targets are available for this "
                         "one yet.")

    out["headline"] = f"{event['lead']}: {clause}"
    out["deck"] = " ".join(deck)
    out["lean"] = lean_label if out["mix"] else None
    out["as_of"] = (fwd.get("fetched_at") or snap.get("fetched_at") or "")[:10]
    return out


# --- cron side --------------------------------------------------------------
def note_reported(conn, ticker, iso_date):
    """Remember an earnings date that has now passed (refresh.save_deep calls
    this just before it overwrites stocks.next_earnings with the next one)."""
    conn.execute(
        "INSERT INTO event_snapshot (ticker, last_earnings) VALUES (?, ?) "
        "ON CONFLICT(ticker) DO UPDATE SET last_earnings = excluded.last_earnings",
        (ticker, iso_date))


def recent_headlines(conn, ticker, today=None, limit=8):
    cutoff = ((today or date.today()) - timedelta(days=NEWS_DAYS)).isoformat()
    return [dict(r) for r in conn.execute(
        "SELECT title, publisher, url, published_at FROM news "
        "WHERE ticker=? AND title IS NOT NULL AND published_at >= ? "
        "ORDER BY published_at DESC LIMIT ?", (ticker, cutoff, limit))]


def news_key(items):
    joined = "|".join(sorted(i["title"] for i in items))
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:16]


def build_prompt(name, ticker, items):
    lines = "\n".join(f"{i}. {it['title']} — {it.get('publisher') or 'unknown'}"
                      for i, it in enumerate(items, 1))
    return f"""You read headlines for InvestRight, a stock-research site. Below are \
this week's headlines about {name} ({ticker}), numbered.

Pick the ONE most consequential development AT THE COMPANY ITSELF: its results \
or guidance, a change of chief executive or other top leadership, a merger, \
acquisition or major deal, a major product launch or failure, regulatory or \
legal action against it, or the gain or loss of a major customer or supplier.

These never qualify: listicles and "stocks to buy" pieces, analyst price-target \
or rating chatter, opinion and retrospectives, market-wide commentary, stories \
about the share price itself, and news about another company (a partner, \
customer or rival) that merely mentions this one. Most weeks, for most \
companies, nothing qualifies — then reply with exactly {{"event": null}}.

Reply with ONLY one JSON object — no prose, no markdown fences:
{{"event": "<a noun phrase of at most 6 words that reads naturally after the word \
'After', for example: the CEO's exit / a record quarter / the antitrust ruling>",
 "material": <true only if a long-term owner of the shares would consider this \
material to the value of the business; otherwise false>,
 "direction": "<positive, negative or neutral for the company's business, judged \
only from the headline; neutral when the headline does not make it clear>",
 "why": "<one plain sentence of at most 22 words stating only what the headline \
reports, as a plain statement of fact (never begin with "The headline"). No \
speculation about what it implies. Use no number that is not in \
the headline. No advice, no price predictions.>",
 "source": <the number of the headline it comes from>}}

HEADLINES
{lines}"""


def parse_ai(text, items):
    """Validate the model's reply against the headlines it was shown. Returns
    {'label','direction','why','src'} or None — None for 'nothing qualifies',
    for an event the model does not itself rate material, and for any reply
    that breaks a rule (a number not in the source headline, advice words, a
    source that doesn't exist). Never raises."""
    try:
        blob = text[text.index("{"):text.rindex("}") + 1]
        data = json.loads(blob)
    except (ValueError, TypeError, AttributeError):
        return None
    if not isinstance(data, dict):
        return None
    label, direction, why = data.get("event"), data.get("direction"), data.get("why")
    try:
        src = items[int(data.get("source")) - 1]
        if int(data.get("source")) < 1:
            return None
    except (TypeError, ValueError, IndexError):
        return None
    if not isinstance(label, str) or not isinstance(why, str):
        return None
    if data.get("material") is not True:
        return None                      # the model itself rates it immaterial
    label = re.sub(r"^after\s+", "", label.strip().rstrip(".!"), flags=re.I)
    why = " ".join(why.split())
    # Models like to narrate the source ("The headline reports that …").
    why = re.sub(r"^the\s+headline\s+\w+\s+(that\s+)?", "", why, flags=re.I)
    why = why[:1].upper() + why[1:]
    direction = str(direction).strip().lower()
    if (not label or len(label) > 60 or not why or len(why) > 220
            or direction not in LEAN):
        return None
    if _ADVICE.search(label) or _ADVICE.search(why):
        return None
    title_nums = set(re.findall(r"\d+(?:[.,]\d+)*", src["title"]))
    for num in re.findall(r"\d+(?:[.,]\d+)*", label + " " + why):
        if num not in title_nums:
            return None                  # the model reached for a figure of its own
    if why[-1] not in ".!?":
        why += "."
    return {"label": label, "direction": direction, "why": why, "src": src}


def _save_read(conn, ticker, key, got, model):
    now = datetime.now().isoformat(timespec="seconds")
    src = got["src"] if got else {}
    conn.execute(
        "INSERT INTO event_snapshot (ticker, news_key, label, direction, why, "
        "src_title, src_url, src_publisher, src_published, model, computed_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(ticker) DO UPDATE SET news_key=excluded.news_key, "
        "label=excluded.label, direction=excluded.direction, why=excluded.why, "
        "src_title=excluded.src_title, src_url=excluded.src_url, "
        "src_publisher=excluded.src_publisher, src_published=excluded.src_published, "
        "model=excluded.model, computed_at=excluded.computed_at",
        (ticker, key, got["label"] if got else None, got["direction"] if got else None,
         got["why"] if got else None, src.get("title"), src.get("url"),
         src.get("publisher"), src.get("published_at"), model, now))


def refresh_events(get_conn, tickers, run, cap=AI_CAP, pace=AI_PACE, today=None,
                   sleep=time.sleep, backoff=AI_BACKOFF):
    """Nightly: for each ticker whose week of headlines has changed, ask the LLM (`run(prompt) -> (text, model)`) for the week's
    event and save its validated read. Returns a one-line status for the log.

    Unchanged headlines cost no call. A 429 is waited out once; a second one
    (or three other failures running) ends the pass, leaving earlier reads in
    place for the next night. Never raises."""
    read = kept = quiet = capped = failed = streak = 0
    for ticker in tickers:
        try:
            with get_conn() as conn:
                s = conn.execute("SELECT name, exchange FROM stocks "
                                 "WHERE ticker=?", (ticker,)).fetchone()
                if not s or s["exchange"] == "INDEX":
                    continue
                ev = conn.execute("SELECT * FROM event_snapshot WHERE ticker=?",
                                  (ticker,)).fetchone()
                items = recent_headlines(conn, ticker, today)
            if not items:
                quiet += 1
                continue
            key = news_key(items)
            if ev and ev["news_key"] == key:
                kept += 1
                continue
            if read + failed >= cap:
                capped += 1
                continue
            if read + failed:
                sleep(pace)
            prompt = build_prompt(s["name"], ticker, items)
            try:
                try:
                    text, model = run(prompt)
                except Exception as e:
                    if "429" not in str(e):
                        raise
                    sleep(backoff)       # per-minute limit: wait it out once
                    text, model = run(prompt)
            except Exception as e:
                failed += 1
                streak += 1
                print(f"  event read failed for {ticker}: {e}")
                if streak >= 3 or "429" in str(e):
                    break                # down, or out of quota — stop for tonight
                continue
            streak = 0
            with get_conn() as conn:
                _save_read(conn, ticker, key, parse_ai(text, items), model)
            read += 1
        except Exception as e:           # one bad ticker never aborts the pass
            print(f"  event snapshot failed for {ticker}: {e}")
    return (f"{read} read · {kept} unchanged · {quiet} quiet"
            + (f" · {capped} over cap" if capped else "")
            + (f" · {failed} failed" if failed else ""))
