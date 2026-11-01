"""Flashed messages must never be silently dropped.

Rendering flashes is per-page, so a flash() set before a redirect to a page
that doesn't include _flashes.html simply vanished — feedback the user never
saw for an action they took. base.html now catches the orphans after the
content block, which relies on get_flashed_messages() draining on first call.
"""


def _flash(client, category, message):
    with client.session_transaction() as sess:
        sess["_flashes"] = [(category, message)]


def test_orphan_flash_surfaces_as_a_toast(client):
    # /team doesn't include _flashes.html, so nothing consumes the message
    # before base.html's net runs.
    _flash(client, "ok", "zz-orphan-message")
    html = client.get("/team").get_data(as_text=True)
    assert "zz-orphan-message" in html, "a flash on a page without the block was dropped"
    assert "flash-toast" in html


def test_a_page_that_shows_flashes_in_context_does_not_also_toast(client):
    # /login includes _flashes.html, so it drains them first and the net stays
    # empty — otherwise every message would render twice.
    _flash(client, "error", "zz-inline-message")
    html = client.get("/login").get_data(as_text=True)
    assert html.count("zz-inline-message") == 1
    assert "flash-toast" not in html


def test_errors_are_announced_assertively(client):
    _flash(client, "error", "zz-bad-thing")
    html = client.get("/login").get_data(as_text=True)
    assert 'role="alert"' in html and 'aria-live="assertive"' in html


def test_non_errors_use_a_polite_status_region(client):
    _flash(client, "ok", "zz-good-thing")
    html = client.get("/login").get_data(as_text=True)
    assert 'role="status"' in html and 'aria-live="polite"' in html
