"""Spec 15 — portfolio import parsers (pure) + the guest-guard on the routes.

parse_rows never raises and never hits the network; symbol resolution + DB writes
live in app.py and aren't exercised here (no authed fixture in the suite)."""
import portfolio_import as p


def test_paste_mixed_good_blank_garbage():
    rows = p.parse_rows("AAPL 10 150\nMSFT, 5, 300\n\nnope", "paste")
    assert rows == [
        {"raw_symbol": "AAPL", "qty": 10.0, "avg_price": 150.0},
        {"raw_symbol": "MSFT", "qty": 5.0, "avg_price": 300.0},
    ]


def test_paste_strips_currency_and_thousands_commas():
    rows = p.parse_rows("RELIANCE.NS 25 ₹2,450.50\nTSLA $3 $1,200", "paste")
    assert rows[0] == {"raw_symbol": "RELIANCE.NS", "qty": 25.0, "avg_price": 2450.50}
    assert rows[1] == {"raw_symbol": "TSLA", "qty": 3.0, "avg_price": 1200.0}


def test_paste_skips_header_line():
    rows = p.parse_rows("Symbol Qty Price\nAAPL 2 100", "paste")
    assert rows == [{"raw_symbol": "AAPL", "qty": 2.0, "avg_price": 100.0}]


def test_generic_csv_keyword_guess():
    csv = "Ticker,Shares,Avg Cost\nAAPL,10,150\nMSFT,5,300\n"
    rows = p.parse_rows(csv, "generic")
    assert rows == [
        {"raw_symbol": "AAPL", "qty": 10.0, "avg_price": 150.0},
        {"raw_symbol": "MSFT", "qty": 5.0, "avg_price": 300.0},
    ]


def test_generic_csv_explicit_colmap():
    csv = "a,b,c,d\nX,AAPL,150,10\n"
    rows = p.parse_rows(csv, "generic",
                        colmap={"symbol": "b", "qty": "d", "price": "c"})
    assert rows == [{"raw_symbol": "AAPL", "qty": 10.0, "avg_price": 150.0}]


def test_broker_zerodha_signature():
    csv = ("Instrument,Qty.,Avg. cost,LTP\n"
           "RELIANCE,25,2450.50,2500\nTCS,10,3200,3300\n")
    rows = p.parse_rows(csv, "zerodha")
    assert rows == [
        {"raw_symbol": "RELIANCE", "qty": 25.0, "avg_price": 2450.50},
        {"raw_symbol": "TCS", "qty": 10.0, "avg_price": 3200.0},
    ]


def test_broker_miss_falls_back_to_guess_not_error():
    # A Zerodha-labelled upload whose headers don't match falls through to the
    # generic keyword guesser rather than raising.
    csv = "Symbol,Quantity,Average Cost\nAAPL,4,120\n"
    rows = p.parse_rows(csv, "zerodha")
    assert rows == [{"raw_symbol": "AAPL", "qty": 4.0, "avg_price": 120.0}]


def test_bad_numbers_kept_as_none_for_csv():
    # CSV rows with a symbol but unparseable numbers survive with None so the
    # confirm page can flag them (✕), rather than being dropped silently.
    csv = "Ticker,Shares,Avg Cost\nAAPL,,\n"
    rows = p.parse_rows(csv, "generic")
    assert rows == [{"raw_symbol": "AAPL", "qty": None, "avg_price": None}]


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
