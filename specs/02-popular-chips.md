# Spec 02 — Home "Popular right now" chips: names + day change

Read `specs/_CONTEXT.md` first. Branch: `feature/popular-chips`. Size: ~1h.

## Problem
Home's "Popular right now" chips show bare tickers (`BPCL.NS`) — meaningless
to newcomers. Add the company's short name and a tinted day-change %.

## Files
`app.py` (route `home()`, ~line 222), `templates/home.html` (chips ~line 71),
`static/style.css`.

## Step 1 — enrich the trending query in `home()`
The existing query joins `events`→`stocks` for ticker+name. LEFT JOIN
snapshots for the change:
```sql
SELECT e.ticker, s.name, n.change_pct
FROM events e
JOIN stocks s ON s.ticker = e.ticker
LEFT JOIN snapshots n ON n.ticker = e.ticker
WHERE e.ticker IS NOT NULL AND e.ticker != ''
  AND e.action IN ('view','analyze','add')
  AND e.ts >= datetime('now','-30 days')
GROUP BY e.ticker ORDER BY COUNT(*) DESC LIMIT 6
```
(Read the current query in the route and modify minimally — keep GROUP BY.)

## Step 2 — template (`.hero-chip` block in home.html)
```html
<a class="hero-chip" href="{{ url_for('stock', ticker=t['ticker']) }}">
  <span class="hero-chip-tick">{{ t['ticker'] }}
    {% if t['change_pct'] is not none %}
    <span class="hero-chip-chg {{ 'up' if t['change_pct'] > 0 else 'down' if t['change_pct'] < 0 else 'flat' }}">
      {{ '%+.1f%%'|format(t['change_pct']) }}</span>
    {% endif %}
  </span>
  <span class="hero-chip-name">{{ t['name'] }}</span>
</a>
```
Shorten long names in Python or with Jinja `truncate(22, True, '…')`.

## Step 3 — CSS (extend the existing `.hero-chip` block)
```css
.hero-chip { display: flex; flex-direction: column; align-items: flex-start;
  gap: 1px; text-align: left; padding: 8px 14px; }
.hero-chip-tick { font-weight: 600; font-size: 13.5px; display: flex;
  gap: 6px; align-items: baseline; }
.hero-chip-chg { font-size: 11px; font-weight: 500; }
.hero-chip-chg.up { color: var(--up); } .hero-chip-chg.down { color: var(--down); }
.hero-chip-name { font-size: 11px; color: var(--muted); max-width: 150px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
```
Keep the row centered and wrapping as it is now (`.hero-chips` untouched).

## Verify
```sh
.venv/bin/python -m pytest -q
.venv/bin/python -c "
import app; c=app.app.test_client(); h=c.get('/').get_data(as_text=True)
assert 'hero-chip-name' in h; print('ok')"
```
Eyeball `/` in browser if available: chips read `AAPL +1.2% / Apple`,
wrap cleanly at 375px, both themes.

## Ship
PR title: `Home: popular chips show company name + day change`
