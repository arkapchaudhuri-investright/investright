"""Signup hardening: the honeypot, and confirmed opt-in for the weekly note.

Three bot signups from throwaway domains reached the mailing list because the
signup box set weekly_email=1 outright. Ticking it is now a request, not a
subscription — the emailed token is the only thing that flips the flag.
"""
import mailer

NOW = "2026-01-01T00:00:00"


def _csrf(client):
    with client.session_transaction() as sess:
        sess["csrf"] = "tok"
    return "tok"


def _form(**over):
    base = {"email": "zz_guard@example.com", "password": "x" * 10,
            "password2": "x" * 10, "name": "GuardQA", "csrf": "tok"}
    base.update(over)
    return base


def _drop(email):
    from db import get_conn
    with get_conn() as conn:
        conn.execute("DELETE FROM users WHERE email=?", (email,))


def test_honeypot_blocks_the_signup(app_module, client):
    from db import get_conn
    _csrf(client)
    try:
        resp = client.post("/register", data=_form(website="http://spam.example"))
        assert resp.status_code == 200          # re-renders the form, no account
        with get_conn() as conn:
            assert conn.execute("SELECT 1 FROM users WHERE email=?",
                                ("zz_guard@example.com",)).fetchone() is None
    finally:
        _drop("zz_guard@example.com")


def test_real_signup_still_works_and_starts_unsubscribed(app_module, client):
    # The account is created immediately — the confirmation gates the mailing
    # list, never the product.
    from db import get_conn
    _csrf(client)
    try:
        resp = client.post("/register", data=_form(weekly_email="1"))
        assert resp.status_code in (302, 303)
        with get_conn() as conn:
            row = conn.execute("SELECT id, weekly_email FROM users WHERE email=?",
                               ("zz_guard@example.com",)).fetchone()
        assert row is not None, "signup should succeed"
        assert row["weekly_email"] == 0, "ticking the box must not subscribe on its own"
    finally:
        _drop("zz_guard@example.com")


def test_confirm_token_turns_the_note_on(app_module, client):
    from db import get_conn
    _csrf(client)
    try:
        client.post("/register", data=_form(weekly_email="1"))
        with get_conn() as conn:
            uid = conn.execute("SELECT id FROM users WHERE email=?",
                               ("zz_guard@example.com",)).fetchone()["id"]
        token = mailer.link_token("optin", uid)
        assert client.get(f"/newsletter/confirm/{token}").status_code == 200
        _csrf(client)
        assert client.post(f"/newsletter/confirm/{token}",
                           data={"csrf": "tok"}).status_code == 200
        with get_conn() as conn:
            assert conn.execute("SELECT weekly_email FROM users WHERE id=?",
                                (uid,)).fetchone()["weekly_email"] == 1
    finally:
        _drop("zz_guard@example.com")


def test_a_forged_or_wrong_purpose_token_is_rejected(app_module, client):
    # An unsubscribe token must not double as a subscribe token.
    assert client.get("/newsletter/confirm/1-deadbeef").status_code == 404
    assert client.get(
        f"/newsletter/confirm/{mailer.unsubscribe_token(1)}").status_code == 404


def test_old_unsubscribe_links_still_verify():
    # Those tokens are already sitting in people's inboxes; generalising the
    # helper must not invalidate them.
    assert mailer.unsubscribe_user_id(mailer.unsubscribe_token(42)) == 42
    assert mailer.link_user_id("unsub", mailer.link_token("unsub", 42)) == 42
