"""GET /portfolio/<ticker>/detail — the per-holding analysis + news fragment.

Runs against the session temp DB (the app_module fixture repoints db.DB_PATH).
Each test seeds its own ZZ-prefixed rows and deletes them in a finally, so the
shared DB stays clean for the smoke tests. There's no login fixture, so the
signed-in cases set uid/stok on the client session by hand.
"""
from werkzeug.security import generate_password_hash

NOW = "2026-01-01T00:00:00"


def _seed(conn):
    """A user holding ZZDEEP, with a snapshot, a DCF, health checks and one
    headline — i.e. everything the fragment can render. Returns the user id."""
    import db
    uid = db.create_user(conn, "zz_detail@example.com",
                         generate_password_hash("x" * 10), name="DetailQA")
    conn.execute("INSERT OR REPLACE INTO stocks "
                 "(ticker,name,exchange,sector,industry,currency,added_at) "
                 "VALUES (?,?,?,?,?,?,?)",
                 ("ZZDEEP", "Deep Co", "NMS", "Tech", "Software", "USD", NOW))
    conn.execute("INSERT OR REPLACE INTO stocks "
                 "(ticker,name,exchange,currency,added_at) VALUES (?,?,?,?,?)",
                 ("ZZAWAY", "Unheld Co", "NMS", "USD", NOW))
    conn.execute("INSERT OR REPLACE INTO snapshots "
                 "(ticker,price,prev_close,change_pct,market_cap,pe,div_yield,fetched_at) "
                 "VALUES (?,?,?,?,?,?,?,?)",
                 ("ZZDEEP", 120.0, 118.0, 1.7, 5.0e9, 18.5, 1.25, NOW))
    conn.execute("INSERT OR REPLACE INTO dcf "
                 "(ticker,fair_value,upside_pct,computed_at) VALUES (?,?,?,?)",
                 ("ZZDEEP", 150.0, 25.0, NOW))
    for cid, axis, passed in (("v1", "value", 1), ("v2", "value", 0),
                              ("h1", "health", 1)):
        conn.execute("INSERT OR REPLACE INTO health_checks "
                     "(ticker,axis,check_id,label,passed,computed_at) VALUES (?,?,?,?,?,?)",
                     ("ZZDEEP", axis, cid, cid.upper(), passed, NOW))
    conn.execute("INSERT OR REPLACE INTO news "
                 "(ticker,published_at,title,publisher,url,fetched_at) VALUES (?,?,?,?,?,?)",
                 ("ZZDEEP", NOW, "Deep Co beats on revenue", "ZZ Wire",
                  "https://example.com/zzdeep", NOW))
    db.upsert_holding(conn, uid, "ZZDEEP", 10, 100.0)
    return uid


def _cleanup(conn, uid):
    for tk in ("ZZDEEP", "ZZAWAY"):
        for tbl in ("holdings", "news", "health_checks", "dcf", "snapshots", "stocks"):
            conn.execute(f"DELETE FROM {tbl} WHERE ticker=?", (tk,))
    conn.execute("DELETE FROM users WHERE id=?", (uid,))


def _sign_in(client, conn, uid):
    import db
    user = db.get_user_by_id(conn, uid)
    with client.session_transaction() as sess:
        sess["uid"] = uid
        sess["stok"] = user["session_token"]


def test_detail_requires_login(client):
    resp = client.get("/portfolio/AAPL/detail")
    assert resp.status_code in (302, 303)
    assert "/login" in resp.headers["Location"]


def test_detail_renders_analysis_and_news(app_module, client):
    from db import get_conn
    # Seed and commit BEFORE the request: the view opens its own connection and
    # can't see rows still sitting in this one's open transaction.
    with get_conn() as conn:
        uid = _seed(conn)
        _sign_in(client, conn, uid)
    try:
        resp = client.get("/portfolio/ZZDEEP/detail")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        # A fragment, not a page — no layout chrome to inject into a row.
        assert "<html" not in html.lower()
        assert "undervalued" in html                  # metrics.takeaway read
        assert "$150.00" in html and "+25%" in html   # fair value + upside
        assert "1/2" in html                          # value axis pass count
        assert "Deep Co beats on revenue" in html     # the headline
        assert "Not investment advice" in html        # honest copy (§1)
    finally:
        with get_conn() as conn:
            _cleanup(conn, uid)


def test_detail_404s_for_a_ticker_you_do_not_hold(app_module, client):
    # The fragment exposes one user's position context, so it's scoped to the
    # caller's own holdings rather than to any known stock.
    from db import get_conn
    with get_conn() as conn:
        uid = _seed(conn)
        _sign_in(client, conn, uid)
    try:
        assert client.get("/portfolio/ZZAWAY/detail").status_code == 404
    finally:
        with get_conn() as conn:
            _cleanup(conn, uid)
