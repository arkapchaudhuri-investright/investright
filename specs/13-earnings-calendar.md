# Spec 13 — Earnings calendar (next earnings date, /today + deep-dive)

Read `specs/_CONTEXT.md` first. Branch: `feature/earnings-calendar`. Size: ~half day.

## Design
Show each stock's NEXT earnings date: a chip on the deep-dive header
("Earnings in 12 days") and an "Earnings ahead" list on /today (tracked
stocks reporting in the next 14 days). Data: yfinance's calendar — flaky and
sometimes absent, so everything degrades to simply not showing.

## Step 1 — probe first (do this before writing code)
```sh
.venv/bin/python -c "
import yfinance as yf
for t in ('AAPL','MSFT','RELIANCE.NS'):
    try:
        cal = yf.Ticker(t).calendar
        print(t, type(cal), cal.get('Earnings Date') if isinstance(cal, dict) else cal)
    except Exception as e: print(t, 'ERR', e)"
```
yfinance returns a dict (newer versions) with `'Earnings Date':
[datetime.date, ...]` or a DataFrame (older). Write `fetch.next_earnings(symbol)`
handling BOTH shapes, returning `'YYYY-MM-DD'` (the earliest future date) or
None. Never raise.

## Step 2 — storage (db.py `_migrate`)
```python
scols = {r["name"] for r in conn.execute("PRAGMA table_info(stocks)")}
if "next_earnings" not in scols:
    conn.execute("ALTER TABLE stocks ADD COLUMN next_earnings TEXT")
```

## Step 3 — write path (refresh.py, cron-side)
In `save_deep()` (or beside the industry backfill there), set it:
```python
try:
    ne = fetch.next_earnings(ticker)
    conn.execute("UPDATE stocks SET next_earnings=? WHERE ticker=?", (ne, ticker))
except Exception:
    pass
```
(Always overwrite — a past date must refresh to the next one or NULL.)
Also add the same call in `app._ingest_stock()` so new searches get it
immediately (that's a POST path — writes allowed).

## Step 4 — deep-dive chip (stock.html header island)
Next to the exchange badge (`.xchg`), when `s['next_earnings']` is a FUTURE
date within 60 days:
```html
{% if earnings_days is not none %}
<span class="earn-chip" title="Next earnings report — {{ s['next_earnings'] }}">
  Earnings {{ 'today' if earnings_days == 0 else 'in %d days'|format(earnings_days) }}
</span>
{% endif %}
```
Compute `earnings_days` in the route (`(date.fromisoformat(ne) - date.today()).days`;
None when missing/past/`>60`). CSS: pill like `.xchg` but `border-color:
var(--accent); color: var(--accent); font-size: 11px;`.

## Step 5 — /today section
In `today()`: read tracked stocks with `next_earnings` in `[today, today+14d]`
(exclude exchange='INDEX'), order by date. Template: small card after Otto's
read — list rows `TICKER · Company — in N days (Mon DD)` linking to the deep
dive. Render nothing (no empty card) when the list is empty.

## Step 6 — tests
- `fetch.next_earnings` unit: feed it monkeypatched dict/DataFrame shapes →
  correct string/None (structure the function so the parse step is a pure
  helper you can test without network).

## Verify
```sh
.venv/bin/python -m pytest -q
# backfill a couple locally (script write, not GET):
.venv/bin/python -c "
import fetch, db
with db.get_conn() as conn:
    for t in ('AAPL','MSFT'):
        conn.execute('UPDATE stocks SET next_earnings=? WHERE ticker=?',
                     (fetch.next_earnings(t), t))
    conn.commit()"
.venv/bin/python -c "
import app; c=app.app.test_client()
h=c.get('/stock/AAPL').get_data(as_text=True); print('chip:', 'earn-chip' in h)"
```
(Chip may legitimately be absent if Yahoo has no upcoming date — check the DB
value before judging.)

## Ship
PR title: `Earnings calendar: next-report chip + two-week /today list`
