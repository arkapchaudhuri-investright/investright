# Spec 12 — Weekly email digest (opt-in, per-user watchlist note)

Read `specs/_CONTEXT.md` first. Branch: `feature/weekly-email`. Size: ~half day.
DEPENDS ON: SMTP_* being set on prod eventually — build disabled-safe like
password reset: everything works, sending silently skips while
`mailer.enabled()` is False.

## Design
Opt-in on /account: "Email me Otto's weekly note". Sunday mornings a cron
builds, per opted-in user, a short plain-text note about THEIR watchlist
(prices, week moves, any screener hits) — written by the free LLM
(digest.py's provider) with an honest rule-based fallback when the AI is
unavailable. Plain text, no HTML email.

## Step 1 — schema (db.py `_migrate`)
```python
ucols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
if "weekly_email" not in ucols:
    conn.execute("ALTER TABLE users ADD COLUMN weekly_email INTEGER NOT NULL DEFAULT 0")
```

## Step 2 — /account toggle
Grep account.html + its POST handlers in auth.py. Add a small form/section:
checkbox (or two-button seg) posting to a new `POST /account/weekly`
(`@login_required`) that flips the flag. Show current state. If
`not mailer.enabled()`, add `.asof`: "Email isn't configured on the server
yet — you can opt in now; notes start once it is."

## Step 3 — the weekly job (new file `weekly.py`, mirror refresh.py's shape)
```python
def build_note(conn, user):  # returns text or None
    rows = conn.execute(
        "SELECT s.ticker, s.name, n.price, n.change_pct, s.currency "
        "FROM user_watchlist w JOIN stocks s ON s.ticker=w.ticker "
        "LEFT JOIN snapshots n ON n.ticker=w.ticker WHERE w.user_id=?",
        (user["id"],)).fetchall()
    if not rows: return None
    # week move: price vs close ~7 days ago from price_history (per ticker)
    ...
    context = "\n".join(f"{r['ticker']} {r['name']}: {r['price']} {r['currency']}, "
                        f"week {wk:+.1f}%" for ...)
    try:
        return digest.ask(context, "Write a calm 6-10 sentence weekly note "
            "summarising how this watchlist moved and anything notable. "
            "Plain text. No advice.")
    except Exception:
        return "Your week at a glance:\n" + context   # honest fallback

def main():
    with get_conn() as conn:
        users = conn.execute("SELECT * FROM users WHERE weekly_email=1").fetchall()
        for u in users:
            note = build_note(conn, u)
            if note:
                mailer.send(u["email"], "Otto's weekly note — InvestRight",
                            note + "\n\nManage: https://investright.us/account"
                            "\nNot investment advice.")
```
End every email with the manage link + disclaimer. NO unsubscribe tokens —
the account toggle is the unsubscribe (personal site, small user base).

## Step 4 — systemd units (systemd/investright-weekly.{service,timer})
Copy investright-symbols.* as the template. Timer: `OnCalendar=Sun *-*-* 08:00:00`,
`Persistent=true`. Service ExecStart: `.../python weekly.py`.
**OnFailure=investright-alert@%n.service goes in [Unit], NOT [Service]**
(systemd silently ignores it there — this bug happened before).
Install instructions in the PR body (cp units, daemon-reload, enable --now the
timer) — installing on the VM is Arka's/deploy's step, not yours.

## Step 5 — tests
- guest POST /account/weekly → 302 login.
- `build_note` with a fake conn/user returning no rows → None.

## Verify
```sh
.venv/bin/python -m pytest -q
.venv/bin/python -c "
import weekly, db
with db.get_conn() as conn:
    u = conn.execute('SELECT * FROM users LIMIT 1').fetchone()
    print(weekly.build_note(conn, u)[:300] if u else 'no user')"
```
(The fallback branch prints even without an AI key.)

## Ship
PR title: `Weekly email: opt-in Otto note on your watchlist (Sunday cron)`
