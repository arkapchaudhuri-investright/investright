# Spec 06 — Guest watchlist page that sells the feature

Read `specs/_CONTEXT.md` first. Branch: `feature/guest-watchlist`. Size: ~2h.

## Problem
Logged-out `/watchlist` just asks you to sign in. Show what you'd GET: three
real demo stocks with mini snowflakes + prices, then the sign-in CTA.

## Files
`app.py` (route `watchlist_page()`, ~line 274), `templates/watchlist.html`,
`static/style.css`.

## Step 1 — route: demo rows for guests
In the `else` (guest) path of `watchlist_page()`, load up to 3 demo tickers
that exist locally AND have scores (read-only):
```python
DEMO = ("AAPL", "RELIANCE.NS", "MSFT")
demo = []
if not user:
    with get_conn() as conn:
        for tk in DEMO:
            s = conn.execute("SELECT s.*, n.price, n.change_pct FROM stocks s "
                             "LEFT JOIN snapshots n ON n.ticker=s.ticker "
                             "WHERE s.ticker=?", (tk,)).fetchone()
            if not s:
                continue
            checks = [dict(r) for r in conn.execute(
                "SELECT axis, passed FROM health_checks WHERE ticker=?", (tk,))]
            sc = metrics.axis_scores(checks)
            demo.append({**dict(s), "snowflake": metrics.snowflake(sc)
                         if any(v is not None for v in sc.values()) else None})
```
Pass `demo=demo` to the template. Missing tickers just shrink the list —
never fake data.

## Step 2 — template (guest branch of watchlist.html)
Replace the bare sign-in message with:
```html
<section class="card guest-demo">
  <div class="card-head"><h2>Your watchlist lives here</h2></div>
  <p class="asof">Track any US or India stock — price, day change, and Otto's
    health score at a glance. Here's what three look like:</p>
  <div class="peer-strip">
    {% for d in demo %}
    <a class="peer" href="{{ url_for('stock', ticker=d['ticker']) }}">
      {% if d['snowflake'] %}
      <svg class="peer-snow" viewBox="0 0 60 60" aria-hidden="true">
        <polygon points="{{ d['snowflake']['rings'][3] }}" fill="none" stroke="var(--hairline)" stroke-width="1"/>
        <polygon points="{{ d['snowflake']['polygon'] }}" fill="rgba(29,158,117,.25)" stroke="var(--accent)" stroke-width="1.5"/>
      </svg>
      {% endif %}
      <span class="tick">{{ d['ticker'] }}</span>
      <span class="peer-name">{{ d['name'] }}</span>
      {% if d['price'] %}<span class="peer-px">{{ d['price']|money(d['currency']) }}</span>{% endif %}
    </a>
    {% endfor %}
  </div>
  <div class="guest-cta">
    <a class="btn-analyze" href="{{ url_for('auth.register') }}">Create a free account</a>
    <a class="btn-watch" href="{{ url_for('auth.login') }}">Sign in</a>
  </div>
</section>
```
Mirror the existing peer-strip markup in stock.html (`.peer`, `.peer-snow`,
`.tick`, `.peer-name`, `.peer-px`) — reuse, don't restyle. Read the current
guest branch first and keep any copy that's already good.

## Step 3 — CSS
```css
.guest-cta { display: flex; gap: 12px; margin-top: 18px; flex-wrap: wrap; }
.guest-cta a { text-decoration: none; }
```

## Verify
```sh
.venv/bin/python -m pytest -q
.venv/bin/python -c "
import app; c=app.app.test_client()
h=c.get('/watchlist').get_data(as_text=True)
assert 'guest-demo' in h and 'Create a free account' in h; print('ok')"
```
Confirm signed-in view unchanged (login with dev creds via test client or
browser).

## Ship
PR title: `Watchlist: guest view shows real demo scores + CTA`
