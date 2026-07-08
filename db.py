"""SQLite plumbing for InvestRight. Cron writes, web app reads (DESIGN.md §3).

Phase 1–2: stocks, watchlist, fx_rates, snapshots.
Phase 3 (deep-dive) adds: fundamentals, health_checks, news, notes, dcf, plus
four value-check columns on snapshots. init_db is idempotent (§8.2).
Phase 4 ("Today") adds: screener (nightly ranked picks) and digest (the AI
summary of them — kept by date so last-good survives an API outage).
"""
import json
import sqlite3
from datetime import date, datetime, timezone
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
    created_at    TEXT NOT NULL
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
    ip      TEXT                        -- client IP (X-Forwarded-For behind Caddy)
);
"""


SNAP_COLS = ("ticker", "fetched_at", "price", "prev_close", "change_pct",
             "market_cap", "pe", "div_yield", "wk52_low", "wk52_high",
             "pb", "ps", "eps", "industry_pe")

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


def create_user(conn, email, password_hash, name=None, market=None):
    """Insert a new account (email pre-lower-cased by the caller). Returns the
    new user id. Raises sqlite3.IntegrityError if the email is already taken."""
    cur = conn.execute(
        "INSERT INTO users (email,password_hash,name,market,created_at) "
        "VALUES (?,?,?,?,?)", (email, password_hash, name, market, _now()))
    return cur.lastrowid


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


def log_event(conn, action, visitor=None, name=None, market=None,
              ticker=None, path=None, ua=None, ip=None):
    """Append one activity-log row (Phase 6c). Best-effort — callers wrap this in
    try/except so logging can never break a page render."""
    conn.execute(
        "INSERT INTO events (ts,visitor,name,market,action,ticker,path,ua,ip) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (_now(), visitor, name, market, action, ticker, path,
         (ua or "")[:200], ip))


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


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
