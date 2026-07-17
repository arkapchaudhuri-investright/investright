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
