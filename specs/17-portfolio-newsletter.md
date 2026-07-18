# Spec 17 — Weekly newsletter, extended (portfolio · watchlist · earnings · movers)

Read `specs/_CONTEXT.md` first. Branch: `feature/portfolio-newsletter`.
Size: ~half day. DEPENDS ON spec 14 (holdings) and spec 16 (review signals).
Builds on spec 12's `weekly.py` + `users.weekly_email` opt-in + `mailer`.

## Design
Grow the Sunday note (spec 12) from "your watchlist moved" into a fuller,
registered-users-only digest with four layers, still plain text, still
disabled-safe (sends nothing while `SMTP_*` is unset), still written by the free
LLM (`digest.ask`) with an honest rule-based fallback:
1. **Your portfolio's week** — total P&L, biggest mover, any ⚑ review flags.
2. **Your watchlist's week** — notable moves on stocks you track (not owned).
3. **Upcoming earnings** — your holdings/watchlist reporting in the next ~7 days
   (spec 13's `stocks.next_earnings`).
4. **Top movers** — the week's biggest gainers/losers across the tracked US +
   India universe (market layer, same for everyone).

The account toggle stays the unsubscribe (no tokens). Every email ends with the
manage link + "Not investment advice" (and review flags keep spec 16's framing).

## Step 1 — build the context (weekly.py)
Extend `build_note(conn, user)` to assemble the four sections as text, each
guarded (a user with no holdings just omits section 1; empty watchlist omits 2):
- Portfolio: read `holdings` join snapshots + price_history 7-day move; sum P&L;
  pull `metrics.review_signals` per holding for the flag lines.
- Watchlist: existing spec-12 query, minus tickers already in the portfolio.
- Earnings: `SELECT ... WHERE next_earnings BETWEEN today AND +7d` over the
  user's holdings ∪ watchlist.
- Movers: top 3 up / 3 down by `change_pct` from snapshots (reuse the home-page
  query shape), display-ccy agnostic (report native % — it's a ratio).
Return `None` only when the user has **nothing** (no holdings AND no watchlist).

## Step 2 — one AI pass, honest fallback
Feed the assembled context to `digest.ask` with a prompt for a calm, 8–12
sentence note that walks the four layers, plain text, no advice. On any failure
(no key, quota, network) fall back to the structured text sections themselves
(labelled), so a real note always sends. (Same try/except shape spec 12 used.)

## Step 3 — nothing else changes structurally
`main()` already iterates `weekly_email=1` users and calls `mailer.send`. Keep the
manage-link + disclaimer footer. Still no HTML email. Still no systemd changes on
the VM from this PR (the spec-12 timer already runs `weekly.py`; if it isn't
installed yet, that stays the owner's step — repeat the install block in the PR
body for convenience).

## Step 4 — tests (tests/test_routes.py or a new test_weekly.py)
- `build_note` with holdings + watchlist (fake conn) → text containing a
  portfolio line, a movers line; no raise.
- `build_note` with neither → None (keep spec 12's case green).
- Fallback branch (monkeypatch `digest.ask` to raise) → returns the structured
  text, non-empty.
- Movers/earnings sections tolerate empty inputs.

## Verify
```sh
.venv/bin/python -m pytest -q
.venv/bin/python -c "
import weekly, db
with db.get_conn() as conn:
    u = conn.execute('SELECT * FROM users LIMIT 1').fetchone()
    print(weekly.build_note(conn, u)[:600] if u else 'no user')"
```
(Prints the fallback layers even without an AI key; with a key, the AI note.)

## Ship
PR title: `Weekly newsletter: portfolio + watchlist + earnings + movers layers`
