# Spec 04 — /today: browse past nightly notes

Read `specs/_CONTEXT.md` first. Branch: `feature/digest-pager`. Size: ~1-2h.

## Problem
The nightly AI note is saved per date in `digest(digest_date PK, body, model,
picks_json, created_at)` but /today only shows the latest. Add "‹ older /
newer ›" links to read previous nights. Read-only — GET must not write.

## Files
`app.py` (route `today()`), `templates/today.html`.

## Step 1 — route
Find where `today()` loads the digest (grep `digest` in app.py). Add:
- `?note=YYYY-MM-DD` query param. If present and valid, load THAT row:
  `SELECT * FROM digest WHERE digest_date = ?`; else the latest (current
  behavior).
- Compute neighbors:
```python
older = conn.execute("SELECT digest_date FROM digest WHERE digest_date < ? "
                     "ORDER BY digest_date DESC LIMIT 1", (cur_date,)).fetchone()
newer = conn.execute("SELECT digest_date FROM digest WHERE digest_date > ? "
                     "ORDER BY digest_date ASC LIMIT 1", (cur_date,)).fetchone()
```
- Pass `note_date`, `older`, `newer` to the template. Guard: unknown/garbage
  `?note=` falls back to latest (no 500, no 404).
- IMPORTANT: keep the existing market/scope query params working — pager links
  must carry them through (`url_for('today', market=..., scope=..., note=...)`
  or just relative `?note=...` if the template builds links that way — mirror
  how the market seg builds its links).

## Step 2 — template
In the "Otto's read" card head, next to the existing `.asof` label, add:
```html
<nav class="digest-pager" aria-label="Browse past notes">
  {% if older %}<a href="?note={{ older }}&market={{ market }}">‹ {{ older }}</a>{% endif %}
  <span class="asof">{{ note_date }}</span>
  {% if newer %}<a href="?note={{ newer }}&market={{ market }}">{{ newer }} ›</a>{% endif %}
</nav>
```
When viewing an old note, show a quiet one-liner above the body:
`<p class="asof">A past note — <a href="{{ url_for('today') }}">back to the latest</a>.</p>`

## Step 3 — CSS
```css
.digest-pager { display: inline-flex; gap: 10px; align-items: baseline; font-size: 12.5px; }
.digest-pager a { color: var(--accent); text-decoration: none; }
.digest-pager a:hover { text-decoration: underline; }
```

## Verify
```sh
.venv/bin/python -m pytest -q
.venv/bin/python -c "
import app, sqlite3
c = app.app.test_client()
h = c.get('/today').get_data(as_text=True); assert h  # latest still renders
# find a real past date locally then request it:
import db
with db.get_conn() as conn:
    d=[r['digest_date'] for r in conn.execute('SELECT digest_date FROM digest ORDER BY 1')][:1]
if d:
    h2 = c.get(f'/today?note={d[0]}').get_data(as_text=True)
    assert 'past note' in h2 or d[0] in h2
h3 = c.get('/today?note=nonsense'); assert h3.status_code == 200
print('ok')"
```

## Ship
PR title: `/today: page through past nightly notes`
