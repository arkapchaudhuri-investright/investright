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
from auth import bp as auth_bp, client_ip, current_user, login_required
from db import (LOGIN_MAX_PER_EMAIL, LOGIN_MAX_PER_IP, LOGIN_WINDOW_MIN,
                add_user_watch, get_conn, get_user_note, init_db, log_event,
                recent_login_failures, remove_user_watch, save_snapshot,
                save_user_note, user_watches)

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


def greeting():
    h = datetime.now().hour
    return "Good morning" if h < 12 else "Good afternoon" if h < 17 else "Good evening"


@app.route("/")
def home():
    ccy = request.args.get("ccy") or request.cookies.get("ccy") or "USD"
    if ccy not in ("USD", "INR"):
        ccy = "USD"
    fx, fx_on = get_usdinr()
    # Watchlist is per-account now (§10.4): logged-in shows the user's own list;
    # logged-out shows an empty sign-in CTA (search / analyze / Today stay open).
    user = current_user()
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
    rows = [convert_row(dict(r), ccy, fx) for r in rows]
    as_of = max((r["fetched_at"] for r in rows if r["fetched_at"]), default=None)
    if as_of:
        as_of = datetime.fromisoformat(as_of).astimezone().strftime("%-d %b, %-I:%M %p")
    fx_stale = (datetime.fromisoformat(fx_on).strftime("%-d %b")
                if fx_on and fx_on != date.today().isoformat() else None)
    # Date the rate is from (fresh or stale) — shown inline at the point of
    # conversion when displaying ₹ prices (§11.3).
    fx_on_label = datetime.fromisoformat(fx_on).strftime("%-d %b") if fx_on else None
    resp = make_response(render_template(
        "home.html", rows=rows, as_of=as_of, greeting=greeting(),
        ccy=ccy, fx=fx, fx_stale=fx_stale, fx_on_label=fx_on_label, show_fx=True))
    if request.args.get("ccy"):
        resp.set_cookie("ccy", ccy, max_age=180 * 24 * 3600)
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
        logos.ensure(meta["ticker"], meta.get("website"))
    except Exception:
        pass
    snap = fetch.snapshot(symbol)
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO stocks (ticker,name,exchange,sector,currency,added_at) "
                     "VALUES (?,?,?,?,?,?)",
                     (meta["ticker"], meta["name"], meta["exchange"],
                      meta["sector"], meta["currency"], now))
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
    return meta


_NOT_FOUND = ("Otto couldn't find “{}” on Yahoo — check the symbol? "
              "Indian tickers need .NS or .BO (e.g. RELIANCE.NS).")


@app.post("/add")
@login_required
def add():
    user = current_user()
    symbol = request.form.get("symbol", "").strip().upper()
    if not symbol:
        return redirect(url_for("home"))
    with get_conn() as conn:
        if user_watches(conn, user["id"], symbol):
            flash(f"{symbol} is already on your watchlist.", "info")
            return redirect(url_for("home"))
    meta = _ingest_stock(symbol)
    if not meta:
        flash(_NOT_FOUND.format(symbol), "error")
        return redirect(url_for("home"))
    with get_conn() as conn:
        add_user_watch(conn, user["id"], meta["ticker"])   # per-user + global union
        try:  # fold the newcomer into /today's ranking (DB-only, instant)
            refresh_job.run_screener(conn)
        except Exception:
            pass
    _log("add", meta["ticker"])
    flash(f"Added {meta['name']} to your watchlist.", "ok")
    return redirect(url_for("home"))


@app.post("/analyze")
def analyze():
    """The search bar's primary action: open a ticker's deep-dive WITHOUT adding
    it to the watchlist. Fetches + persists the first time we see a symbol (the
    /stock page itself stays DB-only reads, §3), then redirects. Add-to-watchlist
    is a separate button — in the search bar and the ☆ on the deep-dive header."""
    symbol = request.form.get("symbol", "").strip().upper()
    if not symbol:
        return redirect(url_for("home"))
    with get_conn() as conn:
        known = conn.execute("SELECT 1 FROM stocks WHERE ticker=?", (symbol,)).fetchone()
    if not known:                        # only hit Yahoo the first time
        meta = _ingest_stock(symbol)
        if not meta:
            flash(_NOT_FOUND.format(symbol), "error")
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
    return redirect(url_for("home"))


@app.post("/refresh")
def refresh():
    with get_conn() as conn:
        symbols = [r["ticker"] for r in conn.execute("SELECT ticker FROM watchlist")]
        peers = [p for p in refresh_job.peer_symbols(symbols)
                 if refresh_job.ensure_stock(conn, p)]
    snaps = fetch.snapshot_many(symbols + peers)
    with get_conn() as conn:
        for snap in snaps:
            save_snapshot(conn, snap)
        for sym in symbols + peers:       # refresh deep data too (checks/DCF/news)
            try:
                refresh_job.save_deep(conn, sym)
            except Exception:
                pass
        try:  # keep /today's ranking in step; the digest stays nightly (cron)
            refresh_job.run_screener(conn)
        except Exception:
            pass
    if symbols and not snaps:
        flash("Yahoo isn't answering right now — showing your last saved prices.", "error")
    return redirect(url_for("home"))


@app.route("/team")
def team():
    """Meet Our Team (§4-style static page). No DB — content lives in the template."""
    return render_template("team.html")


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
    # the shown list still gets the #1 hero treatment.
    market = (request.cookies.get("ir_market") or "BOTH").upper()

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
        drow = conn.execute(
            "SELECT * FROM digest ORDER BY digest_date DESC LIMIT 1").fetchone()

    dig = dict(drow) if drow else None
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
    resp = make_response(render_template(
        "today.html", picks=picks, digest=dig, as_of=as_of, mood=mood,
        takeaway=metrics.today_takeaway(picks), market=market,
        date_label=date.today().strftime("%A, %-d %B"),
        weights=metrics.SCREEN_WEIGHTS, upside_cap=metrics.UPSIDE_CAP,
        scope=scope, mine_count=len(mine), screened=len(rows)))
    if request.args.get("scope"):
        resp.set_cookie("ir_today_scope", scope, max_age=60 * 60 * 24 * 365,
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
        if not s:
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

        # Competitors strip (§4.9) — hand-curated map, scores from saved checks.
        peers = []
        for p in metrics.PEERS.get(ticker, []):
            prow = conn.execute(
                "SELECT s.ticker, s.name, s.currency, n.price, n.change_pct "
                "FROM stocks s LEFT JOIN snapshots n ON n.ticker = s.ticker "
                "WHERE s.ticker=?", (p,)).fetchone()
            if not prow:
                peers.append({"ticker": p, "missing": True})
                continue
            pchecks = [{"axis": r["axis"],
                        "passed": None if r["passed"] is None else bool(r["passed"])}
                       for r in conn.execute(
                           "SELECT axis, passed FROM health_checks WHERE ticker=?", (p,))]
            pscores = metrics.axis_scores(pchecks) if pchecks else None
            peers.append({**dict(prow), "missing": False,
                          "snowflake": metrics.snowflake(pscores, cx=30, cy=30, R=25)
                          if pscores else None})

        insiders = [dict(r) for r in conn.execute(
            "SELECT * FROM insider_tx WHERE ticker=? ORDER BY filed_at DESC LIMIT 10",
            (ticker,))]

    # SQLite stores passed as 1/0/NULL → back to bool/None for the template.
    for c in checks:
        c["passed"] = None if c["passed"] is None else bool(c["passed"])

    # Fair value: cron's stored row, unless the reader overrode assumptions in the URL.
    g, d, tg = _float_arg("growth"), _float_arg("discount"), _float_arg("terminal")
    overridden = any(x is not None for x in (g, d, tg))
    dcf = dict(dcf_row) if dcf_row else None
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
    if dcf and dcf.get("assumptions_json") and "basis" not in dcf:
        dcf["basis"] = json.loads(dcf["assumptions_json"]).get("basis")

    scores = metrics.axis_scores(checks)
    snowflake = metrics.snowflake(scores)
    overall = metrics.overall_score(scores)
    charts = metrics.performance_charts(funds)
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

    _log("view", ticker)
    return render_template(
        "stock.html", s=dict(s), snap=dict(snap) if snap else None,
        logo=logos.find(ticker),
        ccy=s["currency"], dcf=dcf, price=price, overridden=overridden,
        checks_by_axis=checks_by_axis, axis_names=dict(metrics.AXES),
        axis_detail=axis_detail, top6=top6, applicable_n=len(applicable),
        scores=scores, snowflake=snowflake, overall=overall,
        mood=metrics.mood_for(overall),
        takeaway=metrics.takeaway(s["name"], dcf, scores),
        charts=charts, fund_source=fund_source, market_growth_pct=round(metrics.MARKET_GROWTH * 100),
        projection=metrics.future_projection(funds),
        dividend=metrics.dividend_card(funds, snap["div_yield"] if snap else None),
        peers=peers, insiders=insiders,
        ins_buys=sum(1 for i in insiders if i["action"] == "buy"),
        ins_sells=sum(1 for i in insiders if i["action"] == "sell"),
        is_us="." not in ticker,
        news=news, note=dict(note) if note else None, on_watch=on_watch, as_of=as_of)


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

    cur = s["currency"]
    scores = metrics.axis_scores(checks)
    lines = [f"Company: {s['name']} ({ticker}) on {s['exchange']}. Prices in {cur}."]
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


if __name__ == "__main__":
    import sys
    # Local HTTP dev: allow the session cookie over http://localhost (prod keeps
    # Secure on via Caddy's HTTPS). Optional port arg for running a second copy.
    app.config["SESSION_COOKIE_SECURE"] = False
    init_db()
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8700
    app.run(port=port, debug=True)
