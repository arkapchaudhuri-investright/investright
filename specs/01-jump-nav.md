# Spec 01 — Deep-dive jump navigation (sticky section chips)

Read `specs/_CONTEXT.md` first. Branch: `feature/jump-nav`. Size: ~2h.

## Problem
`/stock/<ticker>` stacks ~15 `<section class="card">` blocks with no
wayfinding — a huge scroll. Add a sticky, horizontally-scrollable chip row
under the header island that anchor-links to each section.

## Files
`templates/stock.html`, `static/style.css`.

## Step 1 — give every major section an id
Some already have one: `#trend`, `#income`, `#peers`, `#leadership`.
Find each remaining `<section class="card">` by the `<h2>` text inside its
`.card-head` and add an id:

| h2 text (match loosely) | id |
|---|---|
| Snowflake / score radar | `snowflake` |
| Fair value | `value` |
| Health checks | `checks` |
| Past performance | `past` |
| Future | `future` |
| Dividend | `dividend` |
| Insider activity / Ownership | `insiders` |
| In the news | `news` |
| Your notes / journal | `notes` |
| What investors think / sentiment | `sentiment` |

Skip any that don't exist for this page — no fake anchors.

## Step 2 — chip row in stock.html
Insert directly AFTER the closing `</section>` of the header island
(`<section class="card stock-head">`):

```html
{# Jump nav — sticky chips; only render chips whose section rendered. #}
<nav class="jumpnav" aria-label="Jump to section">
  <a href="#trend">Trend</a>
  <a href="#snowflake">Score</a>
  {% if dcf %}<a href="#value">Fair value</a>{% endif %}
  {% if income %}<a href="#income">Revenue</a>{% endif %}
  <a href="#checks">Checks</a>
  {% if charts %}<a href="#past">Past</a>{% endif %}
  {% if projection %}<a href="#future">Future</a>{% endif %}
  {% if dividend %}<a href="#dividend">Dividend</a>{% endif %}
  <a href="#peers">Competitors</a>
  {% if exec_tiers %}<a href="#leadership">Leadership</a>{% endif %}
  {% if is_us %}<a href="#insiders">Insiders</a>{% endif %}
  {% if news %}<a href="#news">News</a>{% endif %}
  {% if current_user %}<a href="#notes">Notes</a>{% endif %}
</nav>
```
Check the actual template vars guarding each section (open stock.html and
mirror its own `{% if %}` conditions) — the list above is the intent, the
template is the truth.

## Step 3 — CSS (append near the `.stock-head` block)
```css
/* Jump nav — sticky chip row for the deep-dive's ~15 sections. */
.jumpnav {
  position: sticky; top: 0; z-index: 80;
  display: flex; gap: 8px; overflow-x: auto; -webkit-overflow-scrolling: touch;
  scrollbar-width: none; padding: 10px 2px; margin: 0 0 6px;
  background: var(--bg);
  border-bottom: 1px solid var(--hairline);
}
.jumpnav::-webkit-scrollbar { display: none; }
.jumpnav a {
  flex: none; font-size: 12.5px; color: var(--muted); text-decoration: none;
  border: 1px solid var(--hairline); border-radius: 999px; padding: 5px 12px;
}
.jumpnav a:hover { color: var(--accent); border-color: var(--accent); }
/* anchored sections stop below the sticky bar */
#trend, #snowflake, #value, #income, #checks, #past, #future, #dividend,
#peers, #leadership, #insiders, #news, #notes, #sentiment {
  scroll-margin-top: 56px;
}
```
No JS needed (plain anchors). Do NOT add scrollspy.

## Verify
```sh
.venv/bin/python -m pytest -q
.venv/bin/python -c "
import app; c=app.app.test_client(); h=c.get('/stock/AAPL').get_data(as_text=True)
assert 'jumpnav' in h
for i in ('id=\"trend\"','id=\"income\"','id=\"peers\"','id=\"leadership\"','id=\"news\"'):
    assert i in h, i
print('ok')"
```
Also load `/stock/AAPL` in a browser at 375px if available: chips scroll
horizontally, tapping one lands with the section title visible (not under the
bar), dark + light both read.

## Ship
PR title: `Deep-dive: sticky jump navigation across sections`
