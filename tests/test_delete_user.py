"""delete_user must leave nothing pointing at the erased account.

`sessions` is rebuilt by analytics from `events` and has no foreign key to
users, so it survived a delete still carrying the user_id — found while
clearing three bot signups. events.user_id is ON DELETE SET NULL; this applies
the same rule to the table derived from it.
"""
from werkzeug.security import generate_password_hash

NOW = "2026-01-01T00:00:00"


def test_delete_user_clears_the_analytics_session_pointer(app_module):
    import db
    from db import get_conn
    with get_conn() as conn:
        uid = db.create_user(conn, "zz_del@example.com",
                             generate_password_hash("x" * 10), name="DelQA")
        conn.execute(
            "INSERT INTO sessions (id, visitor, started_at, ended_at, duration_s, "
            "pages, signed_in, user_id) VALUES (?,?,?,?,?,?,?,?)",
            ("zz-session", "zz-visitor", NOW, NOW, 3, 2, 1, uid))
    try:
        with get_conn() as conn:
            db.delete_user(conn, uid)
        with get_conn() as conn:
            row = conn.execute("SELECT user_id FROM sessions WHERE id=?",
                               ("zz-session",)).fetchone()
            # The visit itself stays — it's analytics history, and nulling the
            # pointer is what anonymises it.
            assert row is not None
            assert row["user_id"] is None
            assert conn.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone() is None
    finally:
        with get_conn() as conn:
            conn.execute("DELETE FROM sessions WHERE id=?", ("zz-session",))
            conn.execute("DELETE FROM users WHERE id=?", (uid,))
