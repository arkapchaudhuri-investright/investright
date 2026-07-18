# Spec 16 — Portfolio review signals (⚑ "worth a look", never buy/sell)

Read `specs/_CONTEXT.md` first. Branch: `feature/portfolio-signals`.
Size: ~half day. DEPENDS ON spec 14.

## Design
Surface **review flags** on holdings — data-backed nudges to take a second look,
explicitly NOT buy/sell/hold verdicts. Hard trade calls are investment advice
(legal exposure for an unregistered site, and against the app's "Otto crunches
numbers, not advice" voice); framed as signals with a plain-English why, they're
defensible and more honest. Everything is computed from data the app already
saves (health checks, DCF fair value, snapshots, earnings date, allocation).

**Copy rule:** every flag ends implicitly at "worth a look." The page and any
email carry the standing "Not investment advice — Otto just crunches numbers"
line. No "sell", "trim", "dump", "buy more".

## Step 1 — the rules (metrics.py, pure function)
```python
def review_signals(holding, snap, checks, dcf, alloc_pct, earnings_days):
    """Return a list of {'code','text'} review flags for one holding. Pure."""
```
Fire a flag when:
- **Rich vs fair value** — `dcf.fair_value` set and `price > 1.30 * fair_value`:
  "Trading ~X% above Otto's fair-value estimate."
- **Weakening checks** — ≥3 of 5 snowflake axes failing:
  "N of 5 health checks are failing."
- **Concentration** — `alloc_pct > 25`: "This is X% of your portfolio."
- **Down + weakening** — price < 0.80 * avg_price AND checks weakening (reuse the
  axis test): "Down X% from your buy, and fundamentals are softening."
- **Earnings soon** — `0 <= earnings_days <= 7` (spec 13): "Reports earnings in N
  days." (heads-up flavour, distinct hue from the risk flags.)
Thresholds live as named constants at the top so they're easy to tune. Each flag
is independent; a holding can carry several. Pure + fully unit-testable.

## Step 2 — wire into the dashboard (app.py `/portfolio`)
For each holding gather its `snap`, `checks`, `dcf`, `alloc_pct` (its donut slice
%), and `earnings_days` (compute as spec 13's route does), then
`metrics.review_signals(...)`. Read-only — no DB writes (§3); this is derived at
request time from already-saved rows, like the snowflake/DCF on the deep-dive.

## Step 3 — UI (portfolio.html)
- A ⚑ chip on any holding with flags; expanding it lists the reasons.
- A "Worth a look" summary card at the top: the holdings with the most/most-severe
  flags first. Empty (nothing to flag) → a calm "Nothing needs your attention
  this week" line, not a bare card.
- Reuse the amber/`--wash` treatment already used for the overvalued FV bar; keep
  the earnings heads-up in `--accent` so risk vs info read differently.
- Standing disclaimer line under the section.

## Step 4 — tests (tests/test_metrics.py)
- `review_signals` golden cases: each rule fires on the right inputs and stays
  silent otherwise (feed plain dicts — no DB).
- A holding tripping several rules returns all of them.
- None/missing inputs (no dcf, no checks, no earnings) → no crash, fewer flags.

## Verify
```sh
.venv/bin/python -m pytest -q
.venv/bin/python -c "
import metrics
print(metrics.review_signals(
  {'avg_price':100,'qty':10}, {'price':200}, [], {'fair_value':120}, 40, 5))"
# expect: rich-vs-FV + concentration + earnings-soon flags
```
Then dev-login test client: a holding priced well above its DCF shows a ⚑ chip
with the fair-value reason; a tiny position shows none.

## Ship
PR title: `Portfolio: review signals (⚑ worth-a-look flags, not advice)`
