"""The session-shape lever in analytics.rebuild_sessions (item 7).

Human share had drifted from ~4% to ~13% as traffic arrived from IPs whose
AS-organisation isn't on any datacenter list and whose user-agents look like
Chrome. The remaining signal is whether the visit behaved like one.
"""
import analytics

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/140"


def _event(conn, visitor, ts, path, ip="81.2.69.142", ua=UA, user_id=None):
    conn.execute(
        "INSERT INTO events (ts, visitor, action, path, ua, ip, user_id) "
        "VALUES (?,?,?,?,?,?,?)", (ts, visitor, "view", path, ua, ip, user_id))


def _rebuild(conn):
    conn.execute("DELETE FROM events")
    conn.execute("DELETE FROM sessions")
    return conn


def _flags(conn):
    return {r["visitor"]: (r["is_bot"], r["pages"], r["duration_s"])
            for r in conn.execute(
                "SELECT visitor, is_bot, pages, duration_s FROM sessions")}


def test_a_one_page_zero_second_visit_is_filtered(app_module):
    from db import get_conn
    with get_conn() as conn:
        _rebuild(conn)
        try:
            _event(conn, "zz-drive", "2026-08-01T10:00:00+00:00", "/")
            analytics.rebuild_sessions(conn)
            assert _flags(conn)["zz-drive"][0] == 1
        finally:
            _rebuild(conn)


def test_a_second_page_is_evidence_enough(app_module):
    from db import get_conn
    with get_conn() as conn:
        _rebuild(conn)
        try:
            # Two views seconds apart: under the time bar, but it reached a
            # second page, which a drive-by fetch doesn't do.
            _event(conn, "zz-two", "2026-08-01T10:00:00+00:00", "/")
            _event(conn, "zz-two", "2026-08-01T10:00:03+00:00", "/stock/AAPL")
            analytics.rebuild_sessions(conn)
            flags = _flags(conn)["zz-two"]
            assert flags[0] == 0 and flags[1] == 2
        finally:
            _rebuild(conn)


def test_time_on_site_is_evidence_enough_on_its_own(app_module):
    from db import get_conn
    with get_conn() as conn:
        _rebuild(conn)
        try:
            _event(conn, "zz-slow", "2026-08-01T10:00:00+00:00", "/")
            _event(conn, "zz-slow", "2026-08-01T10:00:40+00:00", "/")
            analytics.rebuild_sessions(conn)
            assert _flags(conn)["zz-slow"][0] == 0
        finally:
            _rebuild(conn)


def test_a_signed_in_visit_is_never_filtered_on_shape(app_module):
    # An account holder who loads one page is unarguably a person, whatever the
    # session numbers say — the shape rule must not reach them.
    import db
    from db import get_conn
    from werkzeug.security import generate_password_hash
    with get_conn() as conn:
        _rebuild(conn)
        uid = db.create_user(conn, "zz_bots@example.com",
                             generate_password_hash("x" * 10))
        try:
            _event(conn, "zz-acct", "2026-08-01T10:00:00+00:00", "/", user_id=uid)
            analytics.rebuild_sessions(conn)
            assert _flags(conn)["zz-acct"][0] == 0
        finally:
            _rebuild(conn)
            conn.execute("DELETE FROM users WHERE id=?", (uid,))


def test_a_crawler_user_agent_is_still_caught_regardless_of_shape(app_module):
    from db import get_conn
    with get_conn() as conn:
        _rebuild(conn)
        try:
            _event(conn, "zz-crawl", "2026-08-01T10:00:00+00:00", "/",
                   ua="Mozilla/5.0 (compatible; SemrushBot/7~bl)")
            _event(conn, "zz-crawl", "2026-08-01T10:01:00+00:00", "/today",
                   ua="Mozilla/5.0 (compatible; SemrushBot/7~bl)")
            analytics.rebuild_sessions(conn)
            assert _flags(conn)["zz-crawl"][0] == 1
        finally:
            _rebuild(conn)
