# InvestRight — Design & Build Spec

A self-hosted stock research web app: Simply Wall St-level per-stock insights + a
Finimize-style "interesting stocks" digest. Must be **always available without
Claude or a Pro membership** — build locally, then host 24/7 on a free VM.

> Status: design phase. No app code yet. This doc is the handoff for the build session.

---

## 1. Product decisions (locked)

| Decision | Choice |
|---|---|
| Markets | US (NYSE/Nasdaq) + India (NSE/BSE) |
| Coverage | Watchlist-first: curated ~20–100 tickers, not the whole market |
| Freshness | Daily refresh via cron (prices ~15-min delayed / EOD) |
| Budget | $0/month — free data + free hosting only |
| Stack | Flask + SQLite (reuse Career OS plumbing; brand-new UI) |
| Discovery | Rule-based screener + AI digest via **free Gemini/Groq** (never Claude) |
| Hosting | Local Mac now → Oracle Cloud "Always Free" VM for true 24/7 |

**Honest scope limits:** India data is shallower (yfinance ~4yr, no EDGAR
equivalent). Our "fair value" is a transparent DCF from *historical* growth, not
licensed analyst forecasts — label it clearly. Not investment advice.

---

## 2. Data sources (all free)

- **SEC EDGAR `companyfacts` API** — US, official, 10+ yr full financial statements. The goldmine for deep fundamentals + DCF inputs.
- **`yfinance`** (Yahoo) — prices, ratios, dividends, news; US + India (`.NS` / `.BO`). Unofficial → wrap every call in try/except, cache last-good.
- **Ticker autocomplete** — preload a static CSV of tickers (US from Nasdaq/SP500 list, India from NSE list); filter client-side, no API per keystroke.
- **AI digest** — Google Gemini free tier (or Groq). Nightly, summarize screener top picks + news.

---

## 3. Architecture

```
[cron nightly] → fetch (yfinance + EDGAR + Gemini) → compute rules + DCF → SQLite
                                                                             ↓
                                          Flask web app (24/7, reads only) → pages
```
Cron writes; the web app only reads → fast pages, resilient if an API is down.

### SQLite tables
`stocks` · `prices` · `fundamentals` · `snapshots` (precomputed metrics the UI
reads) · `health_checks` · `news` · `watchlist` · `notes` (your decision journal).

---

## 4. Screens

### Home (Claude-welcome feel)
Calm, centered greeting + one prominent search bar with live ticker autocomplete
(shows ticker · exchange · sector · price · % change). Watchlist = lightweight
top-right toggle with count badge. Watchlist pulse chips below search. Otto the
owl mascot above the greeting.

### Stock deep-dive (the SWS DNA)
Sections (cards), in order — mockup showed a starter set; full page has all of these:
1. Header: name, ticker · exchange badge, price + day change, ★ add-to-watchlist, Otto in mood.
2. **Snowflake** — 5-axis radar: Value · Future · Past · Health · Dividend (plain SVG polygon, scores computed in Python).
3. **Fair-value bar** — price vs. DCF value, "% undervalued", assumptions visible + editable.
4. **Health checks** — full ~28-check list below, grouped by axis, ✅/⚠️ rows, collapsed to top 6 with "show all".
5. **Past performance** — revenue + earnings + FCF bars (5–10yr US via EDGAR), vs. index line.
6. **Future** — historical-trend growth projection (clearly labeled "trend, not analyst forecast").
7. **Dividend** — yield history, payout ratio gauge, streak.
8. **Ownership & insiders** — US only: insider buys/sells from EDGAR Form 4 (free). Skip India v1.
9. **Competitors strip** — 3–4 peers with mini-snowflakes, click to jump.
10. **News** — latest headlines.
11. **My notes** — decision journal entry box (the feature SWS *doesn't* have; your edge).

### The ~28 health checks (all free from statements)
**Value (6):** below DCF fair value · significantly below (>20%) · P/E < industry · P/E < peer avg · P/B < industry · P/S sanity vs history.
**Future (5, historical-trend based):** revenue trend up · earnings trend up · growth > market avg · growth > industry avg · ROE/reinvestment trend improving.
**Past (5):** earnings grew over 5yr · growth accelerating vs 5yr avg · high-quality earnings (no big one-offs) · revenue trend positive · ROE > 20%.
**Health (6):** short-term assets > short-term liabilities · short-term assets > long-term liabilities · debt/equity < 40% · debt/equity falling over 5yr · debt covered by op. cash flow (>20%) · interest covered >3x by EBIT.
**Dividend (6):** yield > market avg · yield in top 25% of payers · payout ratio < 75% · stable (no cut in 10yr) · growing over 10yr · covered by both earnings and FCF.
Score per axis = checks passed → drives the snowflake. Start with the numeric ones; add judgment-y ones (earnings quality) later.

### Watchlist
Saved tickers with snapshot metrics + your notes/decision journal.

### Today (Finimize layer)
Rule-based screener results + nightly AI digest of what looks interesting and why.

---

## 5. Creative direction — "Claude-calm meets SWS-visual"

SWS is the inspiration for *what* is shown; Claude's design language is *how* it feels.
- **Calm over dense.** One idea per card, generous whitespace, hairline borders, 12px radius cards. Never SWS's darkest data-terminal density.
- **Color = meaning only.** Teal/green = good, amber = caution, red = risk. Everything else neutral. No decorative color.
- **Voice.** Plain-English, warm, sentence case, no jargon: "Debt looks manageable", "You're paying less than it's worth". Errors/empty states friendly via Otto.
- **Otto everywhere, quietly.** Tiny Otto in the header reacts to each stock's overall score (pleased / neutral / concerned / sleepy on empty states). Loading states = Otto blinking "crunching the numbers…".
- **Micro-motion, not animation soup.** Snowflake polygon eases in, fair-value bar fills on load, check rows fade in staggered. All CSS, all behind prefers-reduced-motion.
- **Serif for insight, sans for data.** Use a serif ("voice") only for the one-line takeaway at top of each card ("Apple looks 18% cheap, but watch the debt.") — Claude-style; numbers/labels stay sans.
- Dark mode default (SWS mood), light supported via CSS variables.

## 6. Otto the owl (mascot)

Wise/watchful theme fits "invest right". Single self-contained inline `<svg>` +
one `<style>` of `@keyframes`. No JS, no image files, scales crisply.

- Animations: gentle float, blink ~every 5s, pupils glance, wings flap, tufts wiggle, green trend-up badge with pulsing sparkles.
- Fixed colors (teal `#1D9E75` body, cream belly, amber beak) → works light + dark.
- Later: **moods** — same SVG, swap badge/tint: green (undervalued), amber (failed check), sleepy (empty watchlist).
- Accessibility: `role="img"` + label; wrap keyframes in `@media (prefers-reduced-motion: no-preference)`.

---

## 7. Build phases

1. Watchlist + price snapshot table (yfinance only) — prove the loop. ✅ **done**
2. Nightly cron → SQLite (the thing that makes it "an app"). ✅ **done**
3. Deep-dive page: health checks + DCF fair value + charts + news (EDGAR for US). ✅ **done**
4. Screener + AI digest ("Today" page). ✅ **done** — digest needs a free
   Gemini key in `.env` (see the comments in that file); everything else works without it.
5. Deploy to Oracle Free VM → fulfills "always available without Claude". ✅ **built** (see §9; not yet publicly reachable — DNS / security-list / cert blockers still open).
6. Features + open-source: dark/light toggle, footer, team page, GitHub. ← **in progress**

Phases 1–5 are built (see §9). Phase 6 features are built & verified locally; the VM redeploy + GitHub push are pending.

---

## 8. Phase 3 build spec (deep-dive page) — for the next session

**Target: Tier C (full spec).** Build as a ladder A → B → C so that every tier
leaves a *working, shippable* page even if the session ends early (token-frugal:
never leave the app broken mid-tier). Commit/verify at each tier boundary.

### 8.0 Ground rules (carry over from Phases 1–2)
- **Cron writes, web reads** (§3). Every new data fetch (EDGAR, fundamentals,
  news) runs in the nightly `refresh.py`, persists to SQLite, and the page reads
  only from the DB. The page must render fully with Yahoo/EDGAR *down*.
- **Wrap every external call in try/except; cache last-good.** Same discipline as
  `fetch.py`. New fetchers live in `fetch.py` (yfinance) and a new `edgar.py` (SEC).
- **Reuse, don't fork.** `db.py` owns schema + writers; `fetch.py` owns yfinance;
  templates extend `base.html`; Otto via `_otto.html` with a `mood`.
- **Label honesty (§1).** DCF value = "our estimate from *historical* trend, not
  an analyst forecast." Every projected/derived number says so. Not investment advice.
- **India is shallow by design.** EDGAR is US-only. India stocks show yfinance-only
  checks/charts and a friendly "deeper data is US-only for now" note from Otto.

### 8.1 New route & files
- Route `GET /stock/<ticker>` in `app.py` → `templates/stock.html`. Reads only DB.
- `templates/stock.html` — the deep-dive, cards in DESIGN.md §4 order.
- `edgar.py` — SEC `companyfacts` fetcher (US only); maps GAAP concepts → tidy
  multi-year series. No API key; set a `User-Agent` header (SEC requires it).
- `metrics.py` — pure functions: health checks, axis scores, DCF. No I/O, unit-testable.
- Snowflake + fair-value bar + charts = **inline SVG built in Python/Jinja** (no
  JS chart libs, no CDN — must work offline on the Oracle VM). Reuse Otto's
  CSS-animation approach; respect `prefers-reduced-motion`.
- Make watchlist rows + home chips link to `/stock/<ticker>`.

### 8.2 Schema additions (add to `db.py` SCHEMA; `init_db` is idempotent)
- `fundamentals(ticker, fiscal_year, revenue, net_income, fcf, total_assets,
  total_liab, current_assets, current_liab, long_term_debt, equity, ebit,
  op_cash_flow, shares, dividends_paid, source, fetched_at)` — PK `(ticker, fiscal_year)`.
  `source` ∈ {edgar, yfinance}.
- `health_checks(ticker, axis, check_id, label, passed, detail, computed_at)` —
  PK `(ticker, check_id)`. `axis` ∈ {value,future,past,health,dividend}.
- `snapshots`: add nullable `pb`, `ps`, `eps`, `industry_pe` (needed by value checks).
- `news(ticker, published_at, title, publisher, url, fetched_at)` — PK `(ticker,url)`.
- `notes(ticker, body, updated_at)` — PK `ticker`; the decision journal (our edge, §4.11).
- `dcf(ticker, fair_value, upside_pct, growth_used, discount_rate, terminal_growth,
  assumptions_json, computed_at)` — PK `ticker`; store inputs so the page can show/edit them.

### 8.3 The ~28 health checks (DESIGN.md §4) — `metrics.py`
Implement the **numeric** checks first (value/health/dividend are pure arithmetic;
past/future from the multi-year series). Defer judgment-y ones (earnings quality,
one-offs) — return `None`/"n/a" rather than guess. Each check →
`{axis, check_id, label, passed: bool|None, detail: "P/E 18.2 vs industry 24.1"}`.
Axis score = passed / applicable(non-None). Missing data ⇒ check is n/a, not a fail.
**Snowflake** = 5 axis scores 0–1 → pentagon polygon points computed in Python.

### 8.4 DCF (`metrics.py`, clearly-labeled estimate)
2-stage on **FCF** (fallback: owner earnings ≈ net income): historical CAGR (capped,
e.g. 5-yr, clamp to ±15%) for years 1–5, fade to terminal growth (~2.5%), discount
at ~9% (single transparent rate v1), sum PV + terminal value, ÷ shares → per-share
fair value. Compare to price → `upside_pct`. Persist inputs; page shows them and
allows override via querystring (recompute on the fly, don't rewrite cron's row).

### 8.5 Tiers (each independently shippable — verify before moving on)
- **Tier A — yfinance-only, uniform US+India.** `/stock/<ticker>` renders: header
  (name, exchange badge, price/day-change, ★, Otto mood by overall score),
  snowflake, fair-value bar, health checks (the subset computable from yfinance
  snapshot + its ~4yr financials), collapsed-to-6 checks list, news (yfinance
  `.news`), notes journal (save/load). `refresh.py` also writes yfinance
  fundamentals + news + computes checks/DCF nightly.
  *Accept:* page loads for a US and an India ticker with DB-only reads, snowflake +
  bar + ≥12 checks render, notes persist, degrades gracefully with Yahoo stubbed out.
- **Tier B — EDGAR US deep.** Add `edgar.py`; for US tickers replace shallow
  fundamentals with 10-yr EDGAR series → full ~28 numeric checks, real
  past-performance charts (revenue/earnings/FCF bars vs index line), better DCF.
  India unchanged. *Accept:* a US ticker shows 10-yr bars + the fuller check set
  sourced `edgar`; India still works on the yfinance path.
- **Tier C — full page.** Add future-projection card (trend, labeled), dividend
  card (yield history, payout gauge, streak), competitors strip (3–4 peers w/
  mini-snowflakes, link to jump), ownership/insiders (US-only, EDGAR Form 4).
  *Accept:* all §4 cards present; competitors clickable; India hides US-only cards
  with Otto's note; whole page renders offline from SQLite.
  *Known sub-decisions for this tier:*
  - **Peers have no free "similar companies" API.** Hardcode a small peer map
    (e.g. `data/peers.json` or a dict in `metrics.py`) for watchlist tickers —
    don't try to discover peers dynamically.
  - **Insider trading is a different EDGAR surface than `companyfacts`.** Form 4
    data comes from the filing index / `submissions` API, not the XBRL facts
    `edgar.py` already fetches — expect a second fetcher function (or a new
    module) and treat it as the long pole of this tier.

### 8.6 Out of scope for Phase 3
Screener + AI digest = Phase 4 ("Today"). Deploy = Phase 5. Don't pull them in.

---

## 9. Build log
- **Phase 1** (2026-07-05): watchlist + snapshot table, yfinance + last-good cache,
  USD/INR toggle, Otto, client-side autocomplete. Verified.
- **Phase 2** (2026-07-06): `refresh.py` nightly job (all snapshots + USD/INR) →
  SQLite; `save_snapshot` shared via `db.py`; crontab `0 22 * * *` (10 PM CT — after
  US close, India already closed). Verified under real cron incl. macOS Desktop
  access. Caveat: cron doesn't retry missed runs (Mac asleep ⇒ skipped; ↻ button is
  the manual fallback; goes away on the always-on VM in Phase 5).
- **Phase 3 Tier A** (2026-07-06): `/stock/<ticker>` deep-dive, yfinance-only,
  uniform US+India. New `metrics.py` (pure: ~28 checks incl. honest n/a rows, axis
  scores, 2-stage DCF, snowflake geometry, Otto mood, serif takeaway); `fetch.deep()`
  (multi-year statements + ratios + news, each guarded); `db.py` +fundamentals /
  health_checks / news / notes / dcf tables and snapshot pb/ps/eps/industry_pe
  (idempotent migration); `refresh.save_deep()` wired into cron, ↻ and /add. Page
  reads DB only. Verified: AAPL (20 applicable checks) + RELIANCE.NS (21) render
  snowflake + fair-value bar + checks + news + notes; notes persist; DCF assumptions
  overridable via querystring without rewriting cron's row; renders HTTP 200 with
  yfinance stubbed to raise (graceful degrade); mobile reflow OK.
- **Phase 3 Tier B** (2026-07-06): new `edgar.py` — SEC `companyfacts` fetcher,
  US-only, no API key (SEC just requires a `User-Agent`); merges renamed GAAP
  tags (e.g. the ASC 606 revenue rename) into one continuous series; annual
  facts picked by `form=10-K` + duration ~350–380 days (SEC's own `fy` field
  tags the *filing* year, not the period, so it can't be used as the group key).
  `refresh.save_deep()` now tries EDGAR first for tickers with no exchange
  suffix, falling back to the yfinance path on any failure; India (`.NS`/`.BO`)
  untouched. New `metrics.bar_chart()` / `performance_charts()` — pure-Python
  SVG geometry (no chart libs) for revenue/earnings/FCF bars with a dashed
  market-average benchmark line; wired into a new "Past performance" card in
  `stock.html` (§4.5), with an Otto note on India pages explaining the 10-yr
  depth is US-only. Verified: AAPL now carries 10 fiscal years sourced `edgar`
  (2016–2025, values cross-checked against real 10-K figures) vs. RELIANCE.NS
  unchanged at 4 years sourced `yfinance`; trend checks (`rev_trend_up`,
  `roe_trend`, `growth_accelerating`, etc.) now span the full 9-year gap instead
  of ~3; page renders HTTP 200 for both tickers with no template errors.
- **Phase 3 Tier C** (2026-07-06) — completes Phase 3. Future card: revenue +
  earnings bars extended 3yr at the capped historical CAGR (dashed bars, labeled
  "trend, not analyst forecast"); geometry via `bar_chart(n_projected=)`.
  Dividend card: yield vs market, payout gauge w/ 75% line, no-cut streak,
  history bars — charts *total* cash paid, not per-share (statement share
  counts aren't split-adjusted; per-share showed a phantom cut at AAPL's 2020
  4:1). Competitors strip: hardcoded `metrics.PEERS` map (§8.5 sub-decision),
  mini-snowflakes from peers' saved checks, links to `/stock/<peer>`;
  `refresh.ensure_stock()`/`peer_symbols()` pull peers (stocks row + snapshot +
  deep) into the nightly run, ↻ and /add, which also un-n/a'd the `pe_peer`
  value check (peer-average P/E from saved snapshots). Ownership card:
  `edgar.insider_transactions()` — the *submissions* API + per-filing ownership
  XML (separate surface from companyfacts, as §8.5 warned), one row per
  (filing, tx code), P/S/A/M/G/F code map, new `insider_tx` table,
  delete-then-insert keeps last-good on EDGAR failure. Verified: AAPL renders
  all §4.1–4.11 cards (21 applicable checks, real Form 4 rows cross-checked —
  Levinson/Cook-era sales, roles, prices); peer chips click through to
  /stock/MSFT; RELIANCE.NS keeps working on the yfinance path with Otto's
  US-only note replacing the insiders list; both pages HTTP 200 with all
  outbound sockets blocked (DB-only reads); no console errors; mobile (375px)
  reflows without horizontal overflow. **Phase 3 complete — Phase 4 (screener +
  AI digest) next.**
- **Phase 4** (2026-07-06) — "Today" page (§4): rule-based screener + nightly AI
  digest, same cron-writes/web-reads split (§8.0). New `metrics.screen()` — pure,
  transparent 0–100 blend of rows the cron already saved, **no new fetchers and
  no AI in the ranking**: avg axis score ×50 + DCF upside ×30 (capped at +50%,
  where the trend model stretches — the Indian oil PSUs' +200–500% gaps earn a
  chip, not unbounded score) + 90-day net open-market insider buys ×10 + dividend
  axis ×10, plus plain-English reason chips (greens first, cautions last).
  `refresh.run_screener()` assembles inputs from health_checks/dcf/insider_tx/
  snapshots → new `screener` table; re-run on ↻ /add /remove too (DB-only,
  instant) so the ranking never lags its inputs. Also un-n/a'd the `yield_top25`
  dividend check as promised — top-quartile cut among saved payers. New
  `digest.py`: Gemini (`GEMINI_API_KEY`, model gemini-2.5-flash) or Groq
  fallback, key in `.env` via a tiny loader (no new dependency, key stays out of
  code and logged URLs); `refresh.run_digest()` summarizes the top 5 picks +
  their reasons + saved headlines into a new `digest` table, cron-only. Any
  failure ⇒ "skipped (…)" in the log and the page keeps the last saved digest,
  dated, with an amber note if >1 day old (§8.0 last-good). New `GET /today`
  (DB reads only) → `templates/today.html`: Otto + serif takeaway, "Otto's
  read" card labeled honestly ("summarizing the rule-based screener output —
  not investment advice", §1), ranked rows with mini-snowflakes, native-currency
  prices, watchlist ★ vs peer tags, score breakdown on hover, "How the score
  works" footnote; ☀ Today link in the top bar. Verified: 9 tickers ranked with
  real reasons; digest gracefully absent with no key AND with the API stubbed to
  raise (no row written, no crash, both statuses logged); test-client 200s for
  digest present/stale/absent + home + AAPL + RELIANCE.NS regressions; desktop
  + mobile (375px, no horizontal overflow) screenshots clean, no console
  errors. Awaiting a Gemini key in `.env` for the first real digest — tonight's
  cron (or a manual refresh.py run) writes it. **Phase 4 complete — Phase 5
  (deploy) next.**
- **Phase 5** (2026-07-07) — deployed to Oracle Cloud Always Free VM (`investright.app`). Infrastructure: **US-Chicago region**, **VM.Standard.E5.Flex (1 OCPU / 6 GB)** (AMD, always available, within free tier). **Python / gunicorn** replaces Flask dev server (2-worker sync, 127.0.0.1:8700, auto-restart). **Caddy 2.8.4 reverse proxy** on ports 80/443, HTTP basicauth (`arka` / bcrypt-hashed password) gates public access, auto-redirects HTTP→HTTPS with Let's Encrypt certs (once DNS is live for `investright.app`). **Nightly refresh** moved from Mac crontab to **systemd timer** (investright-refresh.timer, `OnCalendar=*-*-* 22:00:00`, `Persistent=true` re-runs missed slots). VM timezone set to `America/Chicago` (DST-aware); iptables rules persist 80/443 across reboots. `.env` (`GEMINI_API_KEY`) transferred OOB, `chmod 600`, never in git. **Seed run verified:** DB populated, timer ready, all three services (gunicorn / caddy / timer) auto-restart on reboot. **Next:** user adds DNS A-record `investright.app → 170.9.255.191`, Caddy auto-provisions HTTPS, app live at `https://investright.app`. Phase 1–4 features unchanged; deployment-only changes per the scope lock. **Phase 5 complete — InvestRight now runs 24/7 on Oracle's free tier, always-on nightly refresh, no Mac dependency.**
- **Phase 6** (2026-07-07) — features + open-source; Phase 5's "deployment-only"
  lock lifted. **Dark/light toggle** (resolves §5's open dark-vs-light question):
  the app was dark-*only*, so this built a full light palette via CSS variables —
  `:root[data-theme="light"]` overrides plus a `@media (prefers-color-scheme: light)`
  fallback scoped to `:not([data-theme])`, so a first-time visitor follows their OS
  and an explicit choice always wins (higher specificity). Semantic hues
  (up/down/amber/accent) are darkened in light mode for AA text contrast; a new
  `--shadow` var softens the suggest-box shadow. Server mirrors the USD/INR pattern:
  `theme` read from `?theme=` → cookie → unset via a context processor and persisted
  by an `after_request` hook; `base.html` renders `<html data-theme>` only when a
  cookie exists (no FOUC; otherwise the media query decides). A header `.theme-seg`
  toggle (☀/☾) flips the attribute + cookie instantly via a tiny inline script (no
  reload), with the `?theme=` links as the no-JS fallback; theme transitions gated
  behind `prefers-reduced-motion`. Otto is theme-proof (fixed teal/cream/amber, belly
  nested inside the body). **Footer credit** "Designed with love by Arkaprava
  Chaudhuri ♥" added once in `base.html` → inherited by every page. **Team page** —
  new `/team` route + `templates/team.html` ("Meet Our Team" / "Our team is growing")
  and a Team nav tab; profile card = square-cropped `static/arka.jpg` (from the
  uploaded 1600×1200 via `sips`), role, a 3-sentence lede + a `<details>` read-more
  with the full bio, and a LinkedIn link with an inline-SVG icon (no CDN, per the
  offline-on-VM rule). Mobile topbar now wraps (Team + toggle would otherwise
  overflow 375px), and the home watchlist — an 8-column table that was clipping 5
  columns off-screen at 375px — becomes iOS-Stocks-style collapsible cards on phones:
  a minimized row (ticker · name · price · coloured change pill) that taps open via
  native `<details>` (no JS) to reveal mkt cap / P/E / yield / the 52-week range bar
  plus a "Full analysis →" deep-dive link and remove. Desktop keeps the full table
  (`.wl-cards` is display:none above 560px; the table is display:none below).
  **Search-first flow:** the home search bar's primary action is now **Analyze** —
  a new `POST /analyze` route that fetches-on-first-sight (via a shared
  `_ingest_stock()` helper extracted from `/add`) then redirects to the deep-dive,
  keeping `/stock` itself DB-only (§3). **Add to watchlist** is demoted to a
  separate, quieter secondary button (search bar + the deep-dive header's ☆/★
  toggle, so a just-analyzed stock can be watchlisted from its own page). Desktop
  watchlist rows also gained an explicit **Full analysis →** CTA (the mobile cards
  already had one). **GitHub**: repo initialized (`main`); `.gitignore` excludes
  `.env`/`.venv`/`data`/`__pycache__`/`*.db`/logs/`.claude`/the source photo; secret
  sweep clean (only digest.py's `.env`-format comments match — no real key);
  `arka.jpg` tracked. Verified on :8700 — all four page types + Otto legible in both
  themes, toggle instant/reload-free/cookie-persisted, read-more works, no server or
  console errors, 375px with no horizontal overflow.
- **Phase 6 ship-out** (2026-07-07) — the pending push + deploy + live-site blockers
  are all now **DONE; investright.us is publicly live over HTTPS.** (1) **GitHub**:
  `gh auth login` (device flow) as `arkapchaudhuri-investright`, then
  `gh repo create investright --public --source=. --push` →
  https://github.com/arkapchaudhuri-investright/investright (PUBLIC). README gained a
  feature-branch → PR flow. (2) **Three live-site blockers cleared:** (a) Oracle VCN
  Default Security List — user added ingress for TCP 80 & 443 from `0.0.0.0/0`;
  (b) GoDaddy — user deleted the domain *Forwarding* rule (the source of the parking
  IPs 13.248.243.5 / 76.223.105.230) and pointed the `@` A-record at 170.9.255.191;
  (c) **Caddy cert storage** — root cause was Caddy running as `www-data` under
  `ProtectSystem=strict`, so its storage resolved to www-data's home `/var/www`
  (nonexistent + read-only) → `mkdir /var/www: read-only file system`. Fixed with a
  systemd drop-in (`/etc/systemd/system/caddy.service.d/override.conf`):
  `StateDirectory=caddy` (creates+chowns `/var/lib/caddy`, auto-adds it to the
  sandbox's writable paths) + `Environment=XDG_DATA_HOME=/var/lib/caddy/data` /
  `XDG_CONFIG_HOME=/var/lib/caddy/config`. After clearing a+b, one `systemctl restart
  caddy` → Let's Encrypt HTTP-01 validated and cert issued (CN=investright.us, valid
  ~90d, auto-renews). Verified externally: `nslookup`→170.9.255.191, 80/443 OPEN,
  HTTPS 401 basic-auth gate, HTTP→HTTPS 308. (3) **App redeploy**: rsync'd the 6
  changed Phase-6 files (app.py, base/home/team.html, style.css, arka.jpg;
  checksum-diffed, VM backup tar'd) + `systemctl restart investright`; verified all
  features on localhost:8700 (theme toggle, /team, search-first `/analyze` → 302
  deep-dive that fetches+persists, no regressions on /today or /stock). (4) **Deploy
  model switched to git-pull**: `/opt/investright` is now a clone of the GitHub repo;
  `.env` + `data/` stay git-ignored/out-of-band (untouched by pull); added
  `deploy.sh` (git pull + pip + restart + health check). Future deploys:
  `cd /opt/investright && ./deploy.sh`. Old rsync dir kept as
  `/opt/investright.rsync-bak`. **Phase 6 complete — InvestRight is open-source and
  publicly live at https://investright.us.**
- **Phase 6c — onboarding market + activity log + gear polish** (2026-07-08).
  (1) **Market onboarding:** the first-visit welcome popup now asks "Where do you
  want to invest?" after the name — a 3-up segmented control (🇺🇸 US / 🇮🇳 India /
  Both, radios styled via `input:checked + span`), stored per-browser in
  `localStorage` (`ir_market`, next to `ir_name`). It **defaults the display
  currency** (India → ₹INR, US/Both → $USD) by writing the existing `ccy` cookie —
  India reloads once so the server re-converts, US/Both stay USD without a reload.
  A matching **Market row was added to the ⚙ settings menu** so the choice is
  changeable later (JS-managed, unlike the server-linked currency/theme: it writes
  localStorage + the ccy cookie and reloads). The home **autocomplete now filters
  to the chosen market** — read live from localStorage each keystroke via the
  ticker's exchange tag (US = NYSE/NASDAQ, India = NSE/BSE; `.NS`/`.BO` carry
  NSE/BSE), so US hides Indian tickers, India shows only them, Both shows all.
  Verified on :8700 desktop + mobile (375px), light + dark. (2) **Activity logging
  (server-side, since localStorage is invisible to the owner):** new `events` table
  (ts, anonymous per-browser cookie UUID `vid`, self-reported name + market, action
  view/analyze/add/remove, ticker, path, coarse UA/IP) written by a best-effort
  `_log()` (try/except — never breaks a render). name/market ride along as
  first-party cookies mirrored from localStorage (base.html also back-fills them for
  pre-existing visitors); IP prefers Caddy's `X-Forwarded-For`. Read via a
  **secret-gated `/admin?key=<ADMIN_KEY>`** (`hmac.compare_digest`; 404 when the key
  is wrong or unset, so the page never advertises itself) → `admin.html` table +
  event/visitor counts + most-touched tickers, with an honest banner that identity
  is unverified pre-accounts. `ADMIN_KEY` lives in `.env` (git-ignored; **must be
  added to the VM's `.env` too**, else /admin stays 404). (3) **Gear icon:** the ⚙
  emoji (off-centre, muted) is now an **inline SVG gear filled brand green**
  (`var(--accent)`, no CDN), perfectly centred by the button's flexbox. Verified:
  all pages 200, no console errors, secret gate 404/404/200, events captured with
  name+market+ticker. **Later phase:** real email/password accounts — the per-browser
  name + market migrate into the account then.
