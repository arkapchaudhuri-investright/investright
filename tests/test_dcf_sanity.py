"""compute_dcf's guards against a cash-flow series it can't honestly value.

Anchoring on the latest year alone put BEL's fair value 97% below the market
(its FCF ran ₹40bn → -₹4.5bn → ₹5.6bn) and BPCL's 572% above it, and both
reached the weekly note as prose. These cover the normalisation and the
output bounds that stop that.
"""
import json

import metrics


def _funds(fcfs, shares=1000.0, start=2022):
    """Minimal fundamentals rows — compute_dcf only reads fcf and fiscal_year."""
    return [{"fiscal_year": start + i, "fcf": v, "net_income": v, "shares": shares}
            for i, v in enumerate(fcfs)]


def _dcf(fcfs, price, shares=1000.0):
    return metrics.compute_dcf(_funds(fcfs, shares), price, shares)


def test_steady_series_keeps_the_latest_year_as_its_base():
    # Normalising a smoothly growing company would understate it, so the latest
    # figure stays the anchor when the recent years agree.
    out = _dcf([100.0, 110.0, 120.0, 130.0], price=2.72)
    assert out is not None
    assumptions = json.loads(out["assumptions_json"])
    assert assumptions["normalised"] is False
    assert assumptions["base_flow"] == 130


def test_a_collapsed_latest_year_is_normalised_away():
    # BEL's shape: a big year, a negative year, then a small one.
    out = _dcf([400.0, 400.0, -45.0, 56.0], price=0.40)
    assert out is not None
    assumptions = json.loads(out["assumptions_json"])
    assert assumptions["normalised"] is True
    # median of the last three (-45, 56, 400) is 56 — not the -45 outlier, and
    # not the 400 that would flatter it.
    assert assumptions["base_flow"] == 56


def test_a_spike_in_the_latest_year_is_normalised_too():
    out = _dcf([100.0, 100.0, 100.0, 900.0], price=1.41)
    assert json.loads(out["assumptions_json"])["normalised"] is True
    assert json.loads(out["assumptions_json"])["base_flow"] == 100


def test_an_all_negative_series_is_declined_outright():
    assert _dcf([-10.0, -20.0, -15.0], price=5.0) is None


def test_fair_value_far_below_price_is_declined_not_published():
    # The high-multiple-grower case: a trailing-FCF model lands near 0.08x,
    # which would render as "trading 1,100% above fair value".
    assert _dcf([100.0, 105.0, 110.0], price=10_000.0) is None


def test_fair_value_far_above_price_is_declined_not_published():
    # The ONGC/BPCL case — nearly always a bad input rather than a cheap stock.
    assert _dcf([100.0, 105.0, 110.0], price=0.05) is None


def test_a_plausible_valuation_still_comes_through():
    out = _dcf([100.0, 110.0, 120.0], price=2.55)
    assert out is not None
    assert metrics.DCF_SANITY_LO <= out["fair_value"] / 2.55 <= metrics.DCF_SANITY_HI
    assert out["basis"] == "free cash flow"


def test_growth_is_measured_against_the_base_actually_discounted():
    # When the base is normalised, the growth rate has to describe the same
    # series — otherwise the rate and the starting figure disagree.
    out = _dcf([100.0, 100.0, 100.0, 900.0], price=1.41)
    assert out["growth_used"] == 0.0        # flat 100 → 100, not 100 → 900
