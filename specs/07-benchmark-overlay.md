# Spec 07 — Benchmark overlay on the price trend (vs S&P 500 / NIFTY 50)

Read `specs/_CONTEXT.md` first. Branch: `feature/benchmark-overlay`. Size: ~half day.

## Problem
The trend chart shows the stock alone. Overlay the relevant index — dashed,
muted — so "did it beat the market?" is answered at a glance. US stocks →
`^GSPC` (S&P 500); `.NS`/`.BO` → `^NSEI` (NIFTY 50).

## Data plumbing (indices are pseudo-stocks)
`price_history.ticker` FKs `stocks(ticker)`, and `/today`'s screener iterates
ALL stocks — so index rows must exist in `stocks` but be excluded everywhere
user-facing:
1. `refresh.py`: at the top of `main()`'s per-night work, ensure rows
   `('^GSPC','S&P 500','INDEX',...)` and `('^NSEI','NIFTY 50','INDEX',...)`
   exist in `stocks` (INSERT OR IGNORE, exchange='INDEX', sector/industry '',
   currency USD/INR), then fetch + `save_price_history` for both via
   `fetch.price_history_resilient(sym, "max")` (first run backfills, later
   runs upsert).
2. Exclusions — grep and patch EVERY user-facing enumeration of `stocks`:
   - `run_screener()` in refresh.py → `WHERE exchange != 'INDEX'`
   - same-industry peer fill in `stock()` (industry='' already excludes, but
     add the guard anyway)
   - any `SELECT ticker FROM stocks` loops in manage.py enrichment commands →
     skip INDEX rows.
   - `/stock/^GSPC` will now resolve — acceptable (it 404s today only because
     the row doesn't exist; after this it renders a sparse page. If trivial,
     `abort(404)` in `stock()` when `s['exchange']=='INDEX'`).

## Chart
3. `metrics.trend_chart(points, bench=None)`: accept a second series
   `[(date, close)]`. Normalise BOTH to % change from each one's first close in
   the window, then scale to the same y-range. Return existing keys plus
   `bench_points` (polyline string) and `bench_change_pct`. Bench maps by
   date: for each stock date use the most recent bench close ≤ that date
   (indices and stocks share most trading days; a dict lookup with fallback
   to previous is enough).
4. `stock()` route: pick bench symbol by market
   (`'^NSEI' if ticker.endswith(('.NS','.BO')) else '^GSPC'`), load its
   history rows in the same window, pass to `trend_chart`. Convert bench to
   display ccy? NO — normalised %, currency-invariant. Also thread bench into
   `trend.json` (grep `def trend_json`) so range switching keeps the overlay.
5. Template `stock.html` (#trend section): after the main polyline add
```html
{% if trend.bench_points %}
<polyline points="{{ trend.bench_points }}" fill="none" stroke="var(--muted)"
          stroke-width="1.2" stroke-dasharray="4,3" opacity=".7"
          vector-effect="non-scaling-stroke"/>
{% endif %}
```
   and in the verdict line add
   `vs {{ bench_name }} {{ '%+.1f'|format(trend.bench_change_pct) }}%`.
   Update the client-side redraw JS in stock.html (it rebuilds the polyline
   from trend.json) to also update the bench polyline + caption — read that
   script block before editing.

## Edge cases
- Bench history missing (first night not run yet) → no overlay, no caption;
  page renders exactly as today. Everything `{% if %}`-guarded.
- 1D live view: skip the bench (intraday index fetch not worth it) — hide the
  overlay when range=1d in the JS.

## Verify
```sh
.venv/bin/python -m pytest -q
# backfill locally once (writes via script, not GET):
.venv/bin/python -c "
import fetch, db
from db import get_conn, save_price_history
import refresh  # if you added an ensure_indices() helper, call it; else inline
with get_conn() as conn:
    conn.execute(\"INSERT OR IGNORE INTO stocks (ticker,name,exchange,sector,currency,added_at) VALUES ('^GSPC','S&P 500','INDEX','','USD',datetime('now'))\")
    rows = fetch.price_history_resilient('^GSPC','max')
    save_price_history(conn, '^GSPC', rows); conn.commit()"
.venv/bin/python -c "
import app; c=app.app.test_client()
h=c.get('/stock/AAPL').get_data(as_text=True)
assert 'stroke-dasharray=\"4,3\"' in h; print('overlay ok')"
# /today must NOT list the index:
.venv/bin/python -c "
import app; c=app.app.test_client()
assert '^GSPC' not in c.get('/today').get_data(as_text=True); print('excluded ok')"
```
Add a pytest: trend_chart with a bench series returns bench_points; screener
excludes INDEX rows.

## Ship
PR title: `Price trend: dashed S&P 500 / NIFTY 50 benchmark overlay`
