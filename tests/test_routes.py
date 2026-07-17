"""Smoke tests: public GET routes render, and CSRF guards POSTs.

These don't assert page *content* — just that the routes wire up, render their
templates without raising, and return 200 against an empty DB. That alone would
have caught the kind of import/template/None-handling breakage we've shipped
before.
"""
import pytest


@pytest.mark.parametrize("path", ["/", "/team", "/strategies", "/today",
                                  "/watchlist", "/login"])
def test_public_get_routes_ok(client, path):
    resp = client.get(path)
    assert resp.status_code == 200, f"{path} -> {resp.status_code}"


def test_unknown_ticker_deep_dive_does_not_500(client):
    # An unknown ticker should redirect or 404 — never a 500.
    resp = client.get("/stock/NOPE.XYZ", follow_redirects=False)
    assert resp.status_code < 500, resp.status_code


def test_post_without_csrf_token_rejected(client):
    # No csrf token in the form → the before_request guard aborts with 400.
    resp = client.post("/add", data={"ticker": "AAPL"})
    assert resp.status_code == 400


def test_post_with_bad_csrf_token_rejected(client):
    # Prime a session csrf token by rendering a page, then send a wrong one.
    client.get("/")
    resp = client.post("/add", data={"ticker": "AAPL", "csrf": "wrong-token"})
    assert resp.status_code == 400


def test_peer_add_requires_login(client):
    # Community peers are signed-in only: a guest POST (valid csrf) bounces to
    # the login page instead of writing.
    with client.session_transaction() as sess:
        sess["csrf"] = "tok"
    resp = client.post("/stock/AAPL/peers/add",
                       data={"peer": "DELL", "csrf": "tok"})
    assert resp.status_code in (302, 303)
    assert "/login" in resp.headers["Location"]


def test_peer_remove_requires_login(client):
    with client.session_transaction() as sess:
        sess["csrf"] = "tok"
    resp = client.post("/stock/AAPL/peers/remove",
                       data={"peer": "DELL", "csrf": "tok"})
    assert resp.status_code in (302, 303)
    assert "/login" in resp.headers["Location"]


def test_notes_csv_requires_login(client):
    resp = client.get("/notes.csv")
    assert resp.status_code in (302, 303)
    assert "/login" in resp.headers["Location"]


def test_compare_needs_two(client):
    # Empty test DB: any ticker set resolves to <2 real cols -> redirect home.
    assert client.get("/compare?t=AAPL").status_code in (302, 303)
    assert client.get("/compare?t=").status_code in (302, 303)


# --- Price alerts (spec 08) -------------------------------------------------
# The suite has no authed-login fixture, so the route tests only cover the
# guest-guard path; check_alerts (the nightly logic) is unit-tested below
# against a fake conn, which is where the real behaviour lives.

def test_add_alert_requires_login(client):
    with client.session_transaction() as sess:
        sess["csrf"] = "tok"
    resp = client.post("/stock/AAPL/alerts",
                       data={"direction": "above", "threshold": "200", "csrf": "tok"})
    assert resp.status_code in (302, 303)
    assert "/login" in resp.headers["Location"]


def test_delete_alert_requires_login(client):
    with client.session_transaction() as sess:
        sess["csrf"] = "tok"
    resp = client.post("/alerts/1/delete", data={"csrf": "tok"})
    assert resp.status_code in (302, 303)
    assert "/login" in resp.headers["Location"]


# --- Portfolio holdings (spec 11) -------------------------------------------
def test_save_holdings_requires_login(client):
    with client.session_transaction() as sess:
        sess["csrf"] = "tok"
    resp = client.post("/watchlist/AAPL/holdings",
                       data={"qty": "10", "buy_price": "150", "csrf": "tok"})
    assert resp.status_code in (302, 303)
    assert "/login" in resp.headers["Location"]


class _FakeConn:
    """Minimal conn: one canned SELECT result, records UPDATE calls."""
    def __init__(self, rows):
        self._rows = rows
        self.updates = []

    def execute(self, sql, params=()):
        if sql.strip().upper().startswith("SELECT"):
            self._last = self._rows
        else:
            self.updates.append((sql, params))
            self._last = []
        return self

    def fetchall(self):
        return self._last


def _alert_row(**kw):
    row = {"id": 1, "ticker": "AAPL", "direction": "above", "threshold": 200.0,
           "email": "a@example.com", "name": "Apple", "currency": "USD",
           "price": 210.0}
    row.update(kw)
    return row


def test_check_alerts_fires_and_marks_when_email_sent(monkeypatch):
    import refresh, mailer
    sent = []
    monkeypatch.setattr(mailer, "send", lambda *a, **k: sent.append(a) or True)
    conn = _FakeConn([_alert_row(direction="above", threshold=200, price=210)])
    refresh.check_alerts(conn)
    assert len(sent) == 1
    assert conn.updates and "triggered_at" in conn.updates[0][0]


def test_check_alerts_below_direction(monkeypatch):
    import refresh, mailer
    monkeypatch.setattr(mailer, "send", lambda *a, **k: True)
    conn = _FakeConn([_alert_row(direction="below", threshold=250, price=210)])
    refresh.check_alerts(conn)
    assert conn.updates  # 210 <= 250 -> hit


def test_check_alerts_no_hit_stays_armed(monkeypatch):
    import refresh, mailer
    monkeypatch.setattr(mailer, "send", lambda *a, **k: True)
    conn = _FakeConn([_alert_row(direction="above", threshold=250, price=210)])
    refresh.check_alerts(conn)
    assert not conn.updates  # 210 < 250 -> no fire


def test_check_alerts_stays_armed_when_email_unset(monkeypatch):
    import refresh, mailer
    monkeypatch.setattr(mailer, "send", lambda *a, **k: False)  # SMTP unset
    conn = _FakeConn([_alert_row(direction="above", threshold=200, price=210)])
    refresh.check_alerts(conn)
    assert not conn.updates  # hit, but not marked because email didn't send


def test_check_alerts_skips_null_price(monkeypatch):
    import refresh, mailer
    sent = []
    monkeypatch.setattr(mailer, "send", lambda *a, **k: sent.append(1) or True)
    conn = _FakeConn([_alert_row(price=None)])
    refresh.check_alerts(conn)
    assert not sent and not conn.updates
