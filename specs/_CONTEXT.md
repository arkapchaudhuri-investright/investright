# InvestRight — shared context for all specs (read me first, read me once)

You are implementing ONE feature spec in this repo (`~/Desktop/InvestRight`).
Everything you need is in this file + your spec. **Do NOT read DESIGN.md,
README.md, or memory files** — that wastes tokens; this file is the distilled
version. Only open the files your spec lists.

## What this app is
Flask + SQLite stock-research dashboard ("InvestRight", mascot: Otto the owl).
Live at https://investright.us. No build step, no JS frameworks, no CDN.
US + India (`.NS`) stocks via yfinance; free Gemini/Groq for AI notes.

## Hard rules (violating these fails review)
1. **$0 spend** — free sources only, no paid APIs, no new services.
2. **Vanilla only** — CSS in `static/style.css` using existing custom
   properties; vanilla JS, progressive enhancement (server-rendered links/forms
   work with JS off; JS only enhances). All charts = inline SVG built in Python.
3. **Cron writes, web reads** — NEVER add a DB write to a GET route. Writes
   happen in POST routes, `refresh.py` (nightly), or `manage.py` commands.
4. **Honest copy** — never invent data; missing data degrades to honest text.
   Never show internal notes like "we haven't built X" to users.
5. **AI is Gemini/Groq (digest.py), never Claude.**
6. Keep money display via the `|money(ccy)` / `|bigmoney(ccy)` filters; page
   figures are pre-converted to display currency in the `stock()` route.

## Design tokens (use these, don't invent)
- Colors: `var(--bg) --card --text --muted --hairline --accent --accent-deep
  --up --down --wash --shadow --shadow-pop --edge`. Dark is default; light via
  `[data-theme=light]`. Never hardcode brand colors except inside SVG mascots.
- Type: `var(--serif)` (Fraunces — titles/voice), `var(--sans)` (Inter — data).
  `.serif` class exists. Page h2s live in `.card-head`.
- Components you can reuse: `.card` (section shell), `.card-head` (h2 + right
  `.asof` label), `.seg` (pill segment nav), `.ghost` (quiet button), `.asof`
  (muted small text), `.explain` 💡 macro (`{{ explain('key') }}`, content in
  `metrics.GRAPH_EXPLAINERS`), `.peer-snow` mini snowflake SVG pattern.
- Spacing/typography tokens `--space-*`, `--fs-*` exist — reuse.

## File map (the only files that matter)
- `app.py` — all routes. Deep-dive = `stock()`. Helpers: `_fx_ctx()`,
  `_fx_factor()`, `get_usdinr()`, `_stock_context()` (Otto grounding),
  `_ingest_stock()` (fetch+persist a new ticker).
- `db.py` — schema (`SCHEMA` string, `CREATE TABLE IF NOT EXISTS`), additive
  migrations in `_migrate(conn)` (pattern: check `PRAGMA table_info`, then
  `ALTER TABLE ... ADD COLUMN`). `get_conn()` returns row-factory conn.
- `fetch.py` — yfinance wrappers: `lookup()`, `snapshot()`, `deep()`,
  `price_history_resilient()`, `income_breakdowns()`.
- `refresh.py` — nightly cron: `main()` iterates the watchlist; `save_deep()`
  per ticker; `run_screener()`; `run_digest()`. systemd timer 22:00 CT.
- `metrics.py` — pure-Python analytics + SVG geometry (snowflake, bar_chart,
  trend_chart, income_sankey, sparkline). `GRAPH_EXPLAINERS` dict at bottom.
- `templates/` — Jinja. `base.html` (topbar/gear/theme/footer + global JS),
  `stock.html` (deep-dive, ~15 `<section class="card">`), `today.html`,
  `watchlist.html`, `home.html`, `strategies.html`, `_otto.html`,
  `_explain.html`, `_flags.html` (SVG flag macro `{{ flag('US') }}`).
- `auth.py` — blueprint; `current_user()`, `login_required`.
- `mailer.py` — `enabled()`, `send(to, subject, body)`; no-op when SMTP_* unset.
- `manage.py` — admin CLI; add new commands as subparsers, dry-run by default.
- `tests/` — pytest; temp-DB fixtures in `conftest.py`. Run:
  `.venv/bin/python -m pytest -q` (must stay green; ~29 tests, ~2s).

## Key tables (columns you'll touch)
- `stocks(ticker PK, name, exchange, sector, industry, currency, added_at)`
- `snapshots(ticker PK, price, prev_close, change_pct, market_cap, pe, pb, ps,
  eps, div_yield, wk52_low/high, rec_key, rec_mean, analyst_n, target_mean,
  fetched_at)`
- `price_history(ticker FK→stocks, d, close, PK(ticker,d))`
- `user_watchlist(user_id, ticker, added_at, PK(user_id,ticker))`
- `user_notes(user_id, ticker, body, updated_at, PK(user_id,ticker))`
- `digest(digest_date PK, body, model, picks_json, created_at)`
- `users(id, email, password_hash, name, market, created_at, session_token)`
- `executives(ticker, rank, name, title, age, pay, photo, edu, bio, enriched)`
- CSRF: every POST form needs
  `<input type="hidden" name="csrf" value="{{ csrf_token }}">` (global guard
  rejects otherwise, 400).

## Workflow (strict — main is branch-protected)
```sh
cd ~/Desktop/InvestRight && export PATH="$HOME/.local/bin:$PATH"
git checkout main && git pull
git checkout -b feature/<spec-slug>
# ...implement...
.venv/bin/python -m pytest -q          # must pass
git add <files> && git commit          # imperative subject; explain the why
git push -u origin feature/<spec-slug>
gh pr create --fill && gh pr merge --squash --delete-branch
```
- Commit trailer: `Co-Authored-By: Claude <noreply@anthropic.com>`
- **Do NOT deploy.** Deploy = Arka's call (`ssh` to the VM). Say it's ready.
- **Before starting**: `git status` — if the tree is dirty with someone else's
  work or another session seems active, STOP and ask.

## Cheap verification recipe (prefer this; browser only if spec says so)
```sh
.venv/bin/python -m pytest -q
# render any page without a browser:
.venv/bin/python -c "
import app; c = app.app.test_client()
r = c.get('/stock/AAPL'); print(r.status_code)
html = r.get_data(as_text=True); assert 'THING' in html"
```
Dev login for authed flows: `arka@example.com` / `testpass123` (POST /login
with csrf from a prior GET, using the test client's session).
Local data: `data/investright.db` has ~61 stocks incl. AAPL, MSFT, NVDA,
RELIANCE.NS with full history. Never write to it from a GET route.
