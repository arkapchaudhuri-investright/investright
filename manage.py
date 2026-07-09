#!/usr/bin/env python
"""Admin CLI for InvestRight — one-off maintenance jobs, run by hand.

Nothing here runs on a schedule or from the web app. Commands are idempotent and
default to a dry run; pass --apply to write.

    python manage.py migrate-watchlist --email you@example.com            # preview
    python manage.py migrate-watchlist --email you@example.com --apply    # commit

migrate-watchlist (DESIGN §10.3): Phase 8 made the watchlist per-user, which
stranded the pre-accounts global `watchlist`/`notes` rows — they belong to
whoever was using the site before accounts existed. This copies them into that
person's account. The global `watchlist` table is left untouched: it is still
the union of tickers the nightly refresh fetches and /today screens (§10.4).
"""
import argparse
import shutil
import sys
from datetime import datetime

from db import DB_PATH, get_conn, get_user_by_email


def _backup():
    """Copy the SQLite file next to itself before any write. Cheap insurance —
    this is a hand-run job against a live DB."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = DB_PATH.with_suffix(f".db.bak-{stamp}")
    shutil.copy2(DB_PATH, dest)
    return dest


def migrate_watchlist(email, apply_):
    email = email.strip().lower()
    with get_conn() as conn:
        user = get_user_by_email(conn, email)
        if not user:
            sys.exit(f"No account with email {email!r}. Register first, then re-run.")
        uid = user["id"]
        print(f"Target account: id={uid} email={user['email']} name={user['name']!r}")

        # Watchlist: every global ticker this user doesn't already track.
        watch = conn.execute(
            "SELECT w.ticker, w.added_at FROM watchlist w "
            "WHERE NOT EXISTS (SELECT 1 FROM user_watchlist u "
            "                  WHERE u.user_id=? AND u.ticker=w.ticker) "
            "ORDER BY w.ticker", (uid,)).fetchall()

        # Notes: only non-empty ones, and never overwrite a note the user already
        # wrote for that ticker.
        notes = conn.execute(
            "SELECT n.ticker, n.body, n.updated_at FROM notes n "
            "WHERE TRIM(COALESCE(n.body,'')) <> '' "
            "  AND NOT EXISTS (SELECT 1 FROM user_notes un "
            "                  WHERE un.user_id=? AND un.ticker=n.ticker) "
            "ORDER BY n.ticker", (uid,)).fetchall()

        print(f"\nwatchlist -> user_watchlist: {len(watch)} row(s)")
        for r in watch:
            print(f"  + {r['ticker']:<14} (added {r['added_at']})")
        print(f"\nnotes -> user_notes: {len(notes)} row(s)")
        for r in notes:
            preview = " ".join((r["body"] or "").split())[:60]
            print(f"  + {r['ticker']:<14} {preview!r}")

        if not watch and not notes:
            print("\nNothing to migrate — already done, or nothing to copy.")
            return

        if not apply_:
            print("\nDRY RUN — nothing written. Re-run with --apply to commit.")
            return

        backup = _backup()
        print(f"\nBacked up DB -> {backup}")
        conn.executemany(
            "INSERT OR IGNORE INTO user_watchlist (user_id,ticker,added_at) VALUES (?,?,?)",
            [(uid, r["ticker"], r["added_at"]) for r in watch])
        conn.executemany(
            "INSERT OR IGNORE INTO user_notes (user_id,ticker,body,updated_at) VALUES (?,?,?,?)",
            [(uid, r["ticker"], r["body"], r["updated_at"]) for r in notes])
        print(f"Wrote {len(watch)} watchlist row(s) and {len(notes)} note(s). Done.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("migrate-watchlist",
                       help="copy the pre-accounts global watchlist + notes into an account")
    m.add_argument("--email", required=True, help="email of the account to receive them")
    m.add_argument("--apply", action="store_true",
                   help="actually write (default is a dry run)")

    args = ap.parse_args()
    if args.cmd == "migrate-watchlist":
        migrate_watchlist(args.email, args.apply)


if __name__ == "__main__":
    main()
