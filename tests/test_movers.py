"""The index-constituent movers behind the landing page tiles.

Ranking the ~120 tickers people here track and calling it "market movers"
would be a lie, so these are real S&P 500 + NIFTY 50 members, quote-only.
The network is stubbed — what's under test is the ranking, the market filter
and the degradation, not whether Yahoo is up.
"""
import json

import db
import fetch
import refresh

NOW = "2026-01-01T00:00:00+00:00"


def _rows():
    return [
        {"ticker": "ZZUP", "name": "Up Co", "market": "US", "price": 11.0,
         "prev_close": 10.0, "change_pct": 10.0, "volume": 100.0, "fetched_at": NOW},
        {"ticker": "ZZDOWN", "name": "Down Co", "market": "US", "price": 9.0,
         "prev_close": 10.0, "change_pct": -10.0, "volume": 900.0, "fetched_at": NOW},
        {"ticker": "ZZIN.NS", "name": "India Co", "market": "IN", "price": 12.0,
         "prev_close": 10.0, "change_pct": 20.0, "volume": 50.0, "fetched_at": NOW},
        {"ticker": "ZZFLAT", "name": "Flat Co", "market": "US", "price": 10.0,
         "prev_close": 10.0, "change_pct": 0.0, "volume": 10.0, "fetched_at": NOW},
    ]


def test_ranking_filters_and_orders(app_module):
    from db import get_conn
    with get_conn() as conn:
        db.save_movers(conn, _rows())
    try:
        with get_conn() as conn:
            assert [r["ticker"] for r in db.top_movers(conn, "gainers", limit=5)] == \
                   ["ZZIN.NS", "ZZUP"]            # flat/negative excluded, biggest first
            assert [r["ticker"] for r in db.top_movers(conn, "losers", limit=5)] == ["ZZDOWN"]
            assert db.top_movers(conn, "actives", limit=1)[0]["ticker"] == "ZZDOWN"
            # Actives ranks on volume alone, so a faller can top it.
            assert db.top_movers(conn, "gainers", market="US", limit=5)[0]["ticker"] == "ZZUP"
    finally:
        with get_conn() as conn:
            conn.execute("DELETE FROM movers")


def test_save_movers_replaces_rather_than_accumulates(app_module):
    # A company leaving the index should leave the table.
    from db import get_conn
    with get_conn() as conn:
        db.save_movers(conn, _rows())
        db.save_movers(conn, _rows()[:1])
    try:
        with get_conn() as conn:
            assert conn.execute("SELECT COUNT(*) FROM movers").fetchone()[0] == 1
    finally:
        with get_conn() as conn:
            conn.execute("DELETE FROM movers")


def test_refresh_keeps_last_good_when_every_quote_fails(app_module, monkeypatch):
    from db import get_conn
    with get_conn() as conn:
        db.save_movers(conn, _rows())
    try:
        monkeypatch.setattr(fetch, "quotes_many", lambda syms, **k: {})
        assert refresh.refresh_movers() == 0
        with get_conn() as conn:          # the old rows survive a bad night
            assert conn.execute("SELECT COUNT(*) FROM movers").fetchone()[0] == 4
    finally:
        with get_conn() as conn:
            conn.execute("DELETE FROM movers")


def test_refresh_survives_a_missing_universe_file(app_module, monkeypatch, tmp_path):
    monkeypatch.setattr(refresh, "MOVERS_UNIVERSE", tmp_path / "nope.json")
    assert refresh.refresh_movers() == 0


def test_shipped_universe_is_sane():
    # The builder refuses to write a short list; this guards the file in git.
    members = json.loads((refresh.MOVERS_UNIVERSE).read_text())
    assert len(members) > 500
    assert {m[2] for m in members} == {"US", "IN"}
    assert all(m[0] and m[1] for m in members), "every member needs a symbol and a name"
    assert all(m[0].endswith(".NS") for m in members if m[2] == "IN")
    # The sector-column bug: names must not be duplicated across many rows.
    names = [m[1] for m in members]
    assert len(set(names)) > len(names) * 0.95
