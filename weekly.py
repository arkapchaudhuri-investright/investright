"""Weekly email digest — the Sunday cron side of spec 12.

For each user who opted in (users.weekly_email=1) build a short, plain-text note
about THEIR watchlist — this week's prices and moves — written by the free LLM
(digest.py's provider), with an honest rule-based fallback when the AI is
unavailable. Sending goes through mailer.send(), which is a no-op while SMTP is
unset, so this whole job is safe to run before email is configured.

Run manually:  .venv/bin/python weekly.py
Installed as:  a systemd timer, Sun 08:00 (see systemd/investright-weekly.*).
"""
import sys
from datetime import datetime

import digest
import mailer
from db import get_conn, init_db


def _week_move(conn, ticker, price):
    """This week's % move: latest price vs the close ~7 days ago (nearest saved
    close at or before the 7-days-ago mark). None when we can't compute it."""
    if price is None:
        return None
    row = conn.execute(
        "SELECT close FROM price_history WHERE ticker=? AND d <= date('now','-7 days') "
        "ORDER BY d DESC LIMIT 1", (ticker,)).fetchone()
    if not row or not row["close"]:
        return None
    return (price / row["close"] - 1) * 100


def build_note(conn, user):
    """A plain-text weekly note for one user, or None if their watchlist is empty.

    Tries the free LLM for a calm summary; falls back to an honest at-a-glance
    list (which also covers the no-AI-key case) so a note always goes out."""
    rows = conn.execute(
        "SELECT s.ticker, s.name, n.price, n.change_pct, s.currency "
        "FROM user_watchlist w JOIN stocks s ON s.ticker=w.ticker "
        "LEFT JOIN snapshots n ON n.ticker=w.ticker WHERE w.user_id=? "
        "ORDER BY w.added_at", (user["id"],)).fetchall()
    if not rows:
        return None

    lines = []
    for r in rows:
        wk = _week_move(conn, r["ticker"], r["price"])
        price = f"{r['price']:.2f} {r['currency']}" if r["price"] is not None else "price n/a"
        wk_txt = f"week {wk:+.1f}%" if wk is not None else "week n/a"
        lines.append(f"{r['ticker']} {r['name']}: {price}, {wk_txt}")
    context = "\n".join(lines)

    try:
        return digest.ask(context, "Write a calm 6-10 sentence weekly note "
            "summarising how this watchlist moved and anything notable. "
            "Plain text. No advice.")
    except Exception:
        return "Your week at a glance:\n" + context      # honest fallback


def main():
    init_db()
    sent = 0
    with get_conn() as conn:
        users = conn.execute("SELECT * FROM users WHERE weekly_email=1").fetchall()
        for u in users:
            try:
                note = build_note(conn, u)
            except Exception as e:                        # never let one user abort the run
                print(f"  build_note failed for user {u['id']}: {e}")
                continue
            if not note:
                continue
            ok = mailer.send(u["email"], "Otto's weekly note — InvestRight",
                             note + "\n\nManage: https://investright.us/account"
                             "\nNot investment advice.")
            if ok:
                sent += 1
    stamp = datetime.now().isoformat(timespec="seconds")
    print(f"{stamp}  weekly notes sent {sent}/{len(users)} opted-in"
          + ("" if mailer.enabled() else " (email disabled — nothing sent)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
