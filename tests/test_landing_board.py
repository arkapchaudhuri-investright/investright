"""The landing page: eleven numbered sections (the "edition"), which folded
in the tiled board that replaced the search-only home page.

The board has to render for a guest with an empty database — a new deploy has
no movers, no topic news and no earnings until the first nightly — so the
empty states matter as much as the populated ones.
"""
import db

NOW = "2026-01-01T00:00:00+00:00"


def test_board_renders_for_a_guest_on_an_empty_database(client):
    html = client.get("/").get_data(as_text=True)
    assert html.count('class="ed-sec') == 11
    # Every section is reachable from the index.
    assert html.count("data-ed-link=") == 11
    # The sections are the tour now, so every feature must always be there.
    for feature in ("Today", "Portfolio", "Watchlist", "Strategies", "Ask Otto"):
        assert feature in html
    # Honest empty states rather than blank tiles.
    assert "No quotes saved yet" in html or "TOP GAINERS" in html.upper()


def test_the_retired_tour_overlay_is_gone(client):
    html = client.get("/").get_data(as_text=True)
    assert 'id="tour"' not in html
    assert "irTour" not in html
    assert "data-tour" not in html


def test_movers_render_and_link_through_analyze(app_module, client):
    # A mover may be an index member we've never ingested, so the row posts to
    # /analyze (which ingests) rather than linking to /stock (a read-only GET).
    from db import get_conn
    with get_conn() as conn:
        db.save_movers(conn, [
            {"ticker": "ZZWIN", "name": "Winner Co", "market": "US", "price": 11.0,
             "prev_close": 10.0, "change_pct": 10.0, "volume": 5.0, "fetched_at": NOW},
            {"ticker": "ZZLOSE", "name": "Loser Co", "market": "US", "price": 9.0,
             "prev_close": 10.0, "change_pct": -10.0, "volume": 9.0, "fetched_at": NOW}])
    try:
        html = client.get("/").get_data(as_text=True)
        assert "ZZWIN" in html and "+10.00%" in html
        assert "ZZLOSE" in html and "-10.00%" in html
        assert 'action="/analyze"' in html
    finally:
        with get_conn() as conn:
            conn.execute("DELETE FROM movers")


def test_news_tabs_render_every_topic_with_one_visible(app_module, client):
    from db import get_conn
    with get_conn() as conn:
        for topic in ("market", "bonds"):
            db.save_topic_news(conn, topic, [
                {"title": f"zz {topic} story", "url": f"https://example.com/{topic}",
                 "publisher": "ZZ Wire", "published_at": NOW}])
    try:
        html = client.get("/").get_data(as_text=True)
        assert "zz market story" in html and "zz bonds story" in html
        # Every rail ships; JS only toggles which shows, so no-JS sees the
        # first rather than nothing.
        assert html.count("data-nw-panel") >= 2
        assert html.count('aria-selected="true"') == 1
    finally:
        with get_conn() as conn:
            conn.execute("DELETE FROM topic_news")


def test_the_welcome_popup_is_gone(client):
    # The board is the onboarding now; a modal over it would hide the thing
    # it's introducing.
    html = client.get("/").get_data(as_text=True)
    assert "name-dialog" not in html
    assert "showModal" not in html
    assert "ir_welcomed" not in html


def test_the_greeting_still_renders_without_the_popup(client):
    # The popup was the only thing that collected a guest name; the
    # time-of-day greeting has to survive on its own.
    html = client.get("/").get_data(as_text=True)
    assert 'id="greet"' in html
    assert "Good morning" in html or "Good afternoon" in html or "Good evening" in html


def test_analysis_plates_fall_back_to_copy_without_a_featured_company(client):
    # An empty database has no DCF or health checks, so plates IV-VI must say
    # so rather than draw invented numbers.
    html = client.get("/").get_data(as_text=True)
    assert "Scores appear once a company has been analysed" in html
    assert "A fair value appears once a company has been analysed" in html


def test_the_portfolio_plate_is_labelled_an_illustration(client):
    # A guest has no book; the example donut must never pass for real data.
    html = client.get("/").get_data(as_text=True)
    assert "An example book, for illustration" in html
