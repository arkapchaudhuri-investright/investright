"""Spec 17 — the extended weekly note (portfolio · watchlist · earnings · movers).

Runs against the session temp DB (via the app_module fixture, which repoints
db.DB_PATH). Each test seeds its own rows and deletes them in a finally so the
shared DB stays clean for the smoke tests. No AI key in CI, so digest.ask raises
and build_note returns its honest structured fallback — which is what we assert.
"""
from datetime import date, timedelta

import pytest
from werkzeug.security import generate_password_hash


def _seed(conn, *, holdings=True, watchlist=True):
    """Insert a user + a couple of stocks/snapshots, optionally a holding and a
    watched stock. Returns the user id. Tickers are ZZ-prefixed to stay unique."""
    now = "2026-01-01T00:00:00"
    uid = None
    import db
    uid = db.create_user(conn, "zz_weekly@example.com",
                         generate_password_hash("x" * 10), name="WeeklyQA")
    soon = (date.today() + timedelta(days=3)).isoformat()
    for tk, name in (("ZZHOLD", "Held Co"), ("ZZWATCH", "Watched Co")):
        conn.execute("INSERT OR REPLACE INTO stocks "
                     "(ticker,name,exchange,sector,industry,currency,added_at,next_earnings) "
                     "VALUES (?,?,?,?,?,?,?,?)",
                     (tk, name, "NMS", "Tech", "Software", "USD", now, soon))
        conn.execute("INSERT OR REPLACE INTO snapshots "
                     "(ticker,price,prev_close,change_pct,fetched_at) VALUES (?,?,?,?,?)",
                     (tk, 120.0, 118.0, 2.5, now))
        conn.execute("INSERT OR REPLACE INTO price_history (ticker,d,close) VALUES (?,?,?)",
                     (tk, (date.today() - timedelta(days=8)).isoformat(), 100.0))
    if holdings:
        db.upsert_holding(conn, uid, "ZZHOLD", 10, 100.0)
    if watchlist:
        conn.execute("INSERT OR IGNORE INTO user_watchlist (user_id,ticker,added_at) "
                     "VALUES (?,?,?)", (uid, "ZZWATCH", now))
    return uid


def _cleanup(conn, uid):
    for tk in ("ZZHOLD", "ZZWATCH"):
        conn.execute("DELETE FROM holdings WHERE ticker=?", (tk,))
        conn.execute("DELETE FROM user_watchlist WHERE ticker=?", (tk,))
        conn.execute("DELETE FROM snapshots WHERE ticker=?", (tk,))
        conn.execute("DELETE FROM price_history WHERE ticker=?", (tk,))
        conn.execute("DELETE FROM stocks WHERE ticker=?", (tk,))
    conn.execute("DELETE FROM users WHERE id=?", (uid,))


def _force_fallback(monkeypatch):
    """Make digest.ask raise so build_note returns its structured fallback — the
    deterministic assembly we're testing (the CI env may or may not have a key)."""
    import digest
    monkeypatch.setattr(digest, "ask",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))


def test_build_note_full(app_module, monkeypatch):
    import db
    import weekly
    _force_fallback(monkeypatch)
    with db.get_conn() as conn:
        uid = _seed(conn)
        try:
            user = db.get_user_by_id(conn, uid)
            note = weekly.build_note(conn, user)
            assert note                                   # never None here
            assert "Your portfolio's week" in note        # section 1
            assert "ZZHOLD" in note
            assert "Top movers" in note                   # section 4
            assert "Earnings this week" in note           # section 3 (reports in 3d)
        finally:
            _cleanup(conn, uid)


def test_build_note_none_when_nothing(app_module):
    import db
    import weekly
    with db.get_conn() as conn:
        uid = _seed(conn, holdings=False, watchlist=False)
        try:
            user = db.get_user_by_id(conn, uid)
            assert weekly.build_note(conn, user) is None
        finally:
            _cleanup(conn, uid)


def test_build_note_watchlist_only_omits_portfolio(app_module, monkeypatch):
    import db
    import weekly
    _force_fallback(monkeypatch)
    with db.get_conn() as conn:
        uid = _seed(conn, holdings=False, watchlist=True)
        try:
            note = weekly.build_note(conn, db.get_user_by_id(conn, uid))
            assert note
            assert "Your portfolio's week" not in note     # omitted, no holdings
            assert "Your watchlist's week" in note
        finally:
            _cleanup(conn, uid)


def test_build_note_fallback_returns_structured_text(app_module, monkeypatch):
    import db
    import digest
    import weekly
    monkeypatch.setattr(digest, "ask",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with db.get_conn() as conn:
        uid = _seed(conn)
        try:
            note = weekly.build_note(conn, db.get_user_by_id(conn, uid))
            assert note and note.startswith("Your week at a glance:")
            assert "ZZHOLD" in note                        # structured sections present
        finally:
            _cleanup(conn, uid)


def test_movers_and_earnings_tolerate_empty(app_module):
    # With no snapshots/tickers these sections return '' rather than raising.
    import db
    import weekly
    with db.get_conn() as conn:
        assert weekly._earnings_section(conn, set()) == ""
        # _movers_section reads whatever snapshots exist; just assert it's a str.
        assert isinstance(weekly._movers_section(conn), str)
