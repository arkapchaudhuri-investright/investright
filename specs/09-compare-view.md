# Spec 09 — Compare view (/compare?t=AAPL,MSFT,NVDA)

Read `specs/_CONTEXT.md` first. Branch: `feature/compare-view`. Size: ~half day.

## Design
Read-only page comparing 2–4 tracked tickers side by side: snowflake, price +
day change, market cap, P/E, P/B, dividend yield, DCF fair-value gap, overall
score. Columns = tickers. Guest-accessible. Entry points: a "Compare" pill on
the deep-dive Competitors card head, each peer chip gains a tiny "+ compare"
affordance (skip if fiddly — the card-head pill covering current ticker + top
peers is enough).

## Step 1 — route (app.py)
```python
@app.route("/compare")
def compare():
    syms = [s.strip().upper() for s in (request.args.get("t") or "").split(",") if s.strip()][:4]
    cols = []
    with get_conn() as conn:
        for tk in syms:
            s = conn.execute("SELECT * FROM stocks WHERE ticker=?", (tk,)).fetchone()
            if not s or s["exchange"] == "INDEX":
                continue
            snap = conn.execute("SELECT * FROM snapshots WHERE ticker=?", (tk,)).fetchone()
            checks = [dict(r) for r in conn.execute(
                "SELECT axis, passed FROM health_checks WHERE ticker=?", (tk,))]
            dcf = conn.execute("SELECT upside_pct FROM dcf WHERE ticker=?", (tk,)).fetchone()
            sc = metrics.axis_scores(checks)
            cols.append({**dict(s), "snap": dict(snap) if snap else None,
                         "scores": sc, "overall": metrics.overall_score(sc),
                         "snowflake": metrics.snowflake(sc),
                         "upside": dcf["upside_pct"] if dcf else None})
    if len(cols) < 2:
        flash("Pick at least two tracked stocks to compare.", "error")
        return redirect(url_for("home"))
    _log("compare")
    return render_template("compare.html", cols=cols, **_fx_ctx())
```
Currency: convert each column's money via `_fx_factor(s['currency'], ...)`
exactly as `stock()` does for peers (grep `for p in peers:` in app.py and copy
that per-column conversion for price/market_cap).

## Step 2 — template `templates/compare.html` (new)
Extends base. One `.card` with a horizontally-scrollable table
(reuse the `.income-matrix-wrap` overflow pattern):
- header row: logo-or-monogram + ticker (link to deep dive) + name
- rows: Price (+day %), Market cap, P/E, P/B, Div yield, DCF gap
  (`{{ '%+.0f%%'|format(c['upside']) }}` tinted up/down, '—' when None),
  Overall score (as %), Snowflake (the `.peer-snow` mini SVG).
- Every metric row label in the sticky first column (copy `.im-label` sticky
  CSS pattern).
- Footer note `.asof`: "Figures from the latest saved snapshot; scores are
  Otto's health checks. Not investment advice."

## Step 3 — entry point (stock.html, Competitors card head)
```html
{% if peers %}
<a class="ghost compare-link"
   href="{{ url_for('compare', t=s['ticker'] ~ ',' ~ (peers[:3]|map(attribute='ticker')|join(','))) }}">Compare</a>
{% endif %}
```
Guard: peers entries with `missing: True` have a ticker too — fine to include.

## Step 4 — CSS
Small: `.compare-table` borrows income-matrix styles; keep columns min-width
~160px; snowflakes 60px.

## Tests
Add to tests/test_routes.py:
- `/compare?t=AAPL,MSFT` → 200 when both exist locally.
- `/compare?t=AAPL` (one ticker) → 302 redirect.
- `/compare?t=` → 302.

## Verify
```sh
.venv/bin/python -m pytest -q
.venv/bin/python -c "
import app; c=app.app.test_client()
h=c.get('/compare?t=AAPL,MSFT,NVDA').get_data(as_text=True)
for t in ('AAPL','MSFT','NVDA'): assert t in h
print('ok')"
```
Browser (if available): 375px — table scrolls horizontally, first column
sticky; dark + light.

## Ship
PR title: `Compare view: side-by-side snowflakes + key metrics for 2-4 stocks`
