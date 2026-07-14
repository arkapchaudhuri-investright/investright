# Spec 05 — PWA manifest + home-screen icons

Read `specs/_CONTEXT.md` first. Branch: `feature/pwa-manifest`. Size: ~1-2h.

## Problem
No web app manifest / apple-touch-icon → "Add to Home Screen" gives a generic
tile. Add a manifest + real Otto icons. Scope: installable identity ONLY — no
service worker, no offline (keep it honest and small).

## Files
`static/manifest.json` (new), `static/icons/` (new PNGs), `templates/base.html`.

## Step 1 — generate icons (192, 512, 180 for apple-touch)
No Pillow locally. Use headless Chrome exactly like the OG image was built:
1. Write a scratch HTML page: solid `#191917` background, Otto SVG centered at
   ~70% of the canvas. Copy the Otto `<svg>` markup from `templates/_otto.html`
   (strip the `<style>` animations — static pose is fine for an icon).
2. Render at each size:
```sh
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new \
  --window-size=512,512 --screenshot=static/icons/icon-512.png file://$PWD/icon.html
```
   Repeat with 192 and 180 (`--window-size` accordingly; adjust the HTML body
   size per run or use one 512 render + `sips -z 192 192` / `-z 180 180` to
   downscale — sips is available on macOS and simpler).
3. Verify with `sips -g pixelWidth -g pixelHeight static/icons/*.png`.

## Step 2 — manifest.json
```json
{
  "name": "InvestRight",
  "short_name": "InvestRight",
  "description": "Deep-dive stock research, minus the noise.",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#191917",
  "theme_color": "#191917",
  "icons": [
    { "src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png" },
    { "src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png" }
  ]
}
```

## Step 3 — base.html `<head>` (next to the favicon link)
```html
<link rel="manifest" href="{{ url_for('static', filename='manifest.json') }}">
<link rel="apple-touch-icon" href="{{ url_for('static', filename='icons/icon-180.png') }}">
```

## Gotcha
`static/` may be partially gitignored — check `.gitignore`; `static/icons/`
and `manifest.json` MUST be committed (they're source, not cache). If a rule
blocks them, add explicit negations (`!static/icons/`).

## Verify
```sh
.venv/bin/python -m pytest -q
.venv/bin/python -c "
import app, json; c=app.app.test_client()
m=c.get('/static/manifest.json'); assert m.status_code==200
json.loads(m.get_data(as_text=True))
h=c.get('/').get_data(as_text=True)
assert 'rel=\"manifest\"' in h and 'apple-touch-icon' in h; print('ok')"
sips -g pixelWidth static/icons/icon-512.png   # = 512
```
Icons should show Otto clearly at 48px thumbnail size — zoom the PNG to check.

## Ship
PR title: `Add PWA manifest + Otto home-screen icons`
