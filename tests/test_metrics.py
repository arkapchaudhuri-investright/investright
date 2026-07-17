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


def test_income_flow_view_balances():
    # Revenue & Expenses widget: the flows must balance both splits.
    row = {"period_label": "FY2024", "revenue": 1000.0, "cost_of_rev": 600.0,
           "gross_profit": 400.0, "rd": 100.0, "sga": 80.0, "net_income": 150.0}
    v = metrics.income_flow_view(row)
    amt = {r["label"]: r["amount"] for r in v["rows"]}
    assert amt["Cost of sales"] + amt["Gross profit"] == 1000.0          # revenue split
    assert v["other"] == 400.0 - 150.0 - 100.0 - 80.0                    # balancing bucket = 70
    assert v["margin_pct"] == 15.0
    assert v["rows"][0]["label"] == "Revenue" and v["rows"][-1]["label"] == "Net income"


def test_income_flow_view_derives_gross_and_drops_absent_rd():
    # gross_profit inferred from cost; a missing R&D line is simply omitted.
    v = metrics.income_flow_view(
        {"revenue": 1000.0, "cost_of_rev": 700.0, "gross_profit": None,
         "rd": None, "sga": 50.0, "net_income": 200.0})
    assert v["gross_profit"] == 300.0
    assert not any(r["label"].startswith("Research") for r in v["rows"])


def test_income_flow_view_detailed_with_yoy():
    # Operating stage decomposes cleanly → detailed tree + margins; a prior
    # comparable period yields Y/Y deltas per line.
    cur = {"period": "FY2025", "ptype": "A", "revenue": 1000.0,
           "cost_of_rev": 600.0, "gross_profit": 400.0, "rd": 100.0,
           "sga": 80.0, "operating_inc": 220.0, "tax": 40.0,
           "net_income": 150.0}
    pri = {**cur, "period": "FY2024", "revenue": 900.0, "net_income": 120.0}
    v = metrics.income_flow_view(cur, pri)
    assert v["detailed"] and v["margins"] == {"gross": 40.0, "net": 15.0,
                                              "operating": 22.0}
    assert v["opex"] == 180.0                       # gross − operating
    assert v["int_other"] == 220.0 - 40.0 - 150.0   # operating − tax − net = 30
    labels = [r["label"] for r in v["rows"]]
    assert "Operating profit" in labels and "Tax" in labels
    rev_row = v["rows"][0]
    assert rev_row["yoy"] == round(100 * (1000 - 900) / 900, 1)
    net_row = v["rows"][-1]
    assert net_row["yoy"] == 25.0                   # 120 → 150


def test_income_flow_view_falls_back_when_nonop_income_dominates():
    # Net above operating−tax (big non-operating income) → the detailed tree
    # can't conserve flow honestly → legacy three-way split.
    v = metrics.income_flow_view(
        {"revenue": 1000.0, "cost_of_rev": 600.0, "gross_profit": 400.0,
         "rd": 0.0, "sga": 50.0, "operating_inc": 100.0, "tax": 20.0,
         "net_income": 300.0})
    assert v["detailed"] is False
    assert "expenses" in v and "other" in v


def test_income_sankey_detailed_tree():
    cur = {"period": "FY2025", "ptype": "A", "revenue": 1000.0,
           "cost_of_rev": 600.0, "gross_profit": 400.0, "rd": 100.0,
           "sga": 80.0, "operating_inc": 220.0, "tax": 40.0,
           "net_income": 150.0}
    sk = metrics.income_sankey(metrics.income_flow_view(cur))
    labels = {n["label"] for n in sk["nodes"]}
    assert {"Revenue", "Gross profit", "Cost of sales", "Operating profit",
            "Operating expenses", "Net profit", "Tax"} <= labels
    subs = {n["label"]: n["sub"] for n in sk["nodes"] if n["sub"]}
    assert subs.get("Operating profit") == "22% margin"


def test_exec_tiers_ranks_by_title():
    execs = [{"name": "A", "title": "CEO & Director"},
             {"name": "B", "title": "Senior VP & CFO"},
             {"name": "C", "title": "Director of Investor Relations"},
             {"name": "D", "title": "Chairman & MD"},
             {"name": "E", "title": None}]
    tiers = metrics.exec_tiers(execs)
    assert [e["name"] for e in tiers[0]] == ["A", "D"]     # CEO + Chairman/MD
    assert [e["name"] for e in tiers[1]] == ["B"]           # C-suite
    assert [e["name"] for e in tiers[2]] == ["C", "E"]      # everyone else
    assert metrics.exec_tiers([]) == []                      # empty tiers dropped


def test_initials_strips_honorifics():
    assert metrics.initials("Mr. Timothy D. Cook") == "TC"
    assert metrics.initials("Ms. Deirdre  O'Brien") == "DO"
    assert metrics.initials("Suhasini Chandramouli") == "SC"
    assert metrics.initials("") == "?"


def test_income_sankey_shape_and_none_safe():
    view = metrics.income_flow_view(
        {"revenue": 1000.0, "cost_of_rev": 600.0, "gross_profit": 400.0,
         "rd": 100.0, "sga": 80.0, "net_income": 150.0})
    sk = metrics.income_sankey(view)
    labels = {n["label"] for n in sk["nodes"]}
    assert {"Revenue", "Gross profit", "Cost of sales", "Earnings", "Expenses"} <= labels
    assert len(sk["links"]) >= 4 and sk["vb"]
    assert metrics.income_sankey(None) is None


def test_income_flow_view_none_when_too_thin():
    assert metrics.income_flow_view(None) is None
    assert metrics.income_flow_view({"revenue": None, "net_income": 5.0}) is None
    assert metrics.income_flow_view({"revenue": 100.0, "net_income": None}) is None
    # revenue present but neither cost nor gross → can't split → None
    assert metrics.income_flow_view(
        {"revenue": 100.0, "net_income": 10.0, "cost_of_rev": None,
         "gross_profit": None}) is None


def test_trend_chart_benchmark_overlay():
    pts = [("2026-01-01", 100), ("2026-02-01", 110), ("2026-03-01", 120)]
    bench = [("2026-01-01", 1000), ("2026-02-01", 1030), ("2026-03-01", 1100)]
    t = metrics.trend_chart(pts, bench=bench)
    assert t["change_pct"] == 20.0 and t["bench_change_pct"] == 10.0
    assert t["bench_points"]
    # no bench -> unchanged shape, no overlay
    t0 = metrics.trend_chart(pts)
    assert t0["bench_points"] is None and t0["bench_change_pct"] is None
    assert (t0["lo"], t0["hi"]) == (100, 120)
