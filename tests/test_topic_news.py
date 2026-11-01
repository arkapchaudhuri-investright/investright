"""Topic news for the landing page carousel.

Per-ticker news answers "what happened to this company"; the carousel asks a
broader question no source we had could answer. Network stubbed throughout —
what's under test is the filtering, the replace-per-topic write and the
degradation when a feed goes quiet.
"""
import db
import fetch
import refresh


def _item(title, when, url=None):
    return {"title": title, "url": url or "https://example.com/" + title[:12],
            "publisher": "ZZ Wire", "published_at": when}


def test_unknown_topic_is_empty_not_an_error():
    assert fetch.topic_headlines("not-a-topic") == []


def test_non_latin_headlines_are_dropped(monkeypatch):
    # Google's index reaches well past English; a headline our readers can't
    # read is noise however good the source.
    monkeypatch.setattr(fetch, "_google_headlines", lambda q, n: [
        _item("Treasury yields fall after the Fed holds", "2026-10-31T00:00:00+00:00"),
        _item("港股异动 | 科技股全线上涨 腾讯领涨", "2026-10-31T00:00:00+00:00"),
    ])
    got = fetch.topic_headlines("bonds")
    assert [n["title"] for n in got] == ["Treasury yields fall after the Fed holds"]


def test_headlines_come_back_newest_first(monkeypatch):
    monkeypatch.setattr(fetch, "_google_headlines", lambda q, n: [
        _item("older story", "2026-09-01T00:00:00+00:00"),
        _item("newest story", "2026-10-31T00:00:00+00:00"),
    ])
    assert [n["title"] for n in fetch.topic_headlines("market")][0] == "newest story"


def test_a_dead_feed_raises_nothing(monkeypatch):
    def boom(q, n):
        raise RuntimeError("feed down")
    monkeypatch.setattr(fetch, "_google_headlines", boom)
    assert fetch.topic_headlines("funds") == []


def test_saving_a_topic_replaces_it(app_module):
    from db import get_conn
    with get_conn() as conn:
        db.save_topic_news(conn, "bonds", [_item("first", "2026-10-01T00:00:00+00:00")])
        db.save_topic_news(conn, "bonds", [_item("second", "2026-10-02T00:00:00+00:00")])
        db.save_topic_news(conn, "funds", [_item("other", "2026-10-02T00:00:00+00:00")])
    try:
        with get_conn() as conn:
            bonds = db.topic_news(conn, "bonds")
            assert [n["title"] for n in bonds] == ["second"], "stale stories should go"
            # Replacing one topic must not touch another.
            assert len(db.topic_news(conn, "funds")) == 1
    finally:
        with get_conn() as conn:
            conn.execute("DELETE FROM topic_news")


def test_a_quiet_feed_keeps_the_last_good_tab(app_module, monkeypatch):
    from db import get_conn
    with get_conn() as conn:
        db.save_topic_news(conn, "bonds", [_item("kept", "2026-10-01T00:00:00+00:00")])
    try:
        monkeypatch.setattr(fetch, "topic_headlines", lambda t, **k: [])
        assert refresh.refresh_topic_news() == []
        with get_conn() as conn:   # blanking a tab is worse than showing yesterday
            assert len(db.topic_news(conn, "bonds")) == 1
    finally:
        with get_conn() as conn:
            conn.execute("DELETE FROM topic_news")
