# Spec 11 — Portfolio tracking (BIG — do in two phases, two PRs)

Read `specs/_CONTEXT.md` first. Size: weekend-ish. Do Phase A fully (PR 1),
then Phase B (PR 2). Do NOT start B before A is merged.

## Design
Watchlist rows optionally carry holdings: quantity + buy price (native
currency). With holdings set, the watchlist shows P&L per row and a totals
strip; /watchlist becomes a lightweight portfolio. No transactions ledger, no
multiple lots — one (qty, avg buy price) pair per ticker. Keep calling it
"Watchlist" in the UI; add a "Holdings" subhead where relevant.

---

## Phase A — schema + edit + per-row P&L  (branch `feature/portfolio-a`)

### A1 — migration (db.py `_migrate`)
Additive columns on user_watchlist (mirror the existing ALTER pattern):
```python
cols = {r["name"] for r in conn.execute("PRAGMA table_info(user_watchlist)")}
if "qty" not in cols:
    conn.execute("ALTER TABLE user_watchlist ADD COLUMN qty REAL")
    conn.execute("ALTER TABLE user_watchlist ADD COLUMN buy_price REAL")
```
NULL qty = plain watch row (no holdings) — everything must handle that.

### A2 — route: save holdings
`POST /watchlist/<ticker>/holdings` (`@login_required`): fields `qty`,
`buy_price` (floats > 0; BOTH empty clears holdings → set NULL/NULL).
Reject garbage with flash; update only the caller's row
(`WHERE user_id=? AND ticker=?`); if no row, flash "star it first". Redirect
to /watchlist. CSRF input. `_log("holdings", ticker)`.

### A3 — watchlist UI (templates/watchlist.html)
The page has BOTH a table (desktop) and cards (mobile) — grep `wl-cards` and
`#watchlist table`; you must touch both:
- New columns/fields (signed-in only): Qty, Avg buy, P&L. P&L = 
  `(price - buy_price) * qty` in NATIVE ccy → convert to display ccy with the
  page's existing per-row conversion (grep how price is converted; holdings
  use the same factor). Show as money + tinted % (`(price/buy_price - 1)*100`).
- Rows without holdings: an "＋ add holdings" ghost link.
- Edit affordance: a `<details>` inline row form (qty + buy price + Save) —
  no JS needed. Native `<details>` matches the app's patterns.

### A4 — tests
- guest POST /watchlist/AAPL/holdings → 302 login.
- (authed-path unit if conftest supports it; else note in PR.)

### A5 — verify
```sh
.venv/bin/python -m pytest -q
```
Then via browser or test client with dev login: star AAPL, add qty 10 @ 150,
row shows P&L; clear both fields → back to plain row; ₹ toggle converts P&L.

PR title: `Watchlist: optional holdings (qty + buy price) with per-row P&L`

---

## Phase B — totals + allocation donut  (branch `feature/portfolio-b`)

### B1 — totals strip (route)
In `watchlist_page()` for signed-in users, sum rows that have holdings, all in
display ccy: `invested`, `value`, `pnl`, `pnl_pct`. Pass `totals` (None when
no holdings anywhere).

### B2 — donut (metrics.py)
Pure-SVG arcs like `snowflake()` — no libs:
```python
def allocation_donut(slices, size=140, thickness=18):
    """slices=[(label, value)]; returns [{'d', 'label', 'pct'}] SVG arc paths."""
```
Compute cumulative angles; each slice an SVG arc path (`A` commands) around
center; colors: rotate through 5 opacities/mixes of the brand green — use
`fill` attrs like `rgba(29,158,117,.9/.7/.5/.35/.22)` and repeat; label list
rendered beside the donut as a legend (chip + ticker + %). Cap at top 7 + an
"Other" slice.

### B3 — UI
Totals strip card above the table: four stat blocks (`.div-stats` pattern
exists — reuse it) + the donut right-aligned (stack on mobile). Only render
when `totals`.

### B4 — verify
pytest + browser: totals match hand math; donut slices sum to full circle
(spot-check angles); empty-holdings user sees no strip; 375px stacks.

PR title: `Watchlist: portfolio totals + allocation donut`
