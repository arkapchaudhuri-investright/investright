"""fetch.headlines — the replacement for yfinance's `.news`, which went
permanently empty around 2026-09-30 and took a month to notice.

The sources are network calls, so every test here stubs them: what matters is
the fallback order, the shape handed to save_news(), the quote-page reject and
the newest-first sort — not whether Yahoo is up today.
"""
import fetch


def _stub(monkeypatch, yahoo=None, google=None):
    """Replace both sources. A value of Exception means 'this source raises'."""
    def make(val):
        def fn(query, limit):
            if val is Exception:
                raise RuntimeError("source down")
            return list(val or [])
        return fn
    monkeypatch.setattr(fetch, "_yahoo_headlines", make(yahoo))
    monkeypatch.setattr(fetch, "_google_headlines", make(google))


def _item(title, when, url=None):
    return {"title": title, "url": url or f"https://example.com/{title}",
            "publisher": "ZZ Wire", "published_at": when}


def test_yahoo_wins_when_it_has_anything(monkeypatch):
    _stub(monkeypatch, yahoo=[_item("from yahoo", "2026-10-01T00:00:00+00:00")],
          google=[_item("from google", "2026-10-02T00:00:00+00:00")])
    got = fetch.headlines("AAPL", "Apple Inc.")
    assert [n["title"] for n in got] == ["from yahoo"]


def test_falls_through_to_google_when_yahoo_is_empty(monkeypatch):
    # The Indian case: Yahoo indexes almost no .NS listings, so an empty list
    # from it is the normal path, not an error.
    _stub(monkeypatch, yahoo=[], google=[_item("from google", "2026-10-02T00:00:00+00:00")])
    assert [n["title"] for n in fetch.headlines("ONGC.NS", "ONGC")] == ["from google"]


def test_falls_through_when_yahoo_raises(monkeypatch):
    _stub(monkeypatch, yahoo=Exception, google=[_item("from google", "2026-10-02T00:00:00+00:00")])
    assert len(fetch.headlines("ONGC.NS", "ONGC")) == 1


def test_both_sources_dead_returns_empty_not_an_exception(monkeypatch):
    # §8.0: no news is a thinner page, never a 500.
    _stub(monkeypatch, yahoo=Exception, google=Exception)
    assert fetch.headlines("AAPL", "Apple Inc.") == []


def test_newest_first_and_trimmed_to_limit(monkeypatch):
    # Google ranks by relevance, so without the sort `limit` keeps an arbitrary
    # subset rather than the freshest stories.
    _stub(monkeypatch, yahoo=[], google=[
        _item("older", "2026-08-01T00:00:00+00:00"),
        _item("newest", "2026-10-30T00:00:00+00:00"),
        _item("middle", "2026-09-15T00:00:00+00:00"),
    ])
    got = fetch.headlines("ONGC.NS", "ONGC", limit=2)
    assert [n["title"] for n in got] == ["newest", "middle"]


def test_undated_stories_sort_last_rather_than_first(monkeypatch):
    _stub(monkeypatch, yahoo=[], google=[
        _item("undated", None), _item("dated", "2026-10-30T00:00:00+00:00")])
    assert [n["title"] for n in fetch.headlines("X", "X")] == ["dated", "undated"]


def test_google_parser_skips_quote_pages_and_keeps_publisher():
    # Yahoo's own quote pages come back through Google's index looking like
    # stories; they're the stock page, not news.
    xml = b"""<?xml version="1.0"?><rss><channel>
      <item><title>Apple Inc. (AAPL) Stock Price, News, Quote &amp; History</title>
        <link>https://finance.yahoo.com/quote/AAPL</link>
        <pubDate>Fri, 30 Oct 2026 10:00:00 GMT</pubDate>
        <source>Yahoo Finance</source></item>
      <item><title>Real story about the company</title>
        <link>https://example.com/story</link>
        <pubDate>Fri, 30 Oct 2026 11:00:00 GMT</pubDate>
        <source>Livemint</source></item>
    </channel></rss>"""

    class _Resp:
        content = xml
        def raise_for_status(self): pass

    import requests
    real = requests.get
    requests.get = lambda *a, **k: _Resp()
    try:
        got = fetch._google_headlines("anything", 10)
    finally:
        requests.get = real
    assert [n["title"] for n in got] == ["Real story about the company"]
    assert got[0]["publisher"] == "Livemint"
    assert got[0]["published_at"].startswith("2026-10-30T11:00")
