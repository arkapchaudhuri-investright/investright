"""Stable-math tests for the scoring + DCF engine (metrics.py).

Golden values are pinned from the current implementation — they lock behaviour
so an accidental change to the DCF formula or score aggregation trips a test
instead of silently shipping. If the model genuinely changes, update the golden
numbers deliberately in the same PR.
"""
import pytest

import metrics

# Oldest→newest; FCF compounds 100 → 110 → 121 (a clean 10% CAGR over 2 years).
FUNDS = [
    {"fiscal_year": 2020, "fcf": 100.0, "net_income": 90.0},
    {"fiscal_year": 2021, "fcf": 110.0, "net_income": 95.0},
    {"fiscal_year": 2022, "fcf": 121.0, "net_income": 100.0},
]


def test_compute_dcf_golden():
    d = metrics.compute_dcf(FUNDS, price=50.0, shares=100.0,
                            growth=0.10, discount=0.09, terminal=0.025)
    assert d["fair_value"] == 26.19
    assert d["upside_pct"] == -47.6
    assert d["basis"] == "free cash flow"


def test_compute_dcf_infers_cagr():
    # With growth unset, it should infer the 10% CAGR and match the pinned run.
    d = metrics.compute_dcf(FUNDS, price=50.0, shares=100.0)
    assert d["growth_used"] == 0.10
    assert d["fair_value"] == 26.19


def test_compute_dcf_guards_bad_inputs():
    # discount must exceed terminal; shares/price must be positive.
    assert metrics.compute_dcf(FUNDS, price=50.0, shares=100.0,
                               discount=0.02, terminal=0.025) is None
    assert metrics.compute_dcf(FUNDS, price=50.0, shares=0) is None
    assert metrics.compute_dcf(FUNDS, price=0, shares=100.0) is None


def test_compute_dcf_needs_two_years():
    thin = [{"fiscal_year": 2022, "fcf": 121.0, "net_income": 100.0}]
    assert metrics.compute_dcf(thin, price=50.0, shares=100.0) is None


def test_dcf_upside_sign_tracks_price():
    cheap = metrics.compute_dcf(FUNDS, price=10.0, shares=100.0, growth=0.10)
    rich = metrics.compute_dcf(FUNDS, price=100.0, shares=100.0, growth=0.10)
    assert cheap["upside_pct"] > 0 > rich["upside_pct"]


def test_axis_and_overall_score_all_none_when_no_checks():
    scores = metrics.axis_scores([])
    assert set(scores) == {"value", "future", "past", "health", "dividend"}
    assert all(v is None for v in scores.values())
    assert metrics.overall_score(scores) is None


def test_overall_score_ignores_none_axes():
    # The live invariant: consumers of axis_scores() must survive a None axis.
    scores = {"value": 1.0, "future": None, "past": 0.5,
              "health": None, "dividend": 0.0}
    assert metrics.overall_score(scores) == pytest.approx((1.0 + 0.5 + 0.0) / 3)


def test_takeaway_survives_none_axes():
    # Regression for the shipped Ask-Otto 500: takeaway() must not choke when
    # every axis is None and dcf is None.
    line = metrics.takeaway("Foo Inc", None, metrics.axis_scores([]))
    assert isinstance(line, str) and line


def test_mood_thresholds():
    assert metrics.mood_for(None) == "neutral"
    assert metrics.mood_for(0.7) == "happy"
    assert metrics.mood_for(0.5) == "neutral"
    assert metrics.mood_for(0.2) == "concerned"
