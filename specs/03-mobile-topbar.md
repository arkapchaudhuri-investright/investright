# Spec 03 — Compress the mobile topbar (3 rows → 2)

Read `specs/_CONTEXT.md` first. Branch: `feature/mobile-topbar`. Size: ~1-2h.

## Problem
At 375px the topbar wraps into ~3 rows (brand / search+links / watchlist pill),
eating ~170px before content. Target: 2 compact rows, same features.

## Files
`templates/base.html` (topbar + gear panel + its JS), `static/style.css`.

## Approach
1. On ≤560px, hide the "Team" nav link (`display:none`) — it is NOT an
   onboarding-tour target (tour targets: navsearch/today/watchlist/settings —
   verify by grepping `data-tour` in base.html before assuming).
2. Add a "Team" row link inside the gear `.settings-panel` (always present, so
   desktop users get it twice — acceptable; or wrap it in a mobile-only class).
   Pattern (inside the panel, after the account row):
```html
<div class="set-row set-mobile-nav">
  <span class="set-label">More</span>
  <a href="{{ url_for('team') }}">Team</a>
</div>
```
```css
.set-mobile-nav { display: none; }
@media (max-width: 560px) { .set-mobile-nav { display: flex; } }
@media (max-width: 560px) { .topnav a.navlink[href*="team"] { display: none; } }
```
   (Check the actual Team link markup — it's a `.navlink`; add a class
   `nav-team` to it for a cleaner selector instead of href matching.)
3. Tighten mobile paddings: find the existing `@media` blocks for `.topbar` /
   `.topnav` / `.brand` and reduce vertical padding + gaps ~25%; shrink
   `.wl-toggle` padding; brand font-size 23px→20px at ≤560px.
4. Do NOT restructure the DOM or add a hamburger — this is a squeeze, not a
   redesign.

## Verify
```sh
.venv/bin/python -m pytest -q
```
Browser at 375×812 (required for this spec): measure before/after —
```js
document.querySelector('header.topbar').offsetHeight
```
Target ≤ ~120px (from ~170). Confirm: Team reachable via gear on mobile,
still in the top nav on desktop; onboarding tour still runs (localStorage
`ir_toured` cleared → reload home → tour steps all anchor correctly).

## Ship
PR title: `Mobile: compress topbar to two rows (Team moves into the gear)`
