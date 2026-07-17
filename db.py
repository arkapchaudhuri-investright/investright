"""SQLite plumbing for InvestRight. Cron writes, web app reads (DESIGN.md §3).

Phase 1–2: stocks, watchlist, fx_rates, snapshots.
Phase 3 (deep-dive) adds: fundamentals, health_checks, news, notes, dcf, plus
four value-check columns on snapshots. init_db is idempotent (§8.2).
Phase 4 ("Today") adds: screener (nightly ranked picks) and digest (the AI
summary of them — kept by date so last-good survives an API outage).
"""
import hashlib
import json
import secrets
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
DB_PATH = DATA_DIR / "investright.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS stocks (
    ticker   TEXT PRIMARY KEY,          -- Yahoo symbol: AAPL, RELIANCE.NS, TCS.BO
    name     TEXT NOT NULL,
    exchange TEXT NOT NULL DEFAULT '',
    sector   TEXT NOT NULL DEFAULT '',
    currency TEXT NOT NULL DEFAULT 'USD',
    added_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS watchlist (
    ticker   TEXT PRIMARY KEY REFERENCES stocks(ticker) ON DELETE CASCADE,
    added_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fx_rates (
    pair       TEXT PRIMARY KEY,        -- e.g. USDINR
    rate       REAL NOT NULL,
    fetched_on TEXT NOT NULL            -- date; refreshed at most once a day
);
CREATE TABLE IF NOT EXISTS snapshots (
    ticker     TEXT PRIMARY KEY REFERENCES stocks(ticker) ON DELETE CASCADE,
    fetched_at TEXT NOT NULL,
    price      REAL,
    prev_close REAL,
    change_pct REAL,
    market_cap REAL,
    pe         REAL,
    div_yield  REAL,                    -- percent, computed from dividendRate/price
    wk52_low   REAL,
    wk52_high  REAL
    -- pb / ps / eps / industry_pe added by _migrate (Phase 3 value checks)
);
CREATE TABLE IF NOT EXISTS fundamentals (
    ticker         TEXT NOT NULL REFERENCES stocks(ticker) ON DELETE CASCADE,
    fiscal_year    INTEGER NOT NULL,
    revenue        REAL,
    net_income     REAL,
    fcf            REAL,
    total_assets   REAL,
    total_liab     REAL,
    current_assets REAL,
    current_liab   REAL,
    long_term_debt REAL,
    equity         REAL,
    ebit           REAL,
    op_cash_flow   REAL,
    shares         REAL,
    dividends_paid REAL,
    source         TEXT NOT NULL DEFAULT 'yfinance',   -- edgar | yfinance
    fetched_at     TEXT NOT NULL,
    PRIMARY KEY (ticker, fiscal_year)
);
CREATE TABLE IF NOT EXISTS health_checks (
    ticker      TEXT NOT NULL REFERENCES stocks(ticker) ON DELETE CASCADE,
    axis        TEXT NOT NULL,          -- value|future|past|health|dividend
    check_id    TEXT NOT NULL,
    label       TEXT NOT NULL,
    passed      INTEGER,                -- 1 pass, 0 fail, NULL n/a (missing data)
    detail      TEXT,
    computed_at TEXT NOT NULL,
    PRIMARY KEY (ticker, check_id)
);
CREATE TABLE IF NOT EXISTS news (
    ticker       TEXT NOT NULL REFERENCES stocks(ticker) ON DELETE CASCADE,
    published_at TEXT,
    title        TEXT,
    publisher    TEXT,
    url          TEXT NOT NULL,
    fetched_at   TEXT NOT NULL,
    PRIMARY KEY (ticker, url)
);
CREATE TABLE IF NOT EXISTS notes (
    ticker     TEXT PRIMARY KEY REFERENCES stocks(ticker) ON DELETE CASCADE,
    body       TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dcf (
    ticker          TEXT PRIMARY KEY REFERENCES stocks(ticker) ON DELETE CASCADE,
    fair_value      REAL,
    upside_pct      REAL,
    growth_used     REAL,
    discount_rate   REAL,
    terminal_growth REAL,
    assumptions_json TEXT,
    computed_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS screener (
    ticker       TEXT PRIMARY KEY REFERENCES stocks(ticker) ON DELETE CASCADE,
    rank         INTEGER NOT NULL,
    score        REAL NOT NULL,          -- 0–100 "interestingness", transparent components
    is_watchlist INTEGER NOT NULL,       -- 0 = rode along as a peer
    components_json TEXT,                -- per-part score breakdown for the UI
    reasons_json TEXT,                   -- [{kind, label}] chips, strongest first
    computed_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS digest (
    digest_date TEXT PRIMARY KEY,        -- local date; rerunning a night overwrites it
    body        TEXT NOT NULL,
    model       TEXT,                    -- which free LLM wrote it (honest label, §1)
    picks_json  TEXT,                    -- tickers it covered, in rank order
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS insider_tx (
    ticker     TEXT NOT NULL REFERENCES stocks(ticker) ON DELETE CASCADE,
    filed_at   TEXT,                    -- transaction date from the Form 4
    name       TEXT,
    role       TEXT,
    action     TEXT,                    -- buy|sell|award|exercise|gift|tax-withhold|other
    code       TEXT,                    -- raw Form 4 transaction code
    shares     REAL,
    price      REAL,
    value      REAL,
    url        TEXT,                    -- SEC filing index page
    fetched_at TEXT NOT NULL
);
-- Accounts (Phase 8). Optional login — the public site stays open (§10.0); an
-- account only unlocks a per-user watchlist (+ notes in Tier B). Email is stored
-- lower-cased; only a password *hash* is kept (Werkzeug PBKDF2), never the raw.
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT UNIQUE NOT NULL,   -- lower-cased; unverified in v1 (§10.6)
    password_hash TEXT NOT NULL,
    name          TEXT,
    market        TEXT,
    created_at    TEXT NOT NULL,
    -- Mirrored into the signed-cookie session as `stok`. Rotating it invalidates
    -- every session for this account — the only way to log other devices out
    -- when the session store is a client-side cookie (Tier C change-password).
    session_token TEXT
);
-- Per-user watchlist membership. The global `watchlist` table stays as the
-- union of every tracked ticker the nightly refresh must fetch (+ what /today
-- screens); this table is who-tracks-what for the home page (§10.2/§10.4).
CREATE TABLE IF NOT EXISTS user_watchlist (
    user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    ticker   TEXT NOT NULL REFERENCES stocks(ticker) ON DELETE CASCADE,
    added_at TEXT NOT NULL,
    PRIMARY KEY (user_id, ticker)
);
-- Per-user decision journal (Phase 8 Tier B). The old global `notes` table is
-- left in place (unused for new writes) — no migration, everyone starts fresh.
CREATE TABLE IF NOT EXISTS user_notes (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    ticker     TEXT NOT NULL REFERENCES stocks(ticker) ON DELETE CASCADE,
    body       TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, ticker)
);
-- Failed sign-in attempts (Tier C throttling). One row per failure; success
-- clears the rows for that email+IP. Pruned to the window on every check, so it
-- stays tiny. Shared via SQLite rather than kept in memory, because gunicorn
-- runs 2 workers and an in-process counter would only see half the attempts.
CREATE TABLE IF NOT EXISTS login_attempts (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT,                          -- lower-cased; what was typed, may not exist
    ip    TEXT,
    ts    TEXT NOT NULL                  -- UTC ISO8601, same format as _now()
);
CREATE INDEX IF NOT EXISTS idx_login_attempts_ts ON login_attempts(ts);
-- Password-reset tokens. Only a SHA-256 *hash* of the token is stored, for the
-- same reason passwords are hashed: whoever reads this table must not be able
-- to reset anyone's password with what they find. Single-use (used_at) and
-- short-lived (expires_at); `ip` exists so we can rate-limit requests without a
-- second table. Rows are pruned on every new request.
CREATE TABLE IF NOT EXISTS password_resets (
    token_hash TEXT PRIMARY KEY,         -- sha256 of the token we emailed
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    ip         TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at    TEXT                      -- non-NULL once redeemed
);
CREATE INDEX IF NOT EXISTS idx_password_resets_user ON password_resets(user_id);
-- Activity log (Phase 6c). Pre-accounts there's no real identity, so this is
-- best-effort: `visitor` is an anonymous per-browser cookie UUID and `name`/
-- `market` are self-reported (mirrored from the visitor's localStorage into
-- cookies) — unverified. Read via the secret-gated /admin page.
CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,              -- UTC ISO8601
    visitor TEXT,                       -- anonymous per-browser cookie UUID
    name    TEXT,                       -- self-reported (localStorage), unverified
    market  TEXT,                       -- US|IN|BOTH, self-reported
    action  TEXT NOT NULL,              -- view|analyze|add|remove
    ticker  TEXT,
    path    TEXT,
    ua      TEXT,                       -- coarse user-agent (truncated)
    ip      TEXT,                       -- client IP (X-Forwarded-For behind Caddy)
    -- The one *verified* identity here, when the visitor was signed in (§10.2).
    -- SET NULL, not CASCADE: deleting an account must not punch holes in the
    -- activity history, but it must stop pointing at the person.
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL
);

-- Daily closing prices for the deep-dive trend chart (Phase: trend widget).
-- Backfilled with Yahoo's full history at ingest, topped up nightly by the
-- refresh job. Web reads only.
CREATE TABLE IF NOT EXISTS price_history (
    ticker TEXT NOT NULL REFERENCES stocks(ticker) ON DELETE CASCADE,
    d      TEXT NOT NULL,               -- YYYY-MM-DD (exchange local)
    close  REAL NOT NULL,
    PRIMARY KEY (ticker, d)
);

-- Monthly rule-based picks for /strategies ("Otto's current matches").
-- Written by strategy_screen.run() (cron side, ~every 30 days); the page only
-- reads. One batch at a time — run() replaces it wholesale.
CREATE TABLE IF NOT EXISTS strategy_picks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_date TEXT NOT NULL,           -- YYYY-MM-DD the sweep ran
    strategy   TEXT NOT NULL,           -- strategies.py id (capex, canslim, …)
    market     TEXT NOT NULL,           -- US | IN
    rank       INTEGER NOT NULL,
    ticker     TEXT NOT NULL,
    name       TEXT,
    why        TEXT NOT NULL            -- the numbers that selected it, in words
);

-- Company leadership for the deep-dive "Leadership" grid. rank = Yahoo's
-- listing order. Base fields come from yfinance companyOfficers at ingest;
-- photo/edu/bio are best-effort Wikidata enrichment done by the nightly
-- refresh (enriched=1 once attempted-and-resolved, so nobodies aren't
-- re-queried forever). pay is total yearly comp in the listing's native
-- currency; the page converts at display time.
CREATE TABLE IF NOT EXISTS executives (
    ticker     TEXT NOT NULL REFERENCES stocks(ticker) ON DELETE CASCADE,
    rank       INTEGER NOT NULL,
    name       TEXT NOT NULL,
    title      TEXT,
    age        INTEGER,
    pay        REAL,
    photo      TEXT,                    -- cached file under static/execs/
    edu        TEXT,                    -- "Harvard University, IIT Bombay"
    bio        TEXT,                    -- Wikidata's one-line description
    enriched   INTEGER NOT NULL DEFAULT 0,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (ticker, rank)
);

-- Community-added competitors. Any signed-in user can add a peer for any
-- stock (visible to everyone, badged "user added") or remove a user-added
-- one; the hand-curated metrics.PEERS entries are never deletable from the
-- web. Writes happen in POST routes only (web never writes on GET).
CREATE TABLE IF NOT EXISTS user_peers (
    ticker   TEXT NOT NULL REFERENCES stocks(ticker) ON DELETE CASCADE,
    peer     TEXT NOT NULL,
    added_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    added_at TEXT NOT NULL,
    PRIMARY KEY (ticker, peer)
);

-- Income-statement breakdowns for the "Revenue & Expenses" widget (Sankey +
-- table on the deep-dive). One row per (ticker, period): every annual year
-- Yahoo serves (~5) plus the recent quarters (~5) — "last 5 years or max
-- whatever is there". Cron writes, the page reads. Money is in the stock's
-- native reporting currency (the deep-dive converts at display time); missing
-- line items stay NULL — the widget folds them into balancing buckets.
CREATE TABLE IF NOT EXISTS income_flow (
    ticker        TEXT NOT NULL REFERENCES stocks(ticker) ON DELETE CASCADE,
    period        TEXT NOT NULL,        -- "FY2025" | "2026Q1"
    ptype         TEXT NOT NULL,        -- A (annual) | Q (quarterly)
    end_date      TEXT NOT NULL,        -- period end, YYYY-MM-DD (Y/Y matching)
    revenue       REAL,
    cost_of_rev   REAL,
    gross_profit  REAL,
    rd            REAL,                 -- research & development
    sga           REAL,                 -- selling, general & administrative
    operating_inc REAL,
    net_income    REAL,
    tax           REAL,
    fetched_at    TEXT NOT NULL,
    PRIMARY KEY (ticker, period)
);
"""


SNAP_COLS = ("ticker", "fetched_at", "price", "prev_close", "change_pct",
             "market_cap", "pe", "div_yield", "wk52_low", "wk52_high",
             "pb", "ps", "eps", "industry_pe",
             "rec_key", "rec_mean", "analyst_n", "target_mean")

FUND_COLS = ("ticker", "fiscal_year", "revenue", "net_income", "fcf",
             "total_assets", "total_liab", "current_assets", "current_liab",
             "long_term_debt", "equity", "ebit", "op_cash_flow", "shares",
             "dividends_paid", "source", "fetched_at")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_snapshot(conn, snap):
    conn.execute(
        f"INSERT OR REPLACE INTO snapshots ({','.join(SNAP_COLS)}) "
        f"VALUES ({','.join('?' * len(SNAP_COLS))})",
        [snap.get(c) for c in SNAP_COLS])


def save_price_history(conn, ticker, rows):
    """Upsert (date, close) rows for the deep-dive trend chart."""
    conn.executemany(
        "INSERT OR REPLACE INTO price_history (ticker, d, close) VALUES (?, ?, ?)",
        [(ticker, d, c) for d, c in rows])


def save_executives(conn, ticker, rows):
    """Replace this ticker's leadership list (delete-then-insert keeps ranks
    honest when Yahoo reorders), preserving prior enrichment by name."""
    old = {r["name"]: dict(r) for r in conn.execute(
        "SELECT * FROM executives WHERE ticker=?", (ticker,))}
    conn.execute("DELETE FROM executives WHERE ticker=?", (ticker,))
    now = _now()
    for i, o in enumerate(rows):
        keep = old.get(o["name"], {})
        conn.execute(
            "INSERT INTO executives (ticker,rank,name,title,age,pay,photo,edu,"
            "bio,enriched,fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (ticker, i, o["name"], o.get("title"), o.get("age"), o.get("pay"),
             keep.get("photo"), keep.get("edu"), keep.get("bio"),
             keep.get("enriched", 0), now))


INCOME_FLOW_COLS = ("ticker", "period", "ptype", "end_date", "revenue",
                    "cost_of_rev", "gross_profit", "rd", "sga", "operating_inc",
                    "net_income", "tax", "fetched_at")


def save_income_flow(conn, ticker, rows):
    """Upsert per-period income breakdowns for the deep-dive widget."""
    now = _now()
    for d in rows:
        vals = {**d, "ticker": ticker, "fetched_at": now}
        conn.execute(
            f"INSERT OR REPLACE INTO income_flow ({','.join(INCOME_FLOW_COLS)}) "
            f"VALUES ({','.join('?' * len(INCOME_FLOW_COLS))})",
            [vals.get(c) for c in INCOME_FLOW_COLS])


def user_peers_for(conn, ticker):
    """Community-added peers for a ticker, oldest first, with the adder's
    display name (falls back to their email's mailbox part)."""
    return conn.execute(
        "SELECT up.peer, up.added_at, "
        "       COALESCE(NULLIF(u.name, ''), substr(u.email, 1, instr(u.email, '@') - 1)) AS added_by "
        "FROM user_peers up LEFT JOIN users u ON u.id = up.added_by "
        "WHERE up.ticker=? ORDER BY up.added_at", (ticker,)).fetchall()


def add_user_peer(conn, ticker, peer, user_id):
    conn.execute(
        "INSERT OR IGNORE INTO user_peers (ticker, peer, added_by, added_at) "
        "VALUES (?,?,?,?)", (ticker, peer, user_id, _now()))


def remove_user_peer(conn, ticker, peer):
    conn.execute("DELETE FROM user_peers WHERE ticker=? AND peer=?", (ticker, peer))


def save_fundamentals(conn, ticker, rows, source="yfinance"):
    """Replace this ticker's multi-year series with `rows` (list of dicts)."""
    now = _now()
    for r in rows:
        vals = {**r, "ticker": ticker, "source": source, "fetched_at": now}
        conn.execute(
            f"INSERT OR REPLACE INTO fundamentals ({','.join(FUND_COLS)}) "
            f"VALUES ({','.join('?' * len(FUND_COLS))})",
            [vals.get(c) for c in FUND_COLS])


def save_checks(conn, ticker, checks):
    """Overwrite this ticker's checks (delete-then-insert so stale ids don't linger)."""
    now = _now()
    conn.execute("DELETE FROM health_checks WHERE ticker=?", (ticker,))
    for c in checks:
        passed = None if c["passed"] is None else int(bool(c["passed"]))
        conn.execute(
            "INSERT INTO health_checks (ticker,axis,check_id,label,passed,detail,computed_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (ticker, c["axis"], c["check_id"], c["label"], passed, c["detail"], now))


def save_news(conn, ticker, items):
    now = _now()
    for n in items:
        conn.execute(
            "INSERT OR REPLACE INTO news (ticker,published_at,title,publisher,url,fetched_at) "
            "VALUES (?,?,?,?,?,?)",
            (ticker, n.get("published_at"), n.get("title"),
             n.get("publisher"), n["url"], now))


def save_dcf(conn, ticker, d):
    conn.execute(
        "INSERT OR REPLACE INTO dcf (ticker,fair_value,upside_pct,growth_used,"
        "discount_rate,terminal_growth,assumptions_json,computed_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (ticker, d["fair_value"], d["upside_pct"], d["growth_used"],
         d["discount_rate"], d["terminal_growth"], d["assumptions_json"], _now()))


def save_insiders(conn, ticker, rows):
    """Replace this ticker's insider rows (delete-then-insert like save_checks;
    callers skip empty fetches so last-good survives an EDGAR outage)."""
    now = _now()
    conn.execute("DELETE FROM insider_tx WHERE ticker=?", (ticker,))
    for r in rows:
        conn.execute(
            "INSERT INTO insider_tx (ticker,filed_at,name,role,action,code,"
            "shares,price,value,url,fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (ticker, r.get("filed_at"), r.get("name"), r.get("role"),
             r.get("action"), r.get("code"), r.get("shares"), r.get("price"),
             r.get("value"), r.get("url"), now))


def save_screener(conn, rows):
    """Overwrite the whole nightly ranking (delete-then-insert like save_checks —
    a ticker that dropped out of the watchlist shouldn't linger on /today)."""
    now = _now()
    conn.execute("DELETE FROM screener")
    for r in rows:
        conn.execute(
            "INSERT INTO screener (ticker,rank,score,is_watchlist,"
            "components_json,reasons_json,computed_at) VALUES (?,?,?,?,?,?,?)",
            (r["ticker"], r["rank"], r["score"], int(bool(r["is_watchlist"])),
             json.dumps(r["components"]), json.dumps(r["reasons"]), now))


def save_digest(conn, body, model, picks):
    conn.execute(
        "INSERT OR REPLACE INTO digest (digest_date,body,model,picks_json,created_at) "
        "VALUES (?,?,?,?,?)",
        (date.today().isoformat(), body, model, json.dumps(picks), _now()))


def save_note(conn, ticker, body):
    conn.execute("INSERT OR REPLACE INTO notes (ticker, body, updated_at) VALUES (?,?,?)",
                 (ticker, body, _now()))


def save_user_note(conn, user_id, ticker, body):
    conn.execute(
        "INSERT OR REPLACE INTO user_notes (user_id, ticker, body, updated_at) "
        "VALUES (?,?,?,?)", (user_id, ticker, body, _now()))


def get_user_note(conn, user_id, ticker):
    return conn.execute("SELECT * FROM user_notes WHERE user_id=? AND ticker=?",
                        (user_id, ticker)).fetchone()


def rotate_session_token(conn, user_id):
    """Mint a fresh session token, invalidating every existing session for this
    account (they carry the old one). Returns the new token for the caller to
    put in the current session."""
    tok = secrets.token_urlsafe(24)
    conn.execute("UPDATE users SET session_token=? WHERE id=?", (tok, user_id))
    return tok


def create_user(conn, email, password_hash, name=None, market=None):
    """Insert a new account (email pre-lower-cased by the caller). Returns the
    new user id. Raises sqlite3.IntegrityError if the email is already taken."""
    cur = conn.execute(
        "INSERT INTO users (email,password_hash,name,market,created_at) "
        "VALUES (?,?,?,?,?)", (email, password_hash, name, market, _now()))
    rotate_session_token(conn, cur.lastrowid)
    return cur.lastrowid


def set_password(conn, user_id, password_hash):
    """Change the password AND rotate the session token, so sessions on other
    devices stop working. Returns the new token."""
    conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                 (password_hash, user_id))
    return rotate_session_token(conn, user_id)


def delete_user(conn, user_id):
    """Erase an account. `user_watchlist` and `user_notes` go with it via
    ON DELETE CASCADE (get_conn sets PRAGMA foreign_keys=ON). The global
    `watchlist` table is deliberately left alone — it's the union of tickers the
    nightly refresh fetches and /today screens, not personal data (§10.4)."""
    conn.execute("DELETE FROM users WHERE id=?", (user_id,))


def get_user_by_email(conn, email):
    return conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()


def get_user_by_id(conn, uid):
    return conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def add_user_watch(conn, user_id, ticker):
    """Add a ticker to a user's watchlist AND to the global union (so the nightly
    refresh + /today keep covering it). Idempotent."""
    now = _now()
    conn.execute("INSERT OR IGNORE INTO user_watchlist (user_id,ticker,added_at) "
                 "VALUES (?,?,?)", (user_id, ticker, now))
    conn.execute("INSERT OR IGNORE INTO watchlist (ticker,added_at) VALUES (?,?)",
                 (ticker, now))


def remove_user_watch(conn, user_id, ticker):
    """Drop a ticker from one user's watchlist. Leaves shared reference data and
    the global union intact (other users / peers / refresh may still need it)."""
    conn.execute("DELETE FROM user_watchlist WHERE user_id=? AND ticker=?",
                 (user_id, ticker))


def user_watches(conn, user_id, ticker):
    return conn.execute(
        "SELECT 1 FROM user_watchlist WHERE user_id=? AND ticker=?",
        (user_id, ticker)).fetchone() is not None


# Sign-in throttling (Tier C). Two limits: per-email (someone guessing one
# account's password) and the looser per-IP one (someone spraying many accounts
# from one host). Both are generous enough that a person mistyping their own
# password a few times never notices.
LOGIN_WINDOW_MIN = 15
LOGIN_MAX_PER_EMAIL = 8
LOGIN_MAX_PER_IP = 20


def _login_cutoff():
    return (datetime.now(timezone.utc) -
            timedelta(minutes=LOGIN_WINDOW_MIN)).isoformat(timespec="seconds")


def login_failures(conn, email, ip):
    """(failures for this email, failures from this IP) inside the window.
    Prunes expired rows first, so the table never grows. Timestamps share one
    UTC format, so a string compare is a time compare."""
    cutoff = _login_cutoff()
    conn.execute("DELETE FROM login_attempts WHERE ts < ?", (cutoff,))
    by_email = conn.execute(
        "SELECT COUNT(*) FROM login_attempts WHERE email=? AND ts >= ?",
        (email, cutoff)).fetchone()[0]
    by_ip = conn.execute(
        "SELECT COUNT(*) FROM login_attempts WHERE ip=? AND ts >= ?",
        (ip, cutoff)).fetchone()[0] if ip else 0
    return by_email, by_ip


def record_login_failure(conn, email, ip):
    conn.execute("INSERT INTO login_attempts (email,ip,ts) VALUES (?,?,?)",
                 (email, ip, _now()))


def clear_login_failures(conn, email, ip):
    """A correct password wipes the slate for that email and that IP — so a
    legitimate person who fumbled their password isn't left near the limit."""
    conn.execute("DELETE FROM login_attempts WHERE email=? OR ip=?", (email, ip))


# Password resets. Short TTL because the link is the only thing standing between
# an inbox and an account. The caps are per-hour and deliberately low: a reset
# email is something a person asks for once, and someone else's inbox is not a
# place we want to be able to flood.
RESET_TTL_MIN = 60
RESET_MAX_PER_HOUR_USER = 3
RESET_MAX_PER_HOUR_IP = 10


def _hash_token(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def _reset_prune(conn):
    """Drop tokens that are spent or expired. Keeps the rate-limit counts honest
    (they only ever look at the last hour) and the table small."""
    conn.execute("DELETE FROM password_resets WHERE used_at IS NOT NULL OR expires_at < ?",
                 (_now(),))


def reset_requests_recent(conn, user_id, ip):
    """(requests for this account, requests from this IP) in the last hour."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
    by_user = conn.execute(
        "SELECT COUNT(*) FROM password_resets WHERE user_id=? AND created_at >= ?",
        (user_id, cutoff)).fetchone()[0]
    by_ip = conn.execute(
        "SELECT COUNT(*) FROM password_resets WHERE ip=? AND created_at >= ?",
        (ip, cutoff)).fetchone()[0] if ip else 0
    return by_user, by_ip


def create_reset(conn, user_id, ip):
    """Mint a reset token. Returns the RAW token — the only time it exists in
    plaintext. It goes straight into the email and is never stored."""
    raw = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) +
               timedelta(minutes=RESET_TTL_MIN)).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO password_resets (token_hash,user_id,ip,created_at,expires_at) "
        "VALUES (?,?,?,?,?)", (_hash_token(raw), user_id, ip, _now(), expires))
    return raw


def get_reset(conn, raw):
    """The still-valid row for this token, else None (unknown, spent, expired)."""
    return conn.execute(
        "SELECT * FROM password_resets WHERE token_hash=? AND used_at IS NULL "
        "AND expires_at >= ?", (_hash_token(raw), _now())).fetchone()


def consume_reset(conn, raw, user_id):
    """Burn this token, and every other outstanding one for the account — asking
    twice and using the first link shouldn't leave a second live link behind."""
    conn.execute("UPDATE password_resets SET used_at=? WHERE token_hash=?",
                 (_now(), _hash_token(raw)))
    conn.execute("DELETE FROM password_resets WHERE user_id=? AND used_at IS NULL",
                 (user_id,))


def recent_login_failures(conn, limit=50):
    """Failed sign-ins still on file, newest first — for the /admin panel.
    Read-only: unlike login_failures() this never prunes, because the web app
    doesn't write (§3). So this is only ever a *recent* slice, never an audit
    trail — a correct password clears that email and IP, and the next sign-in
    attempt prunes everything past the window."""
    return [dict(r) for r in conn.execute(
        "SELECT email, ip, ts FROM login_attempts ORDER BY ts DESC LIMIT ?",
        (limit,))]


def log_event(conn, action, visitor=None, name=None, market=None,
              ticker=None, path=None, ua=None, ip=None, user_id=None):
    """Append one activity-log row (Phase 6c). Best-effort — callers wrap this in
    try/except so logging can never break a page render. `user_id` is the only
    verified field; name/market stay self-reported."""
    conn.execute(
        "INSERT INTO events (ts,visitor,name,market,action,ticker,path,ua,ip,user_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (_now(), visitor, name, market, action, ticker, path,
         (ua or "")[:200], ip, user_id))


def get_conn():
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _migrate(conn):
    """Add columns to existing tables (CREATE IF NOT EXISTS can't). Idempotent."""
    have = {r["name"] for r in conn.execute("PRAGMA table_info(snapshots)")}
    for col in ("pb", "ps", "eps", "industry_pe"):
        if col not in have:
            conn.execute(f"ALTER TABLE snapshots ADD COLUMN {col} REAL")
    # Analyst-sentiment fields (trend/sentiment widgets): Yahoo's consensus.
    for col, typ in (("rec_key", "TEXT"), ("rec_mean", "REAL"),
                     ("analyst_n", "INTEGER"), ("target_mean", "REAL")):
        if col not in have:
            conn.execute(f"ALTER TABLE snapshots ADD COLUMN {col} {typ}")

    # users.session_token (Tier C). Accounts created before this column existed
    # get one now; anyone signed in at that moment is signed out once, because
    # their cookie predates the token. One-off, and only for existing accounts.
    ucols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    if "session_token" not in ucols:
        conn.execute("ALTER TABLE users ADD COLUMN session_token TEXT")
    for r in conn.execute("SELECT id FROM users WHERE session_token IS NULL").fetchall():
        rotate_session_token(conn, r["id"])

    # events.user_id (§10.2). SQLite allows ADD COLUMN with a REFERENCES clause
    # only when it defaults to NULL — which is exactly what we want for the
    # pre-accounts rows.
    ecols = {r["name"] for r in conn.execute("PRAGMA table_info(events)")}
    if "user_id" not in ecols:
        conn.execute("ALTER TABLE events ADD COLUMN user_id INTEGER "
                     "REFERENCES users(id) ON DELETE SET NULL")

    # stocks.industry (island label + same-industry competitor fill). New
    # ingests set it; the nightly refresh backfills existing rows from Yahoo.
    scols = {r["name"] for r in conn.execute("PRAGMA table_info(stocks)")}
    if "industry" not in scols:
        conn.execute("ALTER TABLE stocks ADD COLUMN industry TEXT NOT NULL DEFAULT ''")

    # stocks.next_earnings (spec 13): next earnings date 'YYYY-MM-DD' or NULL.
    # Cron overwrites it every refresh so a past date rolls forward or clears.
    if "next_earnings" not in scols:
        conn.execute("ALTER TABLE stocks ADD COLUMN next_earnings TEXT")

    # income_flow went from one-latest-row-per-ticker to per-period rows
    # (2026-07). The table is a pure refetch-from-Yahoo cache that never
    # shipped to prod in the old shape, so the honest migration is drop +
    # recreate; the next ingest / nightly refresh repopulates it.
    icols = {r["name"] for r in conn.execute("PRAGMA table_info(income_flow)")}
    if icols and "period" not in icols:
        conn.execute("DROP TABLE income_flow")
        conn.executescript(SCHEMA)          # recreate in the new shape


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
