"""Spec 15 — portfolio import parsers (pure) + the guest-guard on the routes.

parse_rows never raises and never hits the network; symbol resolution + DB writes
live in app.py and aren't exercised here (no authed fixture in the suite)."""
import portfolio_import as p


def test_paste_mixed_good_blank_garbage():
    rows = p.parse_rows("AAPL 10 150\nMSFT, 5, 300\n\nnope", "paste")
    assert rows == [
        {"raw_symbol": "AAPL", "isin": "", "qty": 10.0, "avg_price": 150.0, "derived_avg": False},
        {"raw_symbol": "MSFT", "isin": "", "qty": 5.0, "avg_price": 300.0, "derived_avg": False},
    ]


def test_paste_strips_currency_and_thousands_commas():
    rows = p.parse_rows("RELIANCE.NS 25 ₹2,450.50\nTSLA $3 $1,200", "paste")
    assert rows[0] == {"raw_symbol": "RELIANCE.NS", "isin": "", "qty": 25.0, "avg_price": 2450.50, "derived_avg": False}
    assert rows[1] == {"raw_symbol": "TSLA", "isin": "", "qty": 3.0, "avg_price": 1200.0, "derived_avg": False}


def test_paste_skips_header_line():
    rows = p.parse_rows("Symbol Qty Price\nAAPL 2 100", "paste")
    assert rows == [{"raw_symbol": "AAPL", "isin": "", "qty": 2.0, "avg_price": 100.0, "derived_avg": False}]


def test_generic_csv_keyword_guess():
    csv = "Ticker,Shares,Avg Cost\nAAPL,10,150\nMSFT,5,300\n"
    rows = p.parse_rows(csv, "generic")
    assert rows == [
        {"raw_symbol": "AAPL", "isin": "", "qty": 10.0, "avg_price": 150.0, "derived_avg": False},
        {"raw_symbol": "MSFT", "isin": "", "qty": 5.0, "avg_price": 300.0, "derived_avg": False},
    ]


def test_generic_csv_explicit_colmap():
    csv = "a,b,c,d\nX,AAPL,150,10\n"
    rows = p.parse_rows(csv, "generic",
                        colmap={"symbol": "b", "qty": "d", "price": "c"})
    assert rows == [{"raw_symbol": "AAPL", "isin": "", "qty": 10.0, "avg_price": 150.0, "derived_avg": False}]


def test_broker_zerodha_signature():
    csv = ("Instrument,Qty.,Avg. cost,LTP\n"
           "RELIANCE,25,2450.50,2500\nTCS,10,3200,3300\n")
    rows = p.parse_rows(csv, "zerodha")
    assert rows == [
        {"raw_symbol": "RELIANCE", "isin": "", "qty": 25.0, "avg_price": 2450.50, "derived_avg": False},
        {"raw_symbol": "TCS", "isin": "", "qty": 10.0, "avg_price": 3200.0, "derived_avg": False},
    ]


def test_broker_miss_falls_back_to_guess_not_error():
    # A Zerodha-labelled upload whose headers don't match falls through to the
    # generic keyword guesser rather than raising.
    csv = "Symbol,Quantity,Average Cost\nAAPL,4,120\n"
    rows = p.parse_rows(csv, "zerodha")
    assert rows == [{"raw_symbol": "AAPL", "isin": "", "qty": 4.0, "avg_price": 120.0, "derived_avg": False}]


def test_bad_numbers_kept_as_none_for_csv():
    # CSV rows with a symbol but unparseable numbers survive with None so the
    # confirm page can flag them (✕), rather than being dropped silently.
    csv = "Ticker,Shares,Avg Cost\nAAPL,,\n"
    rows = p.parse_rows(csv, "generic")
    assert rows == [{"raw_symbol": "AAPL", "isin": "", "qty": None, "avg_price": None, "derived_avg": False}]


def test_parse_rows_never_raises_on_junk():
    for junk in (None, "", b"\xff\xfe garbage", "\n\n\n", ",,,\n,,,"):
        assert isinstance(p.parse_rows(junk, "generic"), list)
        assert isinstance(p.parse_rows(junk, "paste"), list)


def test_import_preview_requires_login(client):
    with client.session_transaction() as sess:
        sess["csrf"] = "tok"
    resp = client.post("/portfolio/import",
                       data={"paste": "AAPL 10 150", "csrf": "tok"})
    assert resp.status_code in (302, 303)
    assert "/login" in resp.headers["Location"]


def test_import_confirm_requires_login(client):
    with client.session_transaction() as sess:
        sess["csrf"] = "tok"
    resp = client.post("/portfolio/import/confirm",
                       data={"ticker": "AAPL", "qty": "10", "avg_price": "150",
                             "csrf": "tok"})
    assert resp.status_code in (302, 303)
    assert "/login" in resp.headers["Location"]


# --- cost basis (item 3): a total-cost column, and "Price" never winning ----
def test_schwab_signature_derives_the_average_from_total_cost():
    # Schwab's positions export has no per-share average: only the position's
    # total Cost Basis. 4 shares for $600 is a $150 average.
    csv = ("Symbol,Description,Quantity,Price,Market Value,Cost Basis\n"
           "AAPL,APPLE INC,4,210,840,600\n")
    rows = p.parse_rows(csv, "schwab")
    assert rows == [{"raw_symbol": "AAPL", "isin": "", "qty": 4.0,
                     "avg_price": 150.0, "derived_avg": True}]


def test_a_live_price_column_no_longer_beats_the_cost_basis():
    # The bug behind four US holdings showing $0.00 P&L forever: "Price" sat to
    # the left of any cost figure, so column-order-wins read the live price as
    # the average. This is the same file with no broker chosen.
    csv = ("Symbol,Quantity,Price,Cost Basis\n"
           "ORCL,10,140,900\n")
    rows = p.parse_rows(csv, "generic")
    assert rows[0]["avg_price"] == 90.0
    assert rows[0]["derived_avg"] is True


def test_a_per_share_average_still_beats_a_total_cost():
    # Fidelity ships both. The per-share figure is the real average; the total
    # must not overwrite it, nor be claimed twice.
    csv = ("Symbol,Quantity,Cost Basis Per Share,Cost Basis\n"
           "MSFT,5,300,1500\n")
    rows = p.parse_rows(csv, "generic")
    assert rows[0]["avg_price"] == 300.0
    assert rows[0]["derived_avg"] is False


def test_bare_price_still_works_when_there_is_nothing_better():
    # A plain export with only a Price column keeps its old reading — the
    # fallback is narrowed, not removed.
    csv = "Ticker,Shares,Price\nTSLA,2,400\n"
    rows = p.parse_rows(csv, "generic")
    assert rows[0]["avg_price"] == 400.0


def test_total_cost_needs_a_quantity_to_divide_by():
    csv = "Symbol,Quantity,Cost Basis\nAAPL,,600\n"
    rows = p.parse_rows(csv, "generic")
    assert rows[0]["avg_price"] is None      # flagged on the confirm page, not guessed


# --- re-import (item 4): update in place, and offer to drop what's gone -----
NOW = "2026-01-01T00:00:00"


def _seed_book(conn):
    """A user holding ZZKEEP and ZZGONE, both with prices, plus an unheld
    ZZNEW the import can add. Returns the user id."""
    import db
    from werkzeug.security import generate_password_hash
    uid = db.create_user(conn, "zz_reimport@example.com",
                         generate_password_hash("x" * 10))
    for tk, name in (("ZZKEEP", "Keep Co"), ("ZZGONE", "Gone Co"), ("ZZNEW", "New Co")):
        conn.execute("INSERT OR REPLACE INTO stocks "
                     "(ticker,name,exchange,currency,added_at) VALUES (?,?,?,?,?)",
                     (tk, name, "NMS", "USD", NOW))
    db.upsert_holding(conn, uid, "ZZKEEP", 10, 100.0)
    db.upsert_holding(conn, uid, "ZZGONE", 5, 50.0)
    return uid


def _clean_book(conn, uid):
    for tk in ("ZZKEEP", "ZZGONE", "ZZNEW"):
        conn.execute("DELETE FROM holdings WHERE ticker=?", (tk,))
        conn.execute("DELETE FROM stocks WHERE ticker=?", (tk,))
    conn.execute("DELETE FROM users WHERE id=?", (uid,))


def _sign_in(client, conn, uid):
    import db
    with client.session_transaction() as sess:
        sess["uid"] = uid
        sess["stok"] = db.get_user_by_id(conn, uid)["session_token"]
        sess["csrf"] = "tok"


def test_reimport_preview_marks_changes_and_lists_what_is_missing(app_module, client):
    from db import get_conn
    with get_conn() as conn:
        uid = _seed_book(conn)
        _sign_in(client, conn, uid)
    try:
        # ZZKEEP at a new average, ZZNEW added, ZZGONE simply absent.
        resp = client.post("/portfolio/import", data={
            "csrf": "tok", "paste": "ZZKEEP 10 130\nZZNEW 3 20"})
        html = resp.get_data(as_text=True)
        assert resp.status_code == 200
        assert "updates" in html and "new" in html
        # The holding the file never mentions is surfaced, not silently kept —
        # this is how four US positions sat unnoticed beside the Indian book.
        assert "Not in this file" in html
        assert 'name="drop" value="ZZGONE"' in html
    finally:
        with get_conn() as conn:
            _clean_book(conn, uid)


def test_reimport_updates_in_place_and_drops_only_what_was_ticked(app_module, client):
    from db import get_conn
    with get_conn() as conn:
        uid = _seed_book(conn)
        _sign_in(client, conn, uid)
    try:
        resp = client.post("/portfolio/import/confirm", data={
            "csrf": "tok",
            "ticker": ["ZZKEEP", "ZZNEW"],
            "qty": ["10", "3"],
            "avg_price": ["130", "20"],
            "drop": ["ZZGONE"]})
        assert resp.status_code in (302, 303)
        with get_conn() as conn:
            book = {r["ticker"]: (r["qty"], r["avg_price"]) for r in conn.execute(
                "SELECT ticker, qty, avg_price FROM holdings WHERE user_id=?", (uid,))}
        assert book == {"ZZKEEP": (10.0, 130.0), "ZZNEW": (3.0, 20.0)}
    finally:
        with get_conn() as conn:
            _clean_book(conn, uid)


def test_an_unticked_missing_holding_survives_the_import(app_module, client):
    # A partial export — one account of several — must never empty the rest.
    from db import get_conn
    with get_conn() as conn:
        uid = _seed_book(conn)
        _sign_in(client, conn, uid)
    try:
        client.post("/portfolio/import/confirm", data={
            "csrf": "tok", "ticker": ["ZZKEEP"], "qty": ["10"], "avg_price": ["130"]})
        with get_conn() as conn:
            held = {r["ticker"] for r in conn.execute(
                "SELECT ticker FROM holdings WHERE user_id=?", (uid,))}
        assert held == {"ZZKEEP", "ZZGONE"}
    finally:
        with get_conn() as conn:
            _clean_book(conn, uid)
