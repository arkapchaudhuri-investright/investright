"""Weekly-note opt-in: the signup choice, and unsubscribing straight from an email.

The newsletter's first real run sent 0/0 because every account was created
opted out and the only control lived behind a sign-in. These cover both ends.
"""
import mailer


# --- the token in the email link -------------------------------------------
def test_unsubscribe_token_round_trips(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    tok = mailer.unsubscribe_token(42)
    assert mailer.unsubscribe_user_id(tok) == 42


def test_unsubscribe_token_rejects_tampering(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    tok = mailer.unsubscribe_token(42)
    # Swapping the id for someone else's must not verify — otherwise one link
    # would unsubscribe any account you could guess the number of.
    forged = "43-" + tok.partition("-")[2]
    assert mailer.unsubscribe_user_id(forged) is None
    assert mailer.unsubscribe_user_id("42-" + "0" * 32) is None
    assert mailer.unsubscribe_user_id("") is None
    assert mailer.unsubscribe_user_id("notanumber-abc") is None


def test_unsubscribe_token_dies_with_the_key(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "one")
    tok = mailer.unsubscribe_token(7)
    monkeypatch.setenv("SECRET_KEY", "two")
    assert mailer.unsubscribe_user_id(tok) is None


# --- the routes -------------------------------------------------------------
def _signup(client, email, weekly):
    with client.session_transaction() as sess:
        sess["csrf"] = "tok"
    data = {"email": email, "password": "hunter2hunter2",
            "password2": "hunter2hunter2", "csrf": "tok"}
    if weekly:
        data["weekly_email"] = "1"
    return client.post("/register", data=data)


def _drop(email):
    from db import get_conn
    with get_conn() as conn:
        conn.execute("DELETE FROM users WHERE email=?", (email,))


def test_signup_opts_in_when_the_box_is_left_ticked(app_module, client):
    from db import get_conn, get_user_by_email
    email = "zz_optin@example.com"
    try:
        assert _signup(client, email, weekly=True).status_code in (302, 303)
        with get_conn() as conn:
            assert get_user_by_email(conn, email)["weekly_email"] == 1
    finally:
        _drop(email)


def test_signup_respects_unticking_the_box(app_module, client):
    from db import get_conn, get_user_by_email
    email = "zz_optout@example.com"
    try:
        assert _signup(client, email, weekly=False).status_code in (302, 303)
        with get_conn() as conn:
            assert get_user_by_email(conn, email)["weekly_email"] == 0
    finally:
        _drop(email)


def test_unsubscribe_page_confirms_before_writing(app_module, client):
    from db import get_conn, get_user_by_email
    email = "zz_unsub@example.com"
    try:
        _signup(client, email, weekly=True)
        with get_conn() as conn:
            uid = get_user_by_email(conn, email)["id"]
        token = mailer.unsubscribe_token(uid)

        # The GET only shows the confirmation — mail scanners follow links, and
        # a GET that wrote would opt people out on their behalf (and break §3).
        resp = client.get(f"/unsubscribe/{token}")
        assert resp.status_code == 200
        assert "Stop Otto's weekly note?" in resp.get_data(as_text=True)
        with get_conn() as conn:
            assert get_user_by_email(conn, email)["weekly_email"] == 1

        with client.session_transaction() as sess:
            sess["csrf"] = "tok"
        resp = client.post(f"/unsubscribe/{token}", data={"csrf": "tok"})
        assert resp.status_code == 200
        assert "no more weekly notes" in resp.get_data(as_text=True)
        with get_conn() as conn:
            assert get_user_by_email(conn, email)["weekly_email"] == 0
    finally:
        _drop(email)


def test_unsubscribe_404s_on_a_bad_token(client):
    assert client.get("/unsubscribe/1-deadbeef").status_code == 404


def test_weekly_note_carries_a_working_unsubscribe_link(app_module, monkeypatch):
    """The link in the email is the only way out for someone who isn't signed
    in, so assert the send path actually builds one — nothing else exercises it
    until a Sunday in production."""
    import weekly
    from db import get_conn, get_user_by_email
    from werkzeug.security import generate_password_hash
    import db

    email = "zz_send@example.com"
    sent = []
    monkeypatch.setattr(weekly.mailer, "enabled", lambda: True)
    monkeypatch.setattr(weekly.mailer, "send",
                        lambda to, subject, body, **kw: sent.append((to, body, kw)) or True)
    monkeypatch.setattr(weekly, "build_note", lambda conn, u: "Your week at a glance.")
    try:
        with get_conn() as conn:
            uid = db.create_user(conn, email, generate_password_hash("x" * 10))
            conn.execute("UPDATE users SET weekly_email=1 WHERE id=?", (uid,))
        weekly.main()
        assert len(sent) == 1
        to, body, kw = sent[0]
        assert to == email
        token = mailer.unsubscribe_token(uid)
        assert f"/unsubscribe/{token}" in body
        assert kw["headers"]["List-Unsubscribe"] == f"<{weekly.SITE}/unsubscribe/{token}>"
        assert "Not investment advice." in body
    finally:
        _drop(email)
