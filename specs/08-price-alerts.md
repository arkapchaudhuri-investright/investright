# Spec 08 — Price alerts ("email me when X crosses Y")

Read `specs/_CONTEXT.md` first. Branch: `feature/price-alerts`. Size: ~half day.

## Design
Signed-in users set one-shot alerts on a stock: direction (above/below) +
threshold in the stock's NATIVE currency. The nightly refresh checks the fresh
snapshot and emails via `mailer.send()`, then marks the alert triggered
(one-shot; user re-arms manually). If SMTP is unset (`mailer.enabled()` False),
alerts can still be created — the UI notes emails start once email is
configured; the nightly check simply skips sending but still marks nothing.

## Step 1 — schema (db.py)
Add to SCHEMA (CREATE IF NOT EXISTS — no migration needed for a new table):
```sql
CREATE TABLE IF NOT EXISTS user_alerts (
    id        INTEGER PRIMARY KEY,
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    ticker    TEXT NOT NULL REFERENCES stocks(ticker) ON DELETE CASCADE,
    direction TEXT NOT NULL CHECK (direction IN ('above','below')),
    threshold REAL NOT NULL,
    created_at   TEXT NOT NULL,
    triggered_at TEXT                -- NULL = armed
);
```

## Step 2 — routes (app.py)
- `POST /stock/<ticker>/alerts` (`@login_required`): form fields `direction`,
  `threshold` (float, > 0; reject garbage with a flash + redirect). Cap: max
  10 armed alerts per user (flash if over). Insert; flash "Alert set — Otto
  emails you when …"; redirect to `#alerts` (or the header).
- `POST /alerts/<int:alert_id>/delete` (`@login_required`): delete only when
  `user_id = current user`. Redirect back (form carries a `next` hidden or
  use request.referrer with a same-site check — simplest: redirect to
  `/account#alerts`).
- CSRF hidden input in both forms (global guard).

## Step 3 — UI
- Deep-dive header (`stock.html`, near the ★ watch form), signed-in only: a
  small `<details class="alert-set">` popover — summary `🔔 Alert`, inside:
  direction select (above/below), number input (step any, placeholder =
  current native price), Set button. Show the user's armed alerts for THIS
  ticker under it with a tiny ✕ delete form each.
- `/account` (templates/account.html): an "Alerts" section listing all the
  user's alerts (ticker link, condition, armed/triggered, ✕). Grep account.html
  for its section pattern and mirror it.
- If `not mailer_enabled` (pass `mailer.enabled()` into both templates), add
  `.asof` note: "Email isn't configured yet — alerts will start sending once
  it is."

## Step 4 — nightly check (refresh.py)
After snapshots are saved in `main()`:
```python
def check_alerts(conn):
    import mailer
    armed = conn.execute(
        "SELECT a.*, u.email, s.name, s.currency, n.price FROM user_alerts a "
        "JOIN users u ON u.id=a.user_id JOIN stocks s ON s.ticker=a.ticker "
        "JOIN snapshots n ON n.ticker=a.ticker WHERE a.triggered_at IS NULL").fetchall()
    for a in armed:
        hit = (a["price"] >= a["threshold"] if a["direction"] == "above"
               else a["price"] <= a["threshold"])
        if not hit or a["price"] is None:
            continue
        ok = mailer.send(a["email"], f"InvestRight alert: {a['ticker']}",
            f"{a['name']} ({a['ticker']}) closed at {a['price']:.2f} "
            f"{a['currency']} — your alert was {a['direction']} "
            f"{a['threshold']:.2f}.\n\nhttps://investright.us/stock/{a['ticker']}\n"
            "\nNot investment advice — Otto just crunches numbers.")
        if ok:
            conn.execute("UPDATE user_alerts SET triggered_at=datetime('now') "
                         "WHERE id=?", (a["id"],))
```
Only mark triggered when the email actually sent (send returns False when
SMTP unset → alert stays armed, fires the night email goes live).
Call it inside `main()` after snapshot save; wrap in try/except like siblings.

## Step 5 — tests (tests/test_routes.py)
- guest POST to `/stock/AAPL/alerts` → 302 to /login.
- (with a session-faked login if the suite has that pattern — check conftest;
  if not, skip authed tests and note it in the PR.)
- bad threshold ("abc") with auth → no 500 (302 with flash).

## Verify
```sh
.venv/bin/python -m pytest -q
.venv/bin/python -c "
import app; c=app.app.test_client()
h=c.get('/stock/AAPL').get_data(as_text=True)
# guest: no alert widget
assert 'alert-set' not in h; print('guest ok')"
```
Manual: log in via browser/test client, set an alert, see it on /account,
delete it. To test the email path locally you'd need SMTP_* in .env — skip;
verify check_alerts logic with a unit test using a fake conn instead.

## Ship
PR title: `Price alerts: one-shot above/below email alerts (nightly check)`
