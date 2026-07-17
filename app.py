"""InvestRight — Phase 1: watchlist + price snapshot table (yfinance only).
Run: .venv/bin/python app.py  →  http://localhost:8700
"""
import hmac
import json
import os
import secrets
import uuid
from datetime import date, datetime, timedelta
from urllib.parse import unquote

from flask import (Flask, abort, flash, g, jsonify, make_response, redirect,
                   render_template, request, session, url_for)

import digest
import fetch
import logos
import metrics
import refresh as refresh_job   # aliased: the /refresh view below owns the name `refresh`
import strategies as strategies_content   # aliased: /strategies view owns the short name
import strategy_screen
from auth import bp as auth_bp, client_ip, current_user, login_required
from db import (LOGIN_MAX_PER_EMAIL, LOGIN_MAX_PER_IP, LOGIN_WINDOW_MIN,
                add_user_peer, add_user_watch, get_conn, get_user_note, init_db,
                log_event, recent_login_failures, remove_user_peer,
                remove_user_watch, save_price_history, save_snapshot,
                save_user_note, user_peers_for, user_watches)

app = Flask(__name__)
# Session signing key from .env (§10.0) — refresh→digest's loader put it in the
# environment at import. Falls back to a dev-only constant if unset so local
# runs still work; production MUST set SECRET_KEY in the VM .env.
app.secret_key = os.environ.get("SECRET_KEY") or "investright-local-only"
# Session cookie hardening (§10.0). Secure defaults ON (prod is HTTPS via Caddy);
# the __main__ dev server flips it off so login works over http://localhost.
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=(os.environ.get("SESSION_COOKIE_SECURE", "1") == "1"),
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)
app.register_blueprint(auth_bp)

# Ensure the SQLite schema exists at import time, not just under the dev server's
# __main__ block — gunicorn imports this module without running __main__, so
# newly-added tables (Phase 6c's `events`, Phase 8's users/user_watchlist/
# user_notes) would otherwise be missing in production until the nightly
# refresh's init_db() ran. Idempotent (CREATE IF NOT EXISTS + additive migrations).
init_db()

# Plain-English graph explainers for the deep-dive 💡 bulbs (Phase 7). Exposed
# to every template so _explain.html's macro can look them up by key.
app.jinja_env.globals["EXPLAIN"] = metrics.GRAPH_EXPLAINERS

# Secret gating the /admin activity log. Loaded from .env (via refresh→digest's
# tiny loader at import). Unset ⇒ /admin is disabled (404), never wide open.
ADMIN_KEY = os.environ.get("ADMIN_KEY")

# Canonical public origin — used to build absolute URLs for social/OG meta tags
# (crawlers need absolute image + page URLs). Overridable via .env for staging.
SITE_URL = (os.environ.get("SITE_URL") or "https://investright.us").rstrip("/")


@app.context_processor
def inject_site_url():
    return {"SITE_URL": SITE_URL}


@app.before_request
def _ensure_visitor():
    """Anonymous per-browser id for the activity log (Phase 6c) — no accounts
    yet, so this cookie UUID is the only stable handle on a visitor."""
    g.vid = request.cookies.get("vid") or uuid.uuid4().hex


@app.after_request
def _persist_visitor(resp):
    if request.cookies.get("vid") != getattr(g, "vid", None):
        resp.set_cookie("vid", g.vid, max_age=365 * 24 * 3600, samesite="Lax")
    return resp


@app.before_request
def _csrf_protect():
    """Reject cross-site POSTs (§10.0). Every POST form carries a hidden `csrf`
    input matching the per-session token; SameSite=Lax is the first line of
    defence, this token is belt-and-braces."""
    if request.method == "POST":
        token = session.get("csrf")
        if not token or not hmac.compare_digest(request.form.get("csrf", ""), token):
            abort(400)


@app.context_processor
def inject_csrf():
    """Per-session CSRF token, minted lazily on the first render and exposed to
    every POST form as `csrf_token`."""
    token = session.get("csrf")
    if not token:
        token = session["csrf"] = secrets.token_urlsafe(32)
    return {"csrf_token": token}


def _log(action, ticker=None):
    """Record one activity event. name/market are self-reported values the
    browser mirrors from localStorage into cookies (see base.html); IP prefers
    Caddy's X-Forwarded-For. Best-effort: never let logging break a request."""
    try:
        ip = client_ip()
        # name/market cookies are percent-encoded client-side (names may hold
        # spaces/unicode); Werkzeug doesn't unquote them, so do it here.
        name = unquote(request.cookies.get("ir_name") or "") or None
        market = unquote(request.cookies.get("ir_market") or "") or None
        user = current_user()
        with get_conn() as conn:
            log_event(
                conn, action, visitor=getattr(g, "vid", None),
                name=name, market=market, ticker=ticker, path=request.path,
                ua=request.headers.get("User-Agent"), ip=ip,
                user_id=user["id"] if user else None)
    except Exception:
        pass


@app.context_processor
def inject_wl_count():
    """Topbar badge: how many stocks the signed-in visitor watches. The list
    itself moved to /watchlist, so every page needs the count, not the rows."""
    try:
        user = current_user()
        if not user:
            return {"wl_count": 0}
        with get_conn() as conn:
            n = conn.execute("SELECT COUNT(*) c FROM user_watchlist WHERE user_id=?",
                             (user["id"],)).fetchone()["c"]
        return {"wl_count": n}
    except Exception:
        return {"wl_count": 0}


@app.context_processor
def inject_fx():
    """Currency seg + today's $→₹ rate belong in the gear on EVERY page — they
    used to render only where the view passed show_fx (home/watchlist), so the
    settings panel looked gutted elsewhere. Views that convert prices still
    call _fx_ctx() themselves; identical values, render kwargs just shadow."""
    try:
        return _fx_ctx()
    except Exception:
        return {"show_fx": False}


@app.after_request
def persist_ccy(resp):
    """?ccy= works from any page now (the gear's links stay on the current
    path), so the cookie must persist globally — same pattern as ?theme=."""
    c = request.args.get("ccy")
    if c in ("USD", "INR"):
        resp.set_cookie("ccy", c, max_age=180 * 24 * 3600, samesite="Lax")
    return resp


@app.context_processor
def inject_theme():
    """Make the chosen theme available to every template (§5 dark/light toggle).
    None ⇒ no explicit choice: base.html omits data-theme so the CSS
    prefers-color-scheme media query decides — no flash, respects the OS."""
    theme = request.args.get("theme") or request.cookies.get("theme")
    return {"theme": theme if theme in ("dark", "light") else None}


@app.after_request
def persist_theme(resp):
    """Mirror the USD/INR cookie pattern: a ?theme= link persists the choice.
    (The header toggle sets the cookie client-side too, for an instant,
    reload-free switch; this covers the no-JS fallback.)"""
    t = request.args.get("theme")
    if t in ("dark", "light"):
        resp.set_cookie("theme", t, max_age=180 * 24 * 3600, samesite="Lax")
    elif t == "system":                  # clear the choice → follow OS (no-JS path)
        resp.delete_cookie("theme")
    return resp


def get_usdinr():
    """(rate, fetched_on) — one Yahoo fetch per day, last-good on failure."""
    today = date.today().isoformat()
    with get_conn() as conn:
        row = conn.execute("SELECT rate, fetched_on FROM fx_rates WHERE pair='USDINR'").fetchone()
    if row and row["fetched_on"] == today:
        return row["rate"], row["fetched_on"]
    rate = fetch.fx_rate("USDINR=X")
    if rate:
        with get_conn() as conn:
            conn.execute("INSERT OR REPLACE INTO fx_rates (pair, rate, fetched_on) "
                         "VALUES ('USDINR', ?, ?)", (rate, today))
        return rate, today
    return (row["rate"], row["fetched_on"]) if row else (None, None)


def convert_row(r, ccy, rate):
    """Re-express a row's money fields in the display currency; ratios untouched."""
    native = r["currency"]
    if not rate or native == ccy or native not in ("USD", "INR"):
        return r
    factor = rate if native == "USD" else 1 / rate
    for k in ("price", "prev_close", "market_cap", "wk52_low", "wk52_high"):
        if r.get(k):
            r[k] = r[k] * factor
    r["currency"] = ccy
    return r


# Money columns on the deep-dive, by table. Ratios (pe/pb/ps/change_pct/
# div_yield/rec_mean), counts (analyst_n), share counts and years are NOT money
# and must never be scaled by the FX factor.
_SNAP_MONEY = ("price", "prev_close", "market_cap", "wk52_low", "wk52_high",
               "eps", "target_mean")
_FUND_MONEY = ("revenue", "net_income", "fcf", "total_assets", "total_liab",
               "current_assets", "current_liab", "long_term_debt", "equity",
               "ebit", "op_cash_flow", "dividends_paid")


def _fx_factor(native, display, rate):
    """(factor, label_ccy): multiply a `native`-currency amount by `factor` to
    express it in `display`. Falls back to (1.0, native) when conversion isn't
    possible (unknown currency, no rate) so callers can always multiply safely."""
    if (native == display or not rate
            or native not in ("USD", "INR") or display not in ("USD", "INR")):
        return 1.0, native
    return (rate if native == "USD" else 1.0 / rate), display


def greeting():
    h = datetime.now().hour
    return "Good morning" if h < 12 else "Good afternoon" if h < 17 else "Good evening"


def _fx_ctx():
    """Shared currency context: chosen display currency + the dated $→₹ rate
    (fresh or stale) for the gear and for ₹ conversion labels (§11.3)."""
    ccy = request.args.get("ccy") or request.cookies.get("ccy") or "USD"
    if ccy not in ("USD", "INR"):
        ccy = "USD"
    fx, fx_on = get_usdinr()
    fx_stale = (datetime.fromisoformat(fx_on).strftime("%-d %b")
                if fx_on and fx_on != date.today().isoformat() else None)
    fx_on_label = datetime.fromisoformat(fx_on).strftime("%-d %b") if fx_on else None
    return dict(ccy=ccy, fx=fx, fx_stale=fx_stale, fx_on_label=fx_on_label, show_fx=True)


@app.route("/")
def home():
    # Clean, Google-calm home: greeting, search, two actions — plus a quiet
    # "popular right now" row of the day's biggest movers across the US + India
    # (top gainers and top losers by day change), each a one-tap shortcut into
    # its deep dive. Reads snapshots the nightly cron already keeps current.
    ctx = _fx_ctx()
    with get_conn() as conn:
        gainers = [dict(r) for r in conn.execute(
            "SELECT s.ticker, s.name, n.change_pct FROM snapshots n "
            "JOIN stocks s ON s.ticker = n.ticker "
            "WHERE n.change_pct IS NOT NULL AND n.change_pct > 0 "
            "ORDER BY n.change_pct DESC LIMIT 3")]
        losers = [dict(r) for r in conn.execute(
            "SELECT s.ticker, s.name, n.change_pct FROM snapshots n "
            "JOIN stocks s ON s.ticker = n.ticker "
            "WHERE n.change_pct IS NOT NULL AND n.change_pct < 0 "
            "ORDER BY n.change_pct ASC LIMIT 3")]
        trending = gainers + losers
    resp = make_response(render_template(
        "home.html", greeting=greeting(), trending=trending, **ctx))
    if request.args.get("ccy"):
        resp.set_cookie("ccy", ctx["ccy"], max_age=180 * 24 * 3600)
    _log("view")
    return resp


@app.route("/watchlist")
def watchlist_page():
    """The watchlist's own page. Logged-in shows the user's list; logged-out
    shows the sign-in CTA (search / analyze / Today stay open to guests)."""
    ctx = _fx_ctx()
    user = current_user()
    # Market filter (US / India / Both), same shape as /today — the toggle is a
    # plain link persisting to the shared ir_market cookie.
    market = (request.args.get("market") or request.cookies.get("ir_market") or "BOTH").upper()
    if market not in ("US", "IN", "BOTH"):
        market = "BOTH"
    rows = []
    if user:
        with get_conn() as conn:
            rows = conn.execute("""
                SELECT s.*, n.fetched_at, n.price, n.prev_close, n.change_pct,
                       n.market_cap, n.pe, n.div_yield, n.wk52_low, n.wk52_high
                FROM user_watchlist w
                JOIN stocks s ON s.ticker = w.ticker
                LEFT JOIN snapshots n ON n.ticker = w.ticker
                WHERE w.user_id = ?
                ORDER BY w.added_at""", (user["id"],)).fetchall()
    total = len(rows)

    def _in_market(tk):
        india = tk.endswith(".NS") or tk.endswith(".BO")
        return True if market == "BOTH" else (india if market == "IN" else not india)
    if market != "BOTH":
        rows = [r for r in rows if _in_market(r["ticker"])]

    rows = [convert_row(dict(r), ctx["ccy"], ctx["fx"]) for r in rows]
    # A 30-session sparkline per row (last-good closes from price_history — the
    # same table the deep-dive trend uses). Read-only, so no cron rule broken.
    if rows:
        with get_conn() as conn:
            for r in rows:
                closes = [x["close"] for x in conn.execute(
                    "SELECT close FROM price_history WHERE ticker=? "
                    "ORDER BY d DESC LIMIT 30", (r["ticker"],))][::-1]
                r["spark"] = metrics.sparkline(closes)
    as_of = max((r["fetched_at"] for r in rows if r["fetched_at"]), default=None)
    if as_of:
        as_of = datetime.fromisoformat(as_of).astimezone().strftime("%-d %b, %-I:%M %p")

    # Guests see three real demo scores (not a bare sign-in wall) — read-only,
    # skips any demo ticker we don't actually have locally (never fake data).
    demo = []
    if not user:
        with get_conn() as conn:
            for tk in ("AAPL", "RELIANCE.NS", "MSFT"):
                s = conn.execute(
                    "SELECT s.*, n.price, n.change_pct FROM stocks s "
                    "LEFT JOIN snapshots n ON n.ticker = s.ticker "
                    "WHERE s.ticker = ?", (tk,)).fetchone()
                if not s:
                    continue
                d = convert_row(dict(s), ctx["ccy"], ctx["fx"])
                checks = [dict(r) for r in conn.execute(
                    "SELECT axis, passed FROM health_checks WHERE ticker=?", (tk,))]
                sc = metrics.axis_scores(checks)
                d["snowflake"] = (metrics.snowflake(sc)
                                  if any(v is not None for v in sc.values()) else None)
                demo.append(d)

    resp = make_response(render_template(
        "watchlist.html", rows=rows, as_of=as_of, market=market, total=total,
        demo=demo, **ctx))
    if request.args.get("ccy"):
        resp.set_cookie("ccy", ctx["ccy"], max_age=180 * 24 * 3600)
    if request.args.get("market"):
        resp.set_cookie("ir_market", market, max_age=60 * 60 * 24 * 365, samesite="Lax")
    _log("view")
    return resp


def _ingest_stock(symbol):
    """Fetch a symbol from Yahoo and persist its stock row + snapshot + peers +
    deep data (checks/DCF/news) — WITHOUT touching the watchlist. Returns the
    meta dict, or None if Yahoo can't find the symbol. Shared by /add (which also
    watchlists it) and /analyze (which just opens the deep-dive)."""
    meta = fetch.lookup(symbol)
    if not meta:
        return None
    try:  # cache the company logo now so the deep-dive shows it immediately
        logos.ensure(meta["ticker"], meta.get("website"), meta.get("name"))
    except Exception:
        pass
    snap = fetch.snapshot(symbol)
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO stocks (ticker,name,exchange,sector,industry,currency,added_at) "
                     "VALUES (?,?,?,?,?,?,?)",
                     (meta["ticker"], meta["name"], meta["exchange"], meta["sector"],
                      meta.get("industry") or "", meta["currency"], now))
        if snap:
            save_snapshot(conn, snap)
        # peers first (stocks row + price only) so save_deep's peer-average P/E
        # check has prices; their deep data lands on the next refresh (Tier C)
        for p in metrics.PEERS.get(meta["ticker"], []):
            try:
                if refresh_job.ensure_stock(conn, p):
                    psnap = fetch.snapshot(p)
                    if psnap:
                        save_snapshot(conn, psnap)
            except Exception:
                pass
        try:  # deep data so the /stock page works immediately, not only post-cron
            refresh_job.save_deep(conn, meta["ticker"])
        except Exception:
            pass
        try:  # full price history so the trend chart works immediately too
            # resilient: BSE (.BO) history is often empty on Yahoo — fall back
            # to the NSE twin so a first-time search always gets a real chart.
            rows = fetch.price_history_resilient(meta["ticker"], "max")
            if rows:
                save_price_history(conn, meta["ticker"], rows)
        except Exception:
            pass
    return meta


_NOT_FOUND = ("Otto couldn't find “{}” on Yahoo — check the symbol? "
              "Indian tickers need .NS or .BO (e.g. RELIANCE.NS).")


@app.post("/add")
@login_required
def add():
    user = current_user()
    raw = request.form.get("symbol", "").strip()
    symbol = raw.upper()
    if not symbol:
        return redirect(url_for("home"))
    with get_conn() as conn:
        if user_watches(conn, user["id"], symbol):
            flash(f"{symbol} is already on your watchlist.", "info")
            return redirect(url_for("watchlist_page"))
    meta = _ingest_stock(symbol)
    if not meta:                         # free-text → nearest symbol (see /analyze)
        resolved = fetch.search(raw)
        meta = _ingest_stock(resolved) if resolved else None
    if not meta:
        flash(_NOT_FOUND.format(raw), "error")
        return redirect(url_for("home"))
    with get_conn() as conn:
        add_user_watch(conn, user["id"], meta["ticker"])   # per-user + global union
        try:  # fold the newcomer into /today's ranking (DB-only, instant)
            refresh_job.run_screener(conn)
        except Exception:
            pass
    _log("add", meta["ticker"])
    flash(f"Added {meta['name']} to your watchlist.", "ok")
    return redirect(url_for("watchlist_page"))


@app.post("/analyze")
def analyze():
    """The search bar's primary action: open a ticker's deep-dive WITHOUT adding
    it to the watchlist. Fetches + persists the first time we see a symbol (the
    /stock page itself stays DB-only reads, §3), then redirects. Add-to-watchlist
    is a separate button — in the search bar and the ☆ on the deep-dive header."""
    raw = request.form.get("symbol", "").strip()
    symbol = raw.upper()
    if not symbol:
        return redirect(url_for("home"))
    with get_conn() as conn:
        known = conn.execute("SELECT 1 FROM stocks WHERE ticker=?", (symbol,)).fetchone()
    if not known:                        # only hit Yahoo the first time
        meta = _ingest_stock(symbol)
        if not meta:
            # Not a literal ticker — resolve the free text (company name, typo,
            # "Indian railways") to the nearest symbol via Yahoo search.
            resolved = fetch.search(raw)
            meta = _ingest_stock(resolved) if resolved else None
            if not meta:
                flash(_NOT_FOUND.format(raw), "error")
                return redirect(url_for("home"))
        symbol = meta["ticker"]
    _log("analyze", symbol)
    return redirect(url_for("stock", ticker=symbol))


@app.post("/remove")
@login_required
def remove():
    user = current_user()
    ticker = request.form.get("ticker", "")
    _log("remove", ticker)
    # Only drop the ticker from THIS user's list. Shared reference data (stocks /
    # snapshots) and the global union stay — other users, peers, and /today may
    # still need them, and the nightly refresh keeps covering the union.
    with get_conn() as conn:
        remove_user_watch(conn, user["id"], ticker)
    return redirect(url_for("watchlist_page"))


@app.post("/refresh")
def refresh():
    # List pages refresh PRICES, fast: snapshot_many is threaded (~seconds for
    # dozens of tickers), then re-rank. Deep data (checks/DCF/news/EDGAR) is
    # heavy — minutes for a full list — changes ~quarterly, and has its own
    # nightly cron + the per-stock ↻ on the deep dive. Keeping it out of the
    # list-page button is what makes that button feel instant.
    with get_conn() as conn:
        symbols = [r["ticker"] for r in conn.execute("SELECT ticker FROM watchlist")]
        peers = [p for p in refresh_job.peer_symbols(symbols)
                 if refresh_job.ensure_stock(conn, p)]
    snaps = fetch.snapshot_many(symbols + peers)
    with get_conn() as conn:
        for snap in snaps:
            save_snapshot(conn, snap)
        try:  # re-rank /today with the fresh prices; digest stays nightly (cron)
            refresh_job.run_screener(conn)
        except Exception:
            pass
    if symbols and not snaps:
        flash("Yahoo isn't answering right now — showing your last saved prices.", "error")
    elif snaps:
        flash(f"Refreshed the latest prices for {len(snaps)} stocks.", "ok")
    # Return to whichever page fired the refresh (watchlist / today). Only
    # same-site relative paths — never an off-site redirect.
    nxt = request.form.get("next", "")
    if nxt.startswith("/") and not nxt.startswith("//"):
        return redirect(nxt)
    return redirect(url_for("watchlist_page"))


@app.post("/strategies/refresh")
def refresh_strategies():
    """Pull the latest prices for the stocks shown on /strategies (the current
    market's rule-based picks) — fast, snapshot-only like the other list pages.
    The monthly *membership* re-sweeps every 30 days by design (it measures
    ~100 tickers with 5y history — far too slow for a click)."""
    market = "IN" if request.args.get("market") == "IN" else "US"
    with get_conn() as conn:
        tickers = [r["ticker"] for r in conn.execute(
            "SELECT DISTINCT ticker FROM strategy_picks WHERE market=?", (market,))]
        tickers = [t for t in tickers if refresh_job.ensure_stock(conn, t)]
    snaps = fetch.snapshot_many(tickers) if tickers else []
    with get_conn() as conn:
        for snap in snaps:
            save_snapshot(conn, snap)
    if tickers and not snaps:
        flash("Yahoo isn't answering right now — showing the last saved data.", "error")
    elif snaps:
        flash(f"Refreshed the latest prices for {len(snaps)} stocks on this page.", "ok")
    return redirect(url_for("strategies_page", market=market))


@app.post("/stock/<ticker>/refresh")
def refresh_stock(ticker):
    """Refresh just this ticker — re-fetch its snapshot + deep data on demand,
    then back to the deep dive. The one user-triggered write on this page (§3)."""
    ticker = ticker.upper()
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM stocks WHERE ticker=?", (ticker,)).fetchone():
            abort(404)
    snap = fetch.snapshot(ticker)
    with get_conn() as conn:
        if snap:
            save_snapshot(conn, snap)
        try:
            refresh_job.save_deep(conn, ticker)     # checks / DCF / news / history
        except Exception:
            pass
    if not snap:
        flash("Yahoo isn't answering right now — showing the last saved data.", "error")
    else:
        flash(f"Refreshed {ticker}.", "ok")
    return redirect(url_for("stock", ticker=ticker))


@app.route("/team")
def team():
    """Meet Our Team (§4-style static page). No DB — content lives in the template."""
    return render_template("team.html")


@app.route("/strategies")
def strategies_page():
    """Models & Strategies — a hand-curated field guide (strategies.py) on how
    the leading playbooks behaved recently, US vs India. Editorial content, not
    screener output; every stock chip drops into the normal deep-dive flow.
    Market comes from ?market= (the on-page toggle) else the ir_market cookie."""
    market = request.args.get("market") or request.cookies.get("ir_market") or "US"
    market = "IN" if market == "IN" else "US"
    with get_conn() as conn:
        known = {r["ticker"] for r in conn.execute("SELECT ticker FROM stocks")}
        # Otto's current matches — the latest monthly rule-based batch
        # (strategy_screen.py). {(strategy, market): [row…]}; empty until the
        # first sweep runs, and the template then keeps the hand-picked lists.
        picks, picks_date = {}, None
        for r in conn.execute(
                "SELECT batch_date, strategy, market, rank, ticker, name, why "
                "FROM strategy_picks WHERE market=? ORDER BY strategy, rank", (market,)):
            picks_date = r["batch_date"]
            picks.setdefault(r["strategy"], []).append(dict(r))
    # Founder portraits are optional uploads (static/founders/<img>.png) — the
    # template falls back to a monogram medallion for any that are missing.
    founders_dir = os.path.join(app.static_folder, "founders")
    portraits = set(os.listdir(founders_dir)) if os.path.isdir(founders_dir) else set()
    _log("view")
    return render_template(
        "strategies.html", market=market, known=known, portraits=portraits,
        picks=picks, picks_date=picks_date, methods=strategy_screen.METHODS,
        strategies=strategies_content.STRATEGIES, frameworks=strategies_content.FRAMEWORKS,
        icons=strategies_content.ICONS, bottom_line=strategies_content.BOTTOM_LINE[market])


@app.post("/strategies/ask")
def ask_strategies():
    """Ask-Otto for the strategies page — same degrade-friendly shape as the
    per-stock route, grounded in the page's own editorial content."""
    question = (request.form.get("question") or "").strip()[:500]
    if not question:
        return jsonify(error="Ask Otto about a strategy first."), 400
    market = "IN" if request.args.get("market") == "IN" else "US"
    _log("ask")
    with get_conn() as conn:
        pick_rows = [dict(r) for r in conn.execute(
            "SELECT strategy, rank, ticker, name, why FROM strategy_picks "
            "WHERE market=? ORDER BY strategy, rank", (market,))]
    try:
        answer = digest.ask(strategies_content.ask_context(market, pick_rows), question)
        return jsonify(answer=answer)
    except Exception:
        return jsonify(answer="Otto can't reach his brain right now — the free AI "
                       "service is unset or busy. The strategy notes on this page "
                       "still tell the story. (Not investment advice.)"), 200


@app.route("/admin")
def admin():
    """Secret-gated activity log (Phase 6c). Visit /admin?key=<ADMIN_KEY>.
    Honest limits: pre-accounts there's no verified identity — `visitor` is an
    anonymous cookie UUID, and name/market are self-reported. Disabled entirely
    (404) unless ADMIN_KEY is set in .env, and the key never appears in a link
    on the site, so it stays out of logs/history unless you type it."""
    key = request.args.get("key", "")
    if not ADMIN_KEY or not hmac.compare_digest(key, ADMIN_KEY):
        abort(404)                       # don't reveal the page exists
    with get_conn() as conn:
        events = [dict(r) for r in conn.execute(
            "SELECT e.*, u.email AS account FROM events e "
            "LEFT JOIN users u ON u.id = e.user_id "
            "ORDER BY e.id DESC LIMIT 500")]
        total = conn.execute("SELECT COUNT(*) c FROM events").fetchone()["c"]
        visitors = conn.execute(
            "SELECT COUNT(DISTINCT visitor) c FROM events").fetchone()["c"]
        top = [dict(r) for r in conn.execute(
            "SELECT ticker, COUNT(*) n FROM events "
            "WHERE ticker IS NOT NULL AND ticker != '' "
            "GROUP BY ticker ORDER BY n DESC LIMIT 10")]
        failures = recent_login_failures(conn)
    # UTC timestamps → the server's local clock for readability.
    for e in events + failures:
        try:
            e["ts_local"] = (datetime.fromisoformat(e["ts"])
                             .astimezone().strftime("%-d %b %H:%M"))
        except Exception:
            e["ts_local"] = e["ts"]
    return render_template("admin.html", events=events, total=total,
                           visitors=visitors, top=top, key=key,
                           failures=failures, login_window=LOGIN_WINDOW_MIN,
                           max_email=LOGIN_MAX_PER_EMAIL, max_ip=LOGIN_MAX_PER_IP)


@app.route("/today")
def today():
    """The Finimize layer (§4 "Today") — screener ranking + nightly AI digest.
    Reads DB only: cron computed everything here overnight (§3, §8.0)."""
    # Filter the screen to the visitor's chosen market (Phase 7). Market is a
    # per-browser preference mirrored into the ir_market cookie; India tickers
    # carry a .NS/.BO suffix. Ranks are renumbered after filtering so the top of
    # the shown list still gets the #1 hero treatment. A ?market= querystring
    # (the visible US/India/Both toggle) wins over the cookie and persists it,
    # same pattern as /strategies and the scope toggle below — plain links, no JS.
    market = (request.args.get("market") or request.cookies.get("ir_market") or "BOTH").upper()
    if market not in ("US", "IN", "BOTH"):
        market = "BOTH"

    # Scope: the whole tracked universe, or only this account's watchlist. The
    # querystring wins over the cookie so the toggle is a plain link (no JS),
    # and a logged-out visitor always collapses back to "all" — ?scope=mine
    # means nothing without an account to own the list.
    user = current_user()
    scope = (request.args.get("scope")
             or request.cookies.get("ir_today_scope") or "all").lower()
    if scope != "mine" or not user:
        scope = "all"

    def _in_market(tk):
        india = tk.endswith(".NS") or tk.endswith(".BO")
        return True if market == "BOTH" else (india if market == "IN" else not india)

    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute("""
            SELECT sc.*, s.name, s.currency, s.exchange, n.price, n.change_pct
            FROM screener sc
            JOIN stocks s ON s.ticker = sc.ticker
            LEFT JOIN snapshots n ON n.ticker = sc.ticker
            ORDER BY sc.rank""")]
        # Membership comes from user_watchlist, not screener.is_watchlist — that
        # column tracks the *global* union and only refreshes overnight, so a
        # ticker starred today would otherwise miss its own screen.
        mine = {r["ticker"] for r in conn.execute(
            "SELECT ticker FROM user_watchlist WHERE user_id=?", (user["id"],))} \
            if user else set()

        picks = [p for p in rows if _in_market(p["ticker"])]
        if scope == "mine":
            picks = [p for p in picks if p["ticker"] in mine]
        for i, p in enumerate(picks, 1):
            p["rank"] = i
        for p in picks:
            # `screener.is_watchlist` is the *global* union — it says the ticker is
            # tracked by someone, not by you. The ★ means yours.
            p["mine"] = p["ticker"] in mine
            p["reasons"] = json.loads(p["reasons_json"] or "[]")
            p["components"] = json.loads(p["components_json"] or "{}")
            checks = [{"axis": c["axis"],
                       "passed": None if c["passed"] is None else bool(c["passed"])}
                      for c in conn.execute(
                          "SELECT axis, passed FROM health_checks WHERE ticker=?",
                          (p["ticker"],))]
            scores = metrics.axis_scores(checks) if checks else None
            p["snowflake"] = (metrics.snowflake(scores, cx=30, cy=30, R=25)
                              if scores else None)
        # ?note=YYYY-MM-DD browses a specific past night; garbage/unknown falls
        # back to the latest. read-only — no writes on this GET.
        want = request.args.get("note")
        drow = None
        if want:
            drow = conn.execute("SELECT * FROM digest WHERE digest_date=?",
                                (want,)).fetchone()
        if drow is None:
            drow = conn.execute(
                "SELECT * FROM digest ORDER BY digest_date DESC LIMIT 1").fetchone()
        older = newer = None
        if drow:
            older = conn.execute(
                "SELECT digest_date FROM digest WHERE digest_date < ? "
                "ORDER BY digest_date DESC LIMIT 1", (drow["digest_date"],)).fetchone()
            newer = conn.execute(
                "SELECT digest_date FROM digest WHERE digest_date > ? "
                "ORDER BY digest_date ASC LIMIT 1", (drow["digest_date"],)).fetchone()

    dig = dict(drow) if drow else None
    older = older["digest_date"] if older else None
    newer = newer["digest_date"] if newer else None
    if dig:
        d = date.fromisoformat(dig["digest_date"])
        dig["date_label"] = d.strftime("%-d %b")
        # cron writes at night, so "yesterday" is current; older means the API
        # has been failing and we're showing last-good (§8.0) — say so.
        dig["stale"] = (date.today() - d).days > 1

    # From the unfiltered rows: when a filter empties the list, the screener
    # still ran, and saying so beats implying the cron never fired.
    as_of = None
    if rows and rows[0]["computed_at"]:
        as_of = (datetime.fromisoformat(rows[0]["computed_at"])
                 .astimezone().strftime("%-d %b, %-I:%M %p"))

    top_score = picks[0]["score"] if picks else None
    mood = ("sleepy" if not picks
            else "happy" if top_score >= 55 else "neutral")
    # Otto's read for the chosen market — deterministic from the filtered picks,
    # so it changes with the US/India/Both toggle (the AI digest below covers
    # the whole screen and is dated).
    market_read = metrics.today_market_read(picks, market)
    resp = make_response(render_template(
        "today.html", picks=picks, digest=dig, as_of=as_of, mood=mood,
        market_read=market_read, note_older=older, note_newer=newer,
        takeaway=metrics.today_takeaway(picks), market=market,
        date_label=date.today().strftime("%A, %-d %B"),
        weights=metrics.SCREEN_WEIGHTS, upside_cap=metrics.UPSIDE_CAP,
        scope=scope, mine_count=len(mine), screened=len(rows)))
    if request.args.get("scope"):
        resp.set_cookie("ir_today_scope", scope, max_age=60 * 60 * 24 * 365,
                        samesite="Lax")
    if request.args.get("market"):        # toggle persists like the gear's market
        resp.set_cookie("ir_market", market, max_age=60 * 60 * 24 * 365,
                        samesite="Lax")
    return resp


def _float_arg(name):
    """A percent querystring value (e.g. ?growth=8) → fraction 0.08, or None."""
    raw = request.args.get(name)
    if raw in (None, ""):
        return None
    try:
        return float(raw) / 100
    except ValueError:
        return None


@app.route("/stock/<ticker>")
def stock(ticker):
    """Deep-dive — reads DB only (§8.1). Yahoo can be down and this still renders."""
    ticker = ticker.upper()
    user = current_user()
    with get_conn() as conn:
        s = conn.execute("SELECT * FROM stocks WHERE ticker=?", (ticker,)).fetchone()
        if not s or s["exchange"] == "INDEX":       # indices are benchmark data, not deep-dives
            abort(404)
        snap = conn.execute("SELECT * FROM snapshots WHERE ticker=?", (ticker,)).fetchone()
        funds = [dict(r) for r in conn.execute(
            "SELECT * FROM fundamentals WHERE ticker=? ORDER BY fiscal_year", (ticker,))]
        checks = [dict(r) for r in conn.execute(
            "SELECT * FROM health_checks WHERE ticker=?", (ticker,))]
        dcf_row = conn.execute("SELECT * FROM dcf WHERE ticker=?", (ticker,)).fetchone()
        news = [dict(r) for r in conn.execute(
            "SELECT * FROM news WHERE ticker=? ORDER BY published_at DESC LIMIT 8", (ticker,))]
        note = get_user_note(conn, user["id"], ticker) if user else None
        on_watch = bool(user) and conn.execute(
            "SELECT 1 FROM user_watchlist WHERE user_id=? AND ticker=?",
            (user["id"], ticker)).fetchone() is not None

        # Competitors strip (§4.9): curated map ∪ community-added ∪ same-industry
        # fill, each tagged with its source (user-added ones are deletable).
        curated = list(metrics.PEERS.get(ticker, []))
        cand = [(p, "curated", None) for p in curated]
        for r in user_peers_for(conn, ticker):
            if r["peer"] not in curated and r["peer"] != ticker:
                cand.append((r["peer"], "user", r["added_by"]))
        listed = {p for p, _, _ in cand} | {ticker}
        if s["industry"]:   # companies we already track in the same industry
            ph = ",".join("?" * len(listed))
            for r in conn.execute(
                    f"SELECT ticker FROM stocks WHERE industry=? AND exchange != 'INDEX' "
                    f"AND ticker NOT IN ({ph}) "
                    "ORDER BY ticker LIMIT 4", (s["industry"], *listed)):
                cand.append((r["ticker"], "industry", None))
        peers = []
        for p, src, added_by in cand:
            prow = conn.execute(
                "SELECT s.ticker, s.name, s.currency, n.price, n.change_pct "
                "FROM stocks s LEFT JOIN snapshots n ON n.ticker = s.ticker "
                "WHERE s.ticker=?", (p,)).fetchone()
            if not prow:
                peers.append({"ticker": p, "missing": True, "src": src, "added_by": added_by})
                continue
            pchecks = [{"axis": r["axis"],
                        "passed": None if r["passed"] is None else bool(r["passed"])}
                       for r in conn.execute(
                           "SELECT axis, passed FROM health_checks WHERE ticker=?", (p,))]
            pscores = metrics.axis_scores(pchecks) if pchecks else None
            peers.append({**dict(prow), "missing": False, "src": src, "added_by": added_by,
                          "snowflake": metrics.snowflake(pscores, cx=30, cy=30, R=25)
                          if pscores else None})

        insiders = [dict(r) for r in conn.execute(
            "SELECT * FROM insider_tx WHERE ticker=? ORDER BY filed_at DESC LIMIT 10",
            (ticker,))]

        # Trend widget: saved daily closes, windowed by ?range= (1D is a live
        # JSON fetch client-side — see /stock/<t>/spark.json).
        hist = conn.execute(
            "SELECT d, close FROM price_history WHERE ticker=? ORDER BY d",
            (ticker,)).fetchall()

        # Income breakdowns (every saved period) for the Revenue & Expenses
        # widget — annual years + recent quarters, newest first.
        flow_rows = [dict(r) for r in conn.execute(
            "SELECT * FROM income_flow WHERE ticker=? ORDER BY end_date DESC",
            (ticker,))]

        # Leadership grid (Yahoo officers + nightly Wikidata enrichment).
        execs = [dict(r) for r in conn.execute(
            "SELECT * FROM executives WHERE ticker=? ORDER BY rank", (ticker,))]

        # Sentiment widget: this site's own reader signals.
        watchers = conn.execute(
            "SELECT COUNT(*) c FROM user_watchlist WHERE ticker=?",
            (ticker,)).fetchone()["c"]
        views30 = conn.execute(
            "SELECT COUNT(*) c FROM events WHERE ticker=? AND "
            "action IN ('view','analyze','ask') AND ts >= datetime('now','-30 days')",
            (ticker,)).fetchone()["c"]

    # First view of a brand-new / peer-added stock: rather than telling the
    # visitor to come back after the nightly refresh, pull what's missing live
    # and render it now. In-memory only — GET routes never write the DB; the
    # ingest / cron paths persist these on their next pass.
    if not snap:
        snap = fetch.snapshot(ticker)
    if not hist:
        hist = [{"d": d, "close": c}
                for d, c in fetch.price_history_resilient(ticker, "max")]

    # SQLite stores passed as 1/0/NULL → back to bool/None for the template.
    for c in checks:
        c["passed"] = None if c["passed"] is None else bool(c["passed"])

    # --- Display currency (gear $/₹ toggle) -------------------------------
    # Re-express EVERY money figure on the page in the reader's chosen currency,
    # from the stock's native reporting currency. Ratios / % / share counts are
    # left alone. Done at the source (snap, funds, price, peers, insiders, trend)
    # so the charts/DCF/projection built below inherit it automatically; the
    # trend.json / spark.json endpoints apply the same factor for range tabs.
    display_ccy = request.args.get("ccy") or request.cookies.get("ccy") or "USD"
    if display_ccy not in ("USD", "INR"):
        display_ccy = "USD"
    rate, _ = get_usdinr()
    factor, ccy = _fx_factor(s["currency"], display_ccy, rate)
    snap = dict(snap) if snap else None
    if snap:
        for k in _SNAP_MONEY:
            if snap.get(k) is not None:
                snap[k] *= factor
    for f in funds:
        for k in _FUND_MONEY:
            if f.get(k) is not None:
                f[k] *= factor
    hist = [(r["d"], r["close"] * factor) for r in hist]   # trend closes → display ccy
    for p in peers:                                        # each peer may be a different native ccy
        if not p.get("missing") and p.get("price") is not None:
            pf, pccy = _fx_factor(p.get("currency"), display_ccy, rate)
            p["price"] *= pf
            p["currency"] = pccy
    ins_factor, _ = _fx_factor("USD", display_ccy, rate)   # EDGAR insider values are USD
    for i in insiders:
        if i.get("value") is not None:
            i["value"] *= ins_factor
    for fr in flow_rows:                                   # income breakdowns → display ccy
        for k in ("revenue", "cost_of_rev", "gross_profit", "rd", "sga",
                  "operating_inc", "net_income", "tax"):
            if fr.get(k) is not None:
                fr[k] *= factor
    for e in execs:                                        # exec pay → display ccy
        if e.get("pay") is not None:
            e["pay"] *= factor
        e["initials"] = metrics.initials(e["name"])

    # Fair value: cron's stored row, unless the reader overrode assumptions in the URL.
    g, d, tg = _float_arg("growth"), _float_arg("discount"), _float_arg("terminal")
    overridden = any(x is not None for x in (g, d, tg))
    dcf = dict(dcf_row) if dcf_row else None
    dcf_is_native = dcf is not None      # stored cron row is in the native currency
    price = snap["price"] if snap else None
    if funds and (overridden or dcf is None):
        shares = next((f["shares"] for f in reversed(funds) if f.get("shares")), None)
        base = dcf or {}
        recomputed = metrics.compute_dcf(
            funds, price, shares,
            growth=g if g is not None else (base.get("growth_used")),
            discount=d if d is not None else (base.get("discount_rate")),
            terminal=tg if tg is not None else (base.get("terminal_growth")))
        if recomputed:
            dcf = recomputed
            dcf_is_native = False        # built from already-converted funds + price
    # A stored DCF is per-share money in the native currency → scale to display.
    # upside_pct is a ratio (fair/price), so it's currency-invariant either way.
    if dcf and dcf_is_native and factor != 1.0:
        dcf["fair_value"] *= factor
    if dcf and dcf.get("assumptions_json") and "basis" not in dcf:
        dcf["basis"] = json.loads(dcf["assumptions_json"]).get("basis")

    scores = metrics.axis_scores(checks)
    snowflake = metrics.snowflake(scores)
    overall = metrics.overall_score(scores)
    charts = metrics.performance_charts(funds)
    # Revenue & Expenses widget: pick the period (?flow=FY2025 / ?flow=2026Q1;
    # default = latest annual), find the comparable one year earlier for Y/Y.
    flow_map = {r["period"]: r for r in flow_rows}
    annual_periods = [r["period"] for r in flow_rows if r["ptype"] == "A"]
    quarter_periods = [r["period"] for r in flow_rows if r["ptype"] == "Q"]
    cur_flow = flow_map.get(request.args.get("flow"))
    if cur_flow is None and flow_rows:
        cur_flow = (flow_map[annual_periods[0]] if annual_periods
                    else flow_rows[0])
    prior_flow = None
    if cur_flow:
        try:
            end = date.fromisoformat(cur_flow["end_date"])
            best = None
            for r in flow_rows:
                if r["ptype"] != cur_flow["ptype"] or r["period"] == cur_flow["period"]:
                    continue
                off = abs((end - date.fromisoformat(r["end_date"])).days - 365)
                if off <= 60 and (best is None or off < best[0]):
                    best = (off, r)
            prior_flow = best[1] if best else None
        except (ValueError, TypeError):
            pass
    income = metrics.income_flow_view(cur_flow, prior_flow)
    income_sankey = metrics.income_sankey(income)
    # Data view is a multi-period matrix (one column per period of the current
    # cadence), so it doesn't need the single-period dropdown. Built only when
    # the reader is on Data, to keep the common Chart path lean.
    flow_matrix = None
    # Chart | Data are exclusive views (SWS-style) — ?flowview=data shows the
    # table, default shows the Sankey. No sankey geometry ⇒ table regardless.
    flow_view = "data" if (request.args.get("flowview") == "data"
                           or not income_sankey) else "chart"
    if flow_view == "data" and cur_flow:
        same_cadence = [r for r in flow_rows if r["ptype"] == cur_flow["ptype"]]
        flow_matrix = metrics.income_flow_matrix(same_cadence)
    fund_source = funds[-1]["source"] if funds else None
    axis_detail = metrics.axis_detail(checks)
    checks_by_axis = {key: [c for c in checks if c["axis"] == key]
                      for key, _ in metrics.AXES}
    # Top 6 for the collapsed view: surface failures first, then passes (§4.4).
    applicable = [c for c in checks if c["passed"] is not None]
    top6 = sorted(applicable, key=lambda c: 0 if c["passed"] is False else 1)[:6]

    as_of = None
    if snap and snap["fetched_at"]:
        as_of = datetime.fromisoformat(snap["fetched_at"]).astimezone().strftime("%-d %b, %-I:%M %p")

    # Trend window (trading days ≈ 21/month). "max" = every close we have.
    rng = request.args.get("range", "1y")
    spans = {"1m": 21, "6m": 126, "1y": 252, "5y": 1260, "max": None}
    if rng not in spans:
        rng = "1y"
    sel = hist if spans[rng] is None else hist[-spans[rng]:]   # hist already (date, close-in-display-ccy) tuples
    # Benchmark overlay (spec 07): S&P 500 for US, NIFTY 50 for India. Raw index
    # closes over the same window — trend_chart normalises them to %, so no
    # currency conversion needed. Missing history ⇒ no overlay, page unchanged.
    bench_sym = "^NSEI" if ticker.endswith((".NS", ".BO")) else "^GSPC"
    bench_name = "NIFTY 50" if bench_sym == "^NSEI" else "S&P 500"
    bench_rows = []
    if sel:
        with get_conn() as conn:
            bench_rows = [(r["d"], r["close"]) for r in conn.execute(
                "SELECT d, close FROM price_history WHERE ticker=? AND d >= ? ORDER BY d",
                (bench_sym, str(sel[0][0])[:10]))]
    trend = metrics.trend_chart(list(sel), bench=bench_rows or None)
    rng_label = {"1m": "the last month", "6m": "six months", "1y": "one year",
                 "5y": "five years", "max": "all saved history"}[rng]

    # Sentiment: Yahoo's analyst consensus (nightly snapshot) + our readers.
    sd = dict(snap) if snap else {}
    senti = {
        "rec_key": (sd.get("rec_key") or "").replace("_", " "),
        "rec_mean": sd.get("rec_mean"),
        "analyst_n": sd.get("analyst_n"),
        "target_mean": sd.get("target_mean"),
        "target_pct": (round(100 * (sd["target_mean"] / price - 1), 1)
                       if sd.get("target_mean") and price else None),
        "gauge_pct": (round(100 * (sd["rec_mean"] - 1) / 4, 1)
                      if sd.get("rec_mean") else None),
        "watchers": watchers, "views30": views30,
    }

    _log("view", ticker)
    return render_template(
        "stock.html", s=dict(s), snap=dict(snap) if snap else None,
        logo=logos.find(ticker),
        ccy=ccy, native_ccy=s["currency"], dcf=dcf, price=price, overridden=overridden,
        checks_by_axis=checks_by_axis, axis_names=dict(metrics.AXES),
        axis_detail=axis_detail, top6=top6, applicable_n=len(applicable),
        scores=scores, snowflake=snowflake, overall=overall,
        mood=metrics.mood_for(overall),
        takeaway=metrics.takeaway(s["name"], dcf, scores, senti),
        charts=charts, fund_source=fund_source, market_growth_pct=round(metrics.MARKET_GROWTH * 100),
        income=income, income_sankey=income_sankey, flow_view=flow_view,
        flow_matrix=flow_matrix,
        flow_annual=annual_periods, flow_quarters=quarter_periods,
        flow_current=cur_flow["period"] if cur_flow else None,
        exec_tiers=metrics.exec_tiers(execs),
        projection=metrics.future_projection(funds),
        dividend=metrics.dividend_card(funds, snap["div_yield"] if snap else None),
        peers=peers, insiders=insiders,
        ins_buys=sum(1 for i in insiders if i["action"] == "buy"),
        ins_sells=sum(1 for i in insiders if i["action"] == "sell"),
        is_us="." not in ticker,
        trend=trend, rng=rng, rng_label=rng_label, bench_name=bench_name, senti=senti,
        news=news, note=dict(note) if note else None, on_watch=on_watch, as_of=as_of)


@app.route("/stock/<ticker>/spark.json")
def spark_json(ticker):
    """Live intraday closes for the 1D trend tab. The one live fetch on the
    deep-dive — read-only (no DB write on a GET, §3), and any failure returns
    a friendly error instead of a 500."""
    ticker = ticker.upper()
    with get_conn() as conn:
        s = conn.execute("SELECT currency FROM stocks WHERE ticker=?", (ticker,)).fetchone()
        if not s:
            abort(404)
    points = fetch.intraday(ticker)
    if len(points) < 2:
        return jsonify(error="No intraday prices right now — the market may be closed.")
    # Convert to the display currency so the 1D tab reads out the same units as
    # the rest of the page (matches the main route + trend.json).
    ccy = request.args.get("ccy") or request.cookies.get("ccy") or "USD"
    if ccy not in ("USD", "INR"):
        ccy = "USD"
    factor, _ = _fx_factor(s["currency"], ccy, get_usdinr()[0])
    chart = metrics.trend_chart([(t, c * factor) for t, c in points])
    return jsonify(points=chart["points"], area=chart["area"],
                   width=chart["width"], height=chart["height"],
                   dir=chart["dir"], change_pct=chart["change_pct"],
                   first=chart["first"], last=chart["last"],
                   series=chart["series"], ticks=chart["ticks"])


@app.route("/stock/<ticker>/trend.json")
def trend_json(ticker):
    """Saved daily closes for a range (1M…Max), as JSON — lets the range tabs
    redraw client-side instead of navigating (which used to scroll-jump to the
    #trend anchor). Read-only GET (§3). Money values are converted to the
    display currency so the crosshair reads out the same units as the page."""
    ticker = ticker.upper()
    rng = request.args.get("range", "1y")
    spans = {"1m": 21, "6m": 126, "1y": 252, "5y": 1260, "max": None}
    if rng not in spans:
        rng = "1y"
    ccy = request.args.get("ccy") or request.cookies.get("ccy") or "USD"
    if ccy not in ("USD", "INR"):
        ccy = "USD"
    with get_conn() as conn:
        s = conn.execute("SELECT currency FROM stocks WHERE ticker=?", (ticker,)).fetchone()
        if not s:
            abort(404)
        hist = conn.execute(
            "SELECT d, close FROM price_history WHERE ticker=? ORDER BY d",
            (ticker,)).fetchall()
    sel = hist if spans[rng] is None else hist[-spans[rng]:]
    factor, _ = _fx_factor(s["currency"], ccy, get_usdinr()[0])
    bench_sym = "^NSEI" if ticker.endswith((".NS", ".BO")) else "^GSPC"
    bench_rows = []
    if sel:
        with get_conn() as conn:
            bench_rows = [(r["d"], r["close"]) for r in conn.execute(
                "SELECT d, close FROM price_history WHERE ticker=? AND d >= ? ORDER BY d",
                (bench_sym, str(sel[0]["d"])[:10]))]
    chart = metrics.trend_chart([(r["d"], r["close"] * factor) for r in sel],
                                bench=bench_rows or None)
    if not chart:
        return jsonify(error="No saved history for this range yet.")
    return jsonify(points=chart["points"], area=chart["area"],
                   width=chart["width"], height=chart["height"],
                   dir=chart["dir"], change_pct=chart["change_pct"],
                   first=chart["first"], last=chart["last"],
                   series=chart["series"], ticks=chart["ticks"], ccy=ccy,
                   bench_points=chart["bench_points"],
                   bench_change_pct=chart["bench_change_pct"])


@app.route("/notes.csv")
@login_required
def notes_csv():
    """Download all of the signed-in user's notes as CSV. A GET that only reads
    (§8.1 allows read GETs) — no DB writes here."""
    import csv
    import io
    user = current_user()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["ticker", "company", "updated_at", "note"])
    with get_conn() as conn:
        for r in conn.execute(
                "SELECT n.ticker, s.name, n.updated_at, n.body FROM user_notes n "
                "JOIN stocks s ON s.ticker=n.ticker WHERE n.user_id=? AND n.body != '' "
                "ORDER BY n.updated_at DESC", (user["id"],)):
            w.writerow([r["ticker"], r["name"], r["updated_at"], r["body"]])
    resp = make_response(out.getvalue())
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    resp.headers["Content-Disposition"] = "attachment; filename=investright-notes.csv"
    return resp


@app.post("/stock/<ticker>/note")
@login_required
def save_stock_note(ticker):
    user = current_user()
    ticker = ticker.upper()
    with get_conn() as conn:
        if conn.execute("SELECT 1 FROM stocks WHERE ticker=?", (ticker,)).fetchone():
            save_user_note(conn, user["id"], ticker, request.form.get("body", "").strip())
    flash("Saved your note.", "ok")
    return redirect(url_for("stock", ticker=ticker))


def _stock_context(conn, ticker):
    """A compact text digest of everything we've saved on `ticker`, fed to Otto
    (the free LLM) as grounding for a user's question. DB-only, no fetching."""
    s = conn.execute("SELECT * FROM stocks WHERE ticker=?", (ticker,)).fetchone()
    if not s:
        return None
    snap = conn.execute("SELECT * FROM snapshots WHERE ticker=?", (ticker,)).fetchone()
    checks = [dict(r) for r in conn.execute(
        "SELECT axis, label, passed, detail FROM health_checks WHERE ticker=?", (ticker,))]
    for c in checks:
        c["passed"] = None if c["passed"] is None else bool(c["passed"])
    dcf = conn.execute("SELECT * FROM dcf WHERE ticker=?", (ticker,)).fetchone()
    funds = [dict(r) for r in conn.execute(
        "SELECT * FROM fundamentals WHERE ticker=? ORDER BY fiscal_year", (ticker,))]
    news = [r["title"] for r in conn.execute(
        "SELECT title FROM news WHERE ticker=? ORDER BY published_at DESC LIMIT 4", (ticker,))]
    ins = conn.execute(
        "SELECT SUM(action='buy') buys, SUM(action='sell') sells FROM insider_tx "
        "WHERE ticker=? AND filed_at >= date('now','-90 day')", (ticker,)).fetchone()
    execs = [dict(r) for r in conn.execute(
        "SELECT name, title FROM executives WHERE ticker=? ORDER BY rank LIMIT 10", (ticker,))]

    cur = s["currency"]
    scores = metrics.axis_scores(checks)
    sector_bits = " · ".join(x for x in (s["sector"], s["industry"]) if x)
    lines = [f"Company: {s['name']} ({ticker}) on {s['exchange']}"
             + (f", {sector_bits}" if sector_bits else "") + f". Prices in {cur}."]
    if execs:
        # Leadership so Otto can answer "who is the CEO?" etc. from real data.
        lines.append("Leadership / key people (from Yahoo, by seniority): " + "; ".join(
            f"{e['name']} — {e['title']}" if e["title"] else e["name"] for e in execs))
    if snap:
        lines.append(
            f"Price {snap['price']}, change today {snap['change_pct']}%. "
            f"P/E {snap['pe']}, P/B {snap['pb']}, P/S {snap['ps']}, EPS {snap['eps']}, "
            f"dividend yield {snap['div_yield']}%, market cap {snap['market_cap']}, "
            f"52-week range {snap['wk52_low']}–{snap['wk52_high']}.")
    if scores:
        axis_label = dict(metrics.AXES)
        # An axis whose checks are all n/a scores None — a company paying no
        # dividend has no dividend checks to pass or fail. Drop those rather
        # than multiply None, the same guard metrics.snowflake() already makes.
        rated = {k: v for k, v in scores.items() if v is not None}
        if rated:
            lines.append("Snowflake axis scores (share of checks passed): " + ", ".join(
                f"{axis_label.get(k, k)} {round(v * 100)}%" for k, v in rated.items()))
    if dcf:
        d = dict(dcf)
        lines.append(
            f"Our DCF fair value {round(d['fair_value'], 2) if d['fair_value'] else 'n/a'} "
            f"{cur} (estimate from historical growth, not an analyst forecast); "
            f"upside vs price {d['upside_pct']}%. Assumptions: growth "
            f"{d['growth_used']}, discount {d['discount_rate']}, terminal {d['terminal_growth']}.")
    passed = [c for c in checks if c["passed"] is True]
    failed = [c for c in checks if c["passed"] is False]
    if passed:
        lines.append("Checks PASSED: " + "; ".join(
            f"{c['label']} ({c['detail']})" if c["detail"] else c["label"] for c in passed))
    if failed:
        lines.append("Checks FAILED: " + "; ".join(
            f"{c['label']} ({c['detail']})" if c["detail"] else c["label"] for c in failed))
    if funds:
        recent = funds[-5:]
        lines.append("Recent fundamentals (fiscal year: revenue / net income / free cash flow): "
                     + "; ".join(f"{f['fiscal_year']}: {f.get('revenue')} / "
                                 f"{f.get('net_income')} / {f.get('fcf')}" for f in recent))
    if ins and (ins["buys"] or ins["sells"]):
        lines.append(f"Insider trades last 90 days: {ins['buys'] or 0} buys, "
                     f"{ins['sells'] or 0} sells.")
    if news:
        lines.append("Recent headlines: " + " | ".join(news))
    return "\n".join(lines)


@app.post("/stock/<ticker>/ask")
def ask_otto(ticker):
    """Ask-Otto chatbot (Phase 9): answer a question about this stock via the free
    LLM, grounded in our saved metrics. Open to guests (no login). Returns JSON;
    any failure (no key, quota, network) degrades to a friendly message, never 500."""
    ticker = ticker.upper()
    question = (request.form.get("question") or "").strip()[:500]
    if not question:
        return jsonify(error="Ask Otto something about this stock first."), 400
    with get_conn() as conn:
        context = _stock_context(conn, ticker)
    if context is None:
        abort(404)
    _log("ask", ticker)
    try:
        answer = digest.ask(context, question)
        return jsonify(answer=answer)
    except Exception:
        return jsonify(answer="Otto can't reach his brain right now — the free AI "
                       "service is unset or busy. The numbers on this page still "
                       "tell the story. (Not investment advice.)"), 200


@app.post("/stock/<ticker>/watch")
@login_required
def toggle_watch(ticker):
    """Add/remove from the current user's watchlist without leaving the deep-dive."""
    user = current_user()
    ticker = ticker.upper()
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM stocks WHERE ticker=?", (ticker,)).fetchone():
            abort(404)
        if user_watches(conn, user["id"], ticker):
            remove_user_watch(conn, user["id"], ticker)
        else:
            add_user_watch(conn, user["id"], ticker)   # per-user + global union
            try:
                refresh_job.run_screener(conn)
            except Exception:
                pass
    return redirect(url_for("stock", ticker=ticker))


@app.post("/stock/<ticker>/peers/add")
@login_required
def add_peer(ticker):
    """Community peers: any signed-in user can add a competitor. Visible to
    everyone (badged "user added"); unknown symbols are ingested first, so the
    chip lands with real data — never a "wait for the next refresh"."""
    ticker = ticker.upper()
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM stocks WHERE ticker=?", (ticker,)).fetchone():
            abort(404)
        known = {r["ticker"] for r in conn.execute("SELECT ticker FROM stocks")}
    symbol = (request.form.get("peer") or "").strip().upper()
    anchor = redirect(url_for("stock", ticker=ticker) + "#peers")
    if not symbol or symbol == ticker:
        flash("Add a different company's name or ticker as a competitor.", "error")
        return anchor
    if symbol not in known:
        meta = _ingest_stock(symbol)         # validates against Yahoo + saves data
        if not meta:
            flash(f"Couldn't find “{symbol}” on Yahoo — check the ticker.", "error")
            return anchor
        symbol = meta["ticker"]
    if symbol == ticker or symbol in metrics.PEERS.get(ticker, []):
        flash(f"{symbol} is already listed for {ticker}.", "info")
        return anchor
    with get_conn() as conn:
        add_user_peer(conn, ticker, symbol, current_user()["id"])
    _log("peer_add", ticker)
    flash(f"Added {symbol} as a competitor of {ticker}.", "ok")
    return anchor


@app.post("/stock/<ticker>/peers/remove")
@login_required
def remove_peer(ticker):
    """Remove a community-added peer (curated ones aren't in user_peers, so
    they can't be removed from the web — by design)."""
    ticker = ticker.upper()
    peer = (request.form.get("peer") or "").strip().upper()
    with get_conn() as conn:
        remove_user_peer(conn, ticker, peer)
    _log("peer_remove", ticker)
    return redirect(url_for("stock", ticker=ticker) + "#peers")


@app.template_filter("flowlabel")
def flowlabel(p):
    """Income-flow period → human label: 'FY2025' → 'FY 2025', '2025Q3' → 'Q3 2025'."""
    if not p:
        return ""
    if p.startswith("FY"):
        return "FY " + p[2:]
    year, _, q = p.partition("Q")
    return f"Q{q} {year}" if q else p


@app.template_filter("exch")
def exch(value):
    """Yahoo's exchange tags are sub-market codes (NasdaqGS = "Global Select",
    NasdaqGM = "Global Market") — visitors just need the exchange's name."""
    return {"nasdaqgs": "NASDAQ", "nasdaqgm": "NASDAQ", "nasdaqcm": "NASDAQ"}.get(
        (value or "").lower(), value or "")


@app.template_filter("money")
def money(value, currency="USD"):
    if value is None:
        return "—"
    sym = "₹" if currency == "INR" else "$" if currency == "USD" else currency + " "
    return f"{sym}{value:,.2f}"


@app.template_filter("bigmoney")
def bigmoney(value, currency="USD"):
    if not value:
        return "—"
    sym = "₹" if currency == "INR" else "$" if currency == "USD" else currency + " "
    for div, unit in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if value >= div:
            return f"{sym}{value / div:,.2f}{unit}"
    return f"{sym}{value:,.0f}"


@app.errorhandler(404)
def not_found(_e):
    """Friendly, on-brand 404 instead of Flask's stock page — sleepy Otto plus a
    way back home / to the search. Every abort(404) in the app lands here."""
    return render_template("404.html"), 404


if __name__ == "__main__":
    import sys
    # Local HTTP dev: allow the session cookie over http://localhost (prod keeps
    # Secure on via Caddy's HTTPS). Optional port arg for running a second copy.
    app.config["SESSION_COOKIE_SECURE"] = False
    init_db()
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8700
    app.run(port=port, debug=True)
