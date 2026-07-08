# InvestRight

Self-hosted stock research: Simply Wall St-style insights on a watchlist of
US + India stocks, free data only. See [DESIGN.md](DESIGN.md) for the full spec.

## Run

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # once
.venv/bin/python app.py    # → http://localhost:8700
```

## Status

- **Phase 1 (done):** watchlist + price snapshot table via yfinance.
  Add tickers (US plain, India `.NS`/`.BO`), quotes cached in SQLite
  (`data/investright.db`) — last-good values survive Yahoo outages.
- **Phase 2 (done):** nightly cron — `refresh.py` rewrites every watchlist
  snapshot plus the USD/INR rate at 10 PM daily (after both markets close),
  logging to `data/refresh.log`. See `crontab -l`. The Mac must be awake at
  10 PM; the ↻ button in the app remains as a manual fallback.
- **Phase 3 (done):** `/stock/<ticker>` deep-dive — snowflake, health checks,
  DCF fair value, 10-yr EDGAR history (US), dividends, insiders, peers, news,
  and a notes journal. All read from SQLite; cron writes.
- **Phase 4 (done):** `/today` — a nightly rule-based screener ranks the
  watchlist + peers from the saved checks/DCF/insider/dividend rows, and a free
  LLM (Gemini, or Groq) writes a short digest of the top picks. Put a key in
  `.env` (see comments in that file) to turn the digest on; without one the
  screener still works and the page says the digest is off.
- Phase 5 next: deploy to an always-free VM so it runs without this Mac.

Not investment advice.
