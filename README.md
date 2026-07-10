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

## Password reset email (optional)

Accounts work without it. Set these in `.env` and a **Forgot your password?**
link appears on the sign-in page; leave them unset and the reset routes 404
while the UI honestly says there's no self-serve reset.

```sh
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587                       # 465 also works
SMTP_USER=you@gmail.com
SMTP_PASS=xxxxxxxxxxxxxxxx          # a Google App Password, NOT your password
SMTP_FROM=InvestRight <you@gmail.com>   # optional
```

Gmail needs 2-Step Verification on, then an App Password from
[myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords).
Any SMTP relay works — `mailer.py` is stdlib `smtplib`, no new dependency.

Reset links are single-use, expire in an hour, and only a SHA-256 hash of the
token is stored. `/forgot` answers identically whether or not the address has
an account, so it can't be used to discover who's registered. Redeeming a link
signs the account out on every device.

Forgot the password with no relay configured? Reset it from the box:
`python manage.py set-password --email you@example.com --apply`.

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
deploy from `main` on the VM:

```sh
cd /opt/investright && ./deploy.sh    # git pull + restart + health check
```

Keep secrets out of git — `.env` (the Gemini key) is `.gitignore`d and lives
only on the VM (`chmod 600`), transferred out-of-band; `data/` (the SQLite DB)
is git-ignored too, so `git pull` never touches either. Branch protection on
`main` is optional but recommended once collaborators are added.
