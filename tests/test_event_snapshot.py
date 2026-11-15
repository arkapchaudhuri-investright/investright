"""Event snapshot — the deep-dive's (and landing page's) lead widget.

The promises under test are the honesty ones: every figure is arithmetic on
saved rows, the event only chooses a lean, and nothing the LLM writes can put a
number or advice on the page that its source headline didn't carry.
"""
from datetime import date

import pytest

import event_snapshot as es

TODAY = date(2026, 11, 15)
DCF = {"fair_value": 100.0, "growth_used": 0.08, "discount_rate": 0.09,
       "terminal_growth": 0.025}
ITEMS = [{"title": "Italy fines Meta $880 million over accessing users' photos",
          "publisher": "CBS News", "url": "https://x.test/1",
          "published_at": "2026-11-14T10:00:00+00:00"},
         {"title": "5 stocks to buy now", "publisher": "Blog", "url": "https://x.test/2",
          "published_at": "2026-11-13T10:00:00+00:00"}]


# --- numbers ---------------------------------------------------------------
def test_base_scenario_is_the_fair_value_card():
    scen = {s["key"]: s for s in es.scenarios(DCF)}
    assert scen["base"]["mid"] == pytest.approx(100.0)
    # ordered: stress < bear < base < bull, and a higher discount rate is lower
    assert (scen["stress"]["mid"] < scen["bear"]["mid"] < scen["base"]["mid"]
            < scen["bull"]["mid"])
    assert scen["base"]["lo"] < scen["base"]["mid"] < scen["base"]["hi"]


def test_scenarios_none_without_a_usable_dcf():
    assert es.scenarios(None) is None
    assert es.scenarios({**DCF, "fair_value": None}) is None
    assert es.scenarios({**DCF, "discount_rate": 0.03}) is None   # band ≤ terminal


def test_weighted_mixes_are_plain_weighted_averages():
    scen = es.scenarios(DCF)
    by = {s["key"]: s for s in scen}
    rows = {r["key"]: r for r in es.weighted(scen)}
    assert rows["even"]["mid"] == pytest.approx(
        .25 * by["bull"]["mid"] + .50 * by["base"]["mid"] + .25 * by["bear"]["mid"])
    assert rows["bear"]["mid"] < rows["even"]["mid"] < rows["bull"]["mid"]
    assert [r["weights"] for r in es.weighted(scen)] == ["40/45/15", "25/50/25", "15/45/40"]


def test_peg_needs_positive_growing_earnings():
    assert es.peg(20.0, 10.0) == pytest.approx(0.10)     # 100% implied growth
    assert es.peg(10.0, 12.0) is None                    # earnings expected to fall
    assert es.peg(None, 10.0) is None and es.peg(20.0, -5.0) is None


def test_formatting_compacts_large_figures():
    assert es.fmt_range(145, 175, "USD") == "~$145–175"
    assert es.fmt_range(166, 166.3, "USD") == "~$166"
    assert es.fmt_range(19469, 21119, "INR") == "~₹19.5K–21.1K"
    assert es._x(15.02) == "~15x" and es._x(10.27) == "~10.3x" and es._x(22.4) == "~22x"


# --- events ----------------------------------------------------------------
def test_triggers_fire_on_their_own_data_most_recent_first():
    snap = {"change_pct": -6.2, "price": 100, "wk52_high": 150, "wk52_low": 60}
    got = es.triggers(snap, next_earnings="2026-11-17", last_earnings="2026-11-12",
                      react_pct=-4.0, today=TODAY)
    assert [t["kind"] for t in got] == ["move", "earnings_reported", "earnings_ahead"]
    assert got[0]["direction"] == "negative" and "6.2% drop" in got[0]["lead"]
    assert got[1]["direction"] == "negative" and got[1]["lead"] == "After earnings 3 days ago"
    assert got[2]["direction"] == "neutral" and got[2]["lead"] == "Ahead of earnings in 2 days"


def test_triggers_quiet_stock_has_none():
    snap = {"change_pct": 0.4, "price": 100, "wk52_high": 150, "wk52_low": 60}
    assert es.triggers(snap, "2027-02-01", "2026-08-01", None, TODAY) == []
    assert es.triggers(None, None, None, None, TODAY) == []


def test_52_week_extremes():
    hi = es.triggers({"price": 149.5, "wk52_high": 150, "wk52_low": 60}, today=TODAY)
    lo = es.triggers({"price": 60.2, "wk52_high": 150, "wk52_low": 60}, today=TODAY)
    assert hi[0]["kind"] == "high52" and hi[0]["direction"] == "positive"
    assert lo[0]["kind"] == "low52" and lo[0]["direction"] == "negative"


def test_live_events_offers_trigger_and_fresh_headline_then_baseline():
    ev = {"label": "the $880 million fine", "direction": "negative", "why": "A fine.",
          "src_title": "t", "src_url": "u", "src_publisher": "p",
          "src_published": "2026-11-14T10:00:00+00:00", "last_earnings": None}
    got = es.live_events({"change_pct": 0.1}, "2026-11-17", ev, today=TODAY)
    assert [e["kind"] for e in got] == ["earnings_ahead", "news"]
    assert got[1]["ai"] is True and got[1]["lead"] == "After the $880 million fine"
    # a stale read is no longer "latest"; with nothing else, an honest baseline
    old = {**ev, "src_published": "2026-10-01T10:00:00+00:00"}
    assert [e["kind"] for e in es.live_events({}, None, old, today=TODAY)] == ["baseline"]


# --- the AI's reply is validated against its source ------------------------
def _reply(**kw):
    import json
    base = {"event": "the $880 million fine", "material": True,
            "direction": "negative", "source": 1,
            "why": "Italy fined Meta $880 million over accessing users' photos."}
    base.update(kw)
    return json.dumps(base)


def test_parse_ai_accepts_a_grounded_material_event():
    got = es.parse_ai("```json\n" + _reply() + "\n```", ITEMS)
    assert got["label"] == "the $880 million fine" and got["direction"] == "negative"
    assert got["src"]["url"] == "https://x.test/1"


@pytest.mark.parametrize("reply", [
    '{"event": null}',                                   # nothing qualifies
    _reply(material=False),                              # model rates it immaterial
    _reply(why="Italy fined Meta $900 million."),        # a number not in the headline
    _reply(why="Investors should sell after the fine."), # advice
    _reply(source=7), _reply(source=0),                  # a source that doesn't exist
    _reply(direction="terrible"),
    "the model rambled and returned no JSON",
])
def test_parse_ai_rejects_anything_ungrounded(reply):
    assert es.parse_ai(reply, ITEMS) is None


def test_parse_ai_tidies_narration():
    got = es.parse_ai(_reply(event="After the fine.", why="The headline reports that "
                             "Italy fined Meta over accessing users' photos"), ITEMS)
    assert got["label"] == "the fine"
    assert got["why"] == "Italy fined Meta over accessing users' photos."


# --- view ------------------------------------------------------------------
RAW = {"fwd": {"forward_pe": 18.0, "target_low": 90.0, "target_high": 160.0,
               "fetched_at": "2026-11-15T09:00:00"},
       "ev": None, "pe": 27.0, "react_pct": None,
       "peers": [{"ticker": "PEER", "forward_pe": 24.0, "pe": 30.0}]}
SNAP = {"price": 120.0, "change_pct": 0.3, "wk52_high": 200.0, "wk52_low": 80.0}


def test_view_full_card():
    v = es.view("ACME", RAW, SNAP, None, DCF, 1.0, "USD", today=TODAY)
    assert v["event"]["kind"] == "baseline" and v["lean"] == "Balanced"
    assert v["headline"].startswith("No single event this week: ~$")
    assert "cheapest of its peers" in v["deck"]
    assert [r["label"] for r in v["range"]["rows"]] == [
        "Stress", "Bear", "Base", "Bull", "Analyst targets", "Weighted"]
    assert sum(r["lean"] for r in v["mix"]["rows"]) == 1
    assert all(0 <= r["left"] <= 100 and r["left"] + r["width"] <= 101
               for r in v["range"]["rows"])
    assert 0 <= v["range"]["price_left"] <= 100
    assert v["events"] == []                             # one event → no chip row


def test_view_event_only_changes_the_lean_never_a_figure():
    ev = {"label": "the fine", "direction": "negative", "why": "A fine.",
          "src_published": "2026-11-14T00:00:00+00:00"}
    snap = {**SNAP, "change_pct": 7.0}                   # a jump AND a negative headline
    a = es.view("ACME", {**RAW, "ev": ev}, snap, None, DCF, 1.0, "USD", today=TODAY)
    b = es.view("ACME", {**RAW, "ev": ev}, snap, None, DCF, 1.0, "USD",
                selected="news", today=TODAY)
    assert a["event"]["kind"] == "move" and a["lean"] == "Bull-leaning"
    assert b["event"]["kind"] == "news" and b["lean"] == "Bear-leaning"
    assert [e["kind"] for e in a["events"]] == ["move", "news"]
    strip = lambda v: [(r["mid"], r["span"]) for r in v["mix"]["rows"]]
    assert strip(a) == strip(b) and a["stats"] == b["stats"]
    # an unknown ?event= falls back to the default rather than erroring
    c = es.view("ACME", {**RAW, "ev": ev}, snap, None, DCF, 1.0, "USD",
                selected="bogus", today=TODAY)
    assert c["event"]["kind"] == "move"


def test_view_degrades_honestly():
    no_dcf = es.view("ACME", RAW, SNAP, None, None, 1.0, "USD", today=TODAY)
    assert no_dcf["mix"] is None and "analyst targets span" in no_dcf["headline"]
    assert [r["label"] for r in no_dcf["range"]["rows"]] == ["Analyst targets"]
    assert es.view("ACME", {"fwd": None, "ev": None, "pe": None, "peers": []},
                   SNAP, None, None, 1.0, "USD", today=TODAY) is None


def test_view_converts_native_targets_to_display_currency():
    v = es.view("ACME", RAW, SNAP, None, None, 80.0, "INR", today=TODAY)
    assert v["range"]["rows"][0]["txt"] == "~₹7,200–12.8K"


# --- cron side (temp DB) ---------------------------------------------------
@pytest.fixture()
def seeded(app_module):
    import db
    with db.get_conn() as conn:
        conn.execute("DELETE FROM stocks WHERE ticker='EVT'")
        conn.execute("INSERT INTO stocks (ticker,name,exchange,sector,industry,currency,added_at) "
                     "VALUES ('EVT','Event Co','NMS','Tech','Software','USD','2026-01-01')")
        db.save_snapshot(conn, {"ticker": "EVT", "fetched_at": "2026-11-15T09:00:00",
                                "price": 120.0, "prev_close": 119.0, "change_pct": 0.8,
                                "pe": 27.0, "wk52_low": 80.0, "wk52_high": 200.0,
                                "forward_pe": 18.0, "target_low": 90.0, "target_high": 160.0})
        for i, it in enumerate(ITEMS):
            conn.execute("INSERT INTO news (ticker,published_at,title,publisher,url,fetched_at) "
                         "VALUES ('EVT',?,?,?,?,?)",
                         (date.today().isoformat() + f"T0{1 - i}:00:00+00:00", it["title"],
                          it["publisher"], it["url"], "now"))
    yield db
    with db.get_conn() as conn:
        conn.execute("DELETE FROM stocks WHERE ticker='EVT'")


def test_save_snapshot_keeps_last_good_forward_consensus(seeded):
    db = seeded
    with db.get_conn() as conn:
        # a quote that landed without `.info` must not blank the consensus
        db.save_snapshot(conn, {"ticker": "EVT", "fetched_at": "2026-11-16T09:00:00",
                                "price": 121.0, "prev_close": 120.0, "change_pct": 0.8})
        row = conn.execute("SELECT forward_pe FROM forward_est WHERE ticker='EVT'").fetchone()
    assert row["forward_pe"] == 18.0


def test_refresh_events_reads_once_then_costs_nothing(seeded):
    db = seeded
    calls = []

    def run(prompt):
        calls.append(prompt)
        return _reply(), "fake-model"

    assert es.refresh_events(db.get_conn, ["EVT"], run, sleep=lambda s: None).startswith("1 read")
    with db.get_conn() as conn:
        es.note_reported(conn, "EVT", "2026-11-12")      # must not wipe the read
        row = dict(conn.execute("SELECT * FROM event_snapshot WHERE ticker='EVT'").fetchone())
    assert row["label"] == "the $880 million fine" and row["last_earnings"] == "2026-11-12"
    assert row["model"] == "fake-model"
    # same headlines the next night → no second call
    assert "1 unchanged" in es.refresh_events(db.get_conn, ["EVT"], run, sleep=lambda s: None)
    assert len(calls) == 1


def test_refresh_events_survives_a_provider_outage(seeded):
    db = seeded

    def down(prompt):
        raise RuntimeError("503")

    status = es.refresh_events(db.get_conn, ["EVT"], down, sleep=lambda s: None)
    assert "1 failed" in status


def test_deep_dive_and_home_render_the_widget(seeded, client):
    r = client.get("/stock/EVT")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and 'id="snapshot"' in html
    assert "Forward P/E" in html and "Not investment advice" in html
    assert client.get("/stock/EVT?event=bogus").status_code == 200
    assert client.get("/").status_code == 200


def test_refresh_events_waits_out_one_rate_limit(seeded):
    db = seeded
    waits, calls = [], []

    def flaky(prompt):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("gemini: 429 Client Error: Too Many Requests")
        return _reply(), "fake-model"

    status = es.refresh_events(db.get_conn, ["EVT"], flaky, sleep=waits.append)
    assert status.startswith("1 read") and waits == [es.AI_BACKOFF]


def test_refresh_events_stops_when_quota_is_gone(seeded):
    db = seeded
    calls = []

    def exhausted(prompt):
        calls.append(1)
        raise RuntimeError("gemini: 429 Client Error: Too Many Requests")

    status = es.refresh_events(db.get_conn, ["EVT", "EVT"], exhausted, sleep=lambda s: None)
    assert "1 failed" in status and len(calls) == 2      # one retry, then stop
