"""Tests for the earnings-calendar parse (spec 13).

`fetch.next_earnings` reads yfinance's flaky `.calendar`, which is a dict on
newer versions and a DataFrame on older ones. The parse step is a pure helper
so both shapes can be exercised without touching the network.
"""
from datetime import date, datetime, timedelta

import pytest

import fetch

TODAY = date(2026, 7, 17)


def test_earliest_future_picks_soonest_upcoming():
    dates = [date(2026, 8, 1), date(2026, 7, 20), date(2026, 9, 1)]
    assert fetch._earliest_future_date(dates, today=TODAY) == "2026-07-20"


def test_earliest_future_ignores_past_dates():
    dates = [date(2026, 1, 1), date(2026, 7, 5)]
    assert fetch._earliest_future_date(dates, today=TODAY) is None


def test_earliest_future_includes_today():
    assert fetch._earliest_future_date([TODAY], today=TODAY) == "2026-07-17"


def test_earliest_future_handles_datetimes_and_strings():
    dates = [datetime(2026, 8, 2, 9, 0), "2026-07-25"]
    assert fetch._earliest_future_date(dates, today=TODAY) == "2026-07-25"


def test_earliest_future_empty_or_none():
    assert fetch._earliest_future_date([], today=TODAY) is None
    assert fetch._earliest_future_date(None, today=TODAY) is None


class _FakeTicker:
    def __init__(self, cal):
        self._cal = cal

    @property
    def calendar(self):
        return self._cal


def test_next_earnings_dict_shape(monkeypatch):
    future = date.today() + timedelta(days=10)
    cal = {"Earnings Date": [future, future + timedelta(days=90)]}
    monkeypatch.setattr(fetch.yf, "Ticker", lambda s: _FakeTicker(cal))
    assert fetch.next_earnings("AAPL") == future.isoformat()


def test_next_earnings_dataframe_shape(monkeypatch):
    pd = pytest.importorskip("pandas")
    future = date.today() + timedelta(days=6)
    df = pd.DataFrame({"c0": [future]}, index=["Earnings Date"])
    monkeypatch.setattr(fetch.yf, "Ticker", lambda s: _FakeTicker(df))
    assert fetch.next_earnings("MSFT") == future.isoformat()


def test_next_earnings_missing_returns_none(monkeypatch):
    monkeypatch.setattr(fetch.yf, "Ticker", lambda s: _FakeTicker({}))
    assert fetch.next_earnings("XYZ") is None


def test_next_earnings_never_raises(monkeypatch):
    def boom(s):
        raise RuntimeError("yahoo down")
    monkeypatch.setattr(fetch.yf, "Ticker", boom)
    assert fetch.next_earnings("AAPL") is None
