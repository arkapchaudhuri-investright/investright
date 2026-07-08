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
- **Phase 5 (done):** deployed to an Oracle Cloud Always Free VM (Chicago,
  `VM.Standard.E5.Flex`, $0/mo) so it runs 24/7 without this Mac. Gunicorn
  (2 workers, `127.0.0.1:8700`) behind a Caddy reverse proxy (auto-HTTPS via
  Let's Encrypt, HTTP basic-auth gate). The nightly refresh runs as a systemd
  timer (`22:00` America/Chicago, `Persistent=true`).
- **Phase 6 (done):** dark/light theme toggle (cookie-persisted, follows OS by
  default), a `/team` page, an iOS-Stocks-style collapsible watchlist on mobile,
  a search-first "Analyze" flow (`POST /analyze` fetches-on-first-sight, then
  deep-dives; add-to-watchlist is a separate action), and open-sourcing on
  GitHub. Live at **https://investright.us**.

Not investment advice.

## Contributing / development flow

`main` is the deployed branch. Work on a feature branch and open a PR so
changes can be reviewed before they merge:

```sh
git checkout -b feature/<short-name>   # branch off main
# ...make changes, commit...
git push -u origin feature/<short-name>
gh pr create --fill                    # open a PR against main
```

Review and merge the PR on GitHub (squash-merge keeps `main` linear), then
deploy from `main` on the VM. Keep secrets out of git — `.env` (the Gemini
key) is `.gitignore`d and lives only on the VM (`chmod 600`), transferred
out-of-band. Branch protection on `main` is optional but recommended once
collaborators are added.
