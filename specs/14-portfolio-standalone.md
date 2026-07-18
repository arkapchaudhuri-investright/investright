# Spec 14 — Standalone Portfolio (own menu, own table, migrate off watchlist)

Read `specs/_CONTEXT.md` first. Branch: `feature/portfolio-standalone`.
Size: weekend-ish. First of four portfolio specs (14 → 15 → 16 → 17); do them
in order. This one replaces the watchlist-bolted holdings from spec 11 with a
first-class `/portfolio` tab.

## Design
Portfolio is its own thing, **fully independent of the watchlist**: a new
top-level menu (beside Today / Strategies), login-gated, backed by a dedicated
`holdings` table. One portfolio per user; one (qty, avg buy price) pair per
ticker (no lots, no transaction ledger). You can own a stock you don't watch and
watch one you don't own. The per-row P&L / totals-strip / allocation-donut built
in spec 11 **move here** and come **off** `/watchlist`.

Keep the honest-copy rule: missing prices degrade to text, never guesses.

## Step 1 — schema (db.py `SCHEMA`, CREATE IF NOT EXISTS — new table)
```sql
CREATE TABLE IF NOT EXISTS holdings (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    ticker     TEXT NOT NULL REFERENCES stocks(ticker) ON DELETE CASCADE,
    qty        REAL NOT NULL,
    avg_price  REAL NOT NULL,          -- native currency, like spec 11's buy_price
    added_at   TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, ticker)
);
```
No `_migrate` ALTER needed. (Lesson from spec 12: a brand-new **column** added in
`_migrate` races the two gunicorn workers on first deploy — a new **table** via
CREATE IF NOT EXISTS is safe, so prefer that here.)

## Step 2 — data migration (manage.py, NOT `_migrate`)
Moving spec-11 data is a one-shot data copy → put it in `manage.py` as a
dry-run-by-default subcommand (mirror the existing `migrate-watchlist`), never in
startup `_migrate` (that runs per-worker and can double-apply / race):
```
manage.py migrate-holdings           # dry-run: report what would move
manage.py migrate-holdings --commit  # copy user_watchlist(qty,buy_price>0) → holdings,
                                     # then NULL those watchlist columns
```
Idempotent: skip rows whose (user,ticker) already exists in `holdings`. The
`user_watchlist.qty/buy_price` columns stay in place but unused (SQLite can't drop
columns cheaply); spec 11's UI is removed in Step 6 so they stop rendering.

## Step 3 — held tickers must stay priced (refresh.py)
`main()` builds `symbols` from `SELECT ticker FROM watchlist`. Held-but-unwatched
tickers would otherwise never get a nightly snapshot. Add them to the union:
```python
held = [r["ticker"] for r in conn.execute("SELECT DISTINCT ticker FROM holdings")]
symbols = sorted(set(symbols) | set(held))
```
(`ensure_stock` already runs over the union, so a held ticker with no `stocks`
row gets one.) No new cost — same nightly pass.

## Step 4 — routes (app.py)
- `GET /portfolio` (`@login_required`) — the dashboard (Step 5 data).
- `POST /portfolio/add` (`@login_required`): fields `symbol`, `qty`, `avg_price`
  (floats > 0). If the ticker isn't known locally, `_ingest_stock(symbol)` first
  (POST path — writes allowed); reject unknown symbols with the `_NOT_FOUND`
  flash. Upsert one row per (user,ticker) — adding an existing ticker **replaces**
  qty/avg (single-lot model), `updated_at=now`. `_log("hold_add", ticker)`.
- `POST /portfolio/<ticker>/delete` (`@login_required`): delete the caller's row
  only. Redirect to `/portfolio`.
- CSRF hidden input in every form.

## Step 5 — dashboard data + template (templates/portfolio.html)
Reuse the spec-11 machinery, now reading `holdings`:
- Join holdings → stocks → snapshots; `convert_row` each to display ccy (the
  `buy_price`→`avg_price` field is native money, convert with the same factor;
  stash `avg_price_native` for the edit form, as spec 11 did for buy_price).
- Per holding: market value, P&L (money + tinted %). Rows with no live price
  degrade to "price n/a", no P&L.
- **Totals strip** (reuse `.div-stats` + `port-totals`): invested, value, pnl,
  pnl_pct — all display ccy.
- **Allocation donut**: `metrics.allocation_donut([(ticker, mkt_value), ...])`
  (already built + tested in spec 11 Phase B).
- **Sector mix**: a second small line/legend from `stocks.sector` → summed market
  value per sector (reuse `allocation_donut` or a simple % list).
- **Concentration note**: `.asof` line — "Largest position: AAPL 32%" from the
  top donut slice; only when > 25%.
- Inline `<details>` edit form per row (qty + avg buy, native ccy) — same pattern
  as spec 11's `holdings_edit` macro; move that macro here.
- Empty state: honest copy + a link to add the first holding.

## Step 6 — remove holdings from the watchlist (revert spec 11 UI)
- templates/watchlist.html: drop the Qty / Avg buy / P&L columns + card fields,
  the `pnl_cell` / `holdings_edit` macros, and the `port-totals` strip/donut.
- app.py `watchlist_page()`: drop the per-row P&L loop, the `totals`/`donut`
  block, and the `w.qty, w.buy_price` from the SELECT. Leave `convert_row`'s
  `buy_price` entry (harmless) or trim it — either is fine.
- Keep the `POST /watchlist/<ticker>/holdings` route? **Remove it** — /portfolio
  owns holdings now. (Grep for `save_holdings` and delete route + template refs.)

## Step 7 — nav
Add a "Portfolio" link to the topbar/menu in `base.html` beside Today /
Strategies, shown only when `current_user` (grep how those links are gated).

## Step 8 — tests
- guest `GET /portfolio` → 302 login; guest `POST /portfolio/add` → 302 login.
- `POST /portfolio/add` bad qty/price (authed path only if a fixture exists;
  else note in PR) → no 500.
- Keep spec 11's `allocation_donut` unit tests green.

## Verify
```sh
.venv/bin/python -m pytest -q
# dry-run the migration against local data:
.venv/bin/python manage.py migrate-holdings
```
Then with dev login (test client): add AAPL 10 @ 150 → /portfolio shows P&L +
totals + donut; delete → empty state; ₹ toggle converts money, % stays.
Confirm /watchlist no longer shows any holdings UI.

## Ship
PR title: `Portfolio: standalone /portfolio tab + holdings table (off watchlist)`
Note in the PR body: run `manage.py migrate-holdings --commit` on the VM after
deploy (owner's step), since it moves live spec-11 data.
