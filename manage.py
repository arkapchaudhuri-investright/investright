#!/usr/bin/env python
"""Admin CLI for InvestRight — one-off maintenance jobs, run by hand.

Nothing here runs on a schedule or from the web app. Commands are idempotent and
default to a dry run; pass --apply to write.

    python manage.py migrate-watchlist --email you@example.com            # preview
    python manage.py migrate-watchlist --email you@example.com --apply    # commit
    python manage.py set-password --email you@example.com --apply         # prompts
    python manage.py backup --apply                                       # local only
    python manage.py backup --email you@gmail.com --apply                 # + offsite

migrate-watchlist (DESIGN §10.3): Phase 8 made the watchlist per-user, which
stranded the pre-accounts global `watchlist`/`notes` rows — they belong to
whoever was using the site before accounts existed. This copies them into that
person's account. The global `watchlist` table is left untouched: it is still
the union of tickers the nightly refresh fetches and /today screens (§10.4).

set-password (DESIGN §10.6): the manual password reset. Still the fallback when
no SMTP relay is configured, and the way in if you lock yourself out. Resetting
rotates the account's session token, signing out every device.

backup (DESIGN §12.7): the DB is the only thing here that can't be rebuilt from
code — accounts, watchlists and notes. Takes a consistent snapshot with SQLite's
online backup API (a plain file copy of a live DB can tear), reads it back with
PRAGMA integrity_check, gzips it, and rotates. With --email it also sends an
AES-256-encrypted copy offsite, because a backup on the same disk as the
original is not a backup.
"""
import argparse
import getpass
import gzip
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from werkzeug.security import generate_password_hash

from db import DB_PATH, get_conn, get_user_by_email, set_password


def _snapshot(dest):
    """Copy the live DB with SQLite's online backup API, not shutil.

    gunicorn may be mid-write, and a plain file copy of a live SQLite database
    can capture a torn page or miss the WAL. conn.backup() takes a consistent
    snapshot under the same locks SQLite uses itself.
    """
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(dest)
    try:
        with dst:
            src.backup(dst)
    finally:
        dst.close()
        src.close()


def _verify(path):
    """A backup nobody has read back is a rumour. Returns True if SQLite says
    the copy is intact."""
    conn = sqlite3.connect(path)
    try:
        return conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


def _backup():
    """Snapshot the DB next to itself before any write. Cheap insurance — these
    commands are hand-run against a live database."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = DB_PATH.with_suffix(f".db.bak-{stamp}")
    _snapshot(dest)
    return dest


def _encrypt(src, dest, passphrase):
    """AES-256 via the openssl CLI. The passphrase goes through the environment,
    never argv — argv is world-readable in `ps`."""
    subprocess.run(
        ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000",
         "-salt", "-pass", "env:IR_BACKUP_PASS", "-in", str(src), "-out", str(dest)],
        check=True, env={**os.environ, "IR_BACKUP_PASS": passphrase})


def backup(dir_, keep, email, apply_):
    out = Path(dir_)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = out / f"investright-{stamp}.db.gz"
    passphrase = os.environ.get("BACKUP_PASSPHRASE")

    # Refuse to put an unencrypted DB — password hashes, email addresses, private
    # notes — into an inbox. Local copies stay in place, protected by file perms.
    if email and not passphrase:
        sys.exit("Refusing to email an unencrypted database. Set BACKUP_PASSPHRASE "
                 "in .env first, and keep a copy of it somewhere that is NOT this "
                 "machine — without it the backup is unreadable.")

    # Prune oldest-first so that after this run's file lands there are exactly
    # `keep` of them. Timestamped names sort chronologically. The max(0, ...)
    # matters: `[:-keep + 1]` silently prunes nothing when keep == 1.
    existing = sorted(out.glob("investright-*.db.gz")) if out.is_dir() else []
    doomed = existing[:max(0, len(existing) - keep + 1)] if keep else []

    print(f"source     : {DB_PATH} ({DB_PATH.stat().st_size:,} bytes)")
    print(f"destination: {target}")
    print(f"retention  : keep {keep} → {len(existing)} on disk, "
          f"{len(doomed)} would be pruned")
    print(f"offsite    : {'email to ' + email if email else 'none (local only)'}"
          f"{' (encrypted)' if email else ''}")
    if not apply_:
        print("\nDRY RUN — nothing written. Re-run with --apply.")
        return

    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "snapshot.db"
        _snapshot(raw)
        if not _verify(raw):
            sys.exit("Snapshot failed PRAGMA integrity_check — refusing to keep it. "
                     "The live DB may be damaged; investigate before overwriting "
                     "any good backup.")
        with open(raw, "rb") as fh, gzip.open(target, "wb") as gz:
            gz.writelines(fh)
        print(f"\nwrote {target} ({target.stat().st_size:,} bytes, integrity ok)")

        if email:
            import mailer
            if not mailer.enabled():
                sys.exit("SMTP isn't configured (SMTP_* in .env), so there's "
                         "nowhere to send it. The local backup above is kept.")
            enc = Path(tmp) / f"{target.name}.enc"
            _encrypt(target, enc, passphrase)
            ok = mailer.send(
                email, f"InvestRight backup {stamp}",
                f"Encrypted SQLite backup of investright.db, taken {stamp}.\n\n"
                "Decrypt with:\n\n"
                f"    openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \\\n"
                f"      -in {enc.name} -out backup.db.gz\n"
                "    gunzip backup.db.gz\n\n"
                "The passphrase is BACKUP_PASSPHRASE from the server's .env. If "
                "you don't have it somewhere other than that server, this file is "
                "worthless — go copy it into your password manager now.\n",
                attachment=(enc.name, enc.read_bytes()))
            print(f"emailed {enc.name} ({enc.stat().st_size:,} bytes) to {email}"
                  if ok else "EMAIL FAILED — local backup kept; see the log above.")
            if not ok:
                sys.exit(1)

    for old in doomed:
        old.unlink()
        print(f"pruned {old.name}")


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


def set_user_password(email, password, apply_):
    email = email.strip().lower()
    with get_conn() as conn:
        user = get_user_by_email(conn, email)
        if not user:
            sys.exit(f"No account with email {email!r}.")
        print(f"Target account: id={user['id']} email={user['email']} name={user['name']!r}")

        if not apply_:
            print("\nDRY RUN — no password asked for, nothing written. "
                  "Re-run with --apply to set one.")
            return

        # Prompt rather than take it on argv, so the password never lands in
        # shell history or `ps` output.
        if not password:
            password = getpass.getpass("New password (hidden): ")
            if password != getpass.getpass("Confirm: "):
                sys.exit("The two passwords don't match. Nothing changed.")
        if len(password) < 8:
            sys.exit("Use a password of at least 8 characters. Nothing changed.")

        backup = _backup()
        print(f"Backed up DB -> {backup}")
        set_password(conn, user["id"], generate_password_hash(password))
        print(f"Password reset for {user['email']}. "
              "Every device signed into that account has been signed out.")


def purge_small_logos(apply=False):
    """Delete cached logos that fail logos._sharp() (e.g. a 16×16 favicon that
    renders blurry at header size). The next ingest / nightly refresh refetches
    each purged ticker via the new HQ source order (Wikidata P154 first)."""
    import logos
    victims = [p for p in sorted(logos.LOGO_DIR.glob("*.*")) if logos.is_small(p)]
    if not victims:
        print("all cached logos pass the sharpness gate — nothing to purge")
        return
    for p in victims:
        print(("deleting  " if apply else "would delete  ") + p.name)
        if apply:
            p.unlink()
    if not apply:
        print(f"\ndry run — {len(victims)} file(s); re-run with --apply to delete")


def backfill_industry(apply=False):
    """Fill stocks.industry for rows missing it. The nightly refresh only touches
    watchlist tickers, so a stock someone searched but never watchlisted keeps a
    blank industry (and its deep-dive header shows none). One light Yahoo lookup
    per missing stock; only writes when Yahoo actually returns an industry."""
    import time

    import fetch
    with get_conn() as conn:
        rows = [r["ticker"] for r in conn.execute(
            "SELECT ticker FROM stocks WHERE industry IS NULL OR industry='' "
            "ORDER BY ticker")]
        if not rows:
            print("every stock already has an industry — nothing to backfill")
            return
        print(f"{len(rows)} stock(s) missing industry"
              + ("" if apply else " (dry run)"))
        filled = 0
        for tk in rows:
            meta = fetch.lookup(tk)
            ind = (meta or {}).get("industry") or ""
            if not ind:
                print(f"  {tk}: Yahoo has no industry")
                continue
            print(f"  {tk}: {ind}" + ("" if apply else "  (would set)"))
            if apply:
                conn.execute("UPDATE stocks SET industry=? WHERE ticker=? AND "
                             "(industry IS NULL OR industry='')", (ind, tk))
                filled += 1
            time.sleep(0.3)                       # be polite to Yahoo
        conn.commit()
        print(f"\nset industry on {filled} stock(s)" if apply
              else "\ndry run — re-run with --apply to write")


def enrich_execs(reset=False):
    """Recover / fill leadership photos+bios across ALL stocks (the nightly
    refresh only enriches watchlist tickers). First re-links any cached photo
    file whose DB pointer went missing, then runs the (now throttle-resilient)
    Wikidata enrichment for every pending exec. Long-running — background it."""
    import re
    import time

    import refresh
    import wiki
    with get_conn() as conn:
        relinked = 0
        for row in conn.execute(
                "SELECT ticker, rank FROM executives WHERE photo IS NULL OR photo=''"):
            safe = re.sub(r"[^A-Za-z0-9._-]", "_", f"{row['ticker']}_{row['rank']}")
            f = wiki.EXEC_DIR / (safe + ".jpg")
            if f.exists():
                conn.execute("UPDATE executives SET photo=?, enriched=1 "
                             "WHERE ticker=? AND rank=?", (f.name, row["ticker"], row["rank"]))
                relinked += 1
        conn.commit()
        print(f"relinked {relinked} cached photo(s) whose DB pointer was lost")
        if reset:
            n = conn.execute("UPDATE executives SET enriched=0 "
                             "WHERE photo IS NULL OR photo=''").rowcount
            conn.commit()
            print(f"reset {n} photo-less row(s) to re-query")
        tickers = [r["ticker"] for r in conn.execute(
            "SELECT ticker FROM stocks ORDER BY ticker")]
        for t in tickers:
            try:
                refresh.enrich_executives(conn, t)
            except Exception as e:                       # transient throttle → retry next run
                print(f"  {t}: {type(e).__name__} (will retry next run)")
            conn.commit()
            time.sleep(1)
        got = conn.execute("SELECT COUNT(*) FROM executives "
                           "WHERE photo IS NOT NULL AND photo!=''").fetchone()[0]
        print(f"done — execs with photos now: {got}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("migrate-watchlist",
                       help="copy the pre-accounts global watchlist + notes into an account")
    m.add_argument("--email", required=True, help="email of the account to receive them")
    m.add_argument("--apply", action="store_true",
                   help="actually write (default is a dry run)")

    p = sub.add_parser("set-password",
                       help="reset an account's password (no self-serve reset exists, §10.6)")
    p.add_argument("--email", required=True, help="email of the account to reset")
    p.add_argument("--password", help="skip the hidden prompt (leaks into shell history)")
    p.add_argument("--apply", action="store_true",
                   help="actually write (default is a dry run)")

    ee = sub.add_parser("enrich-execs",
                        help="recover/fill leadership photos across all stocks "
                             "(relink lost files + throttle-resilient Wikidata)")
    ee.add_argument("--reset", action="store_true",
                    help="also re-queue photo-less rows already marked done")

    bi = sub.add_parser("backfill-industry",
                        help="fill stocks.industry for rows Yahoo can supply it for "
                             "(the nightly refresh only covers watchlist tickers)")
    bi.add_argument("--apply", action="store_true",
                    help="actually write (default is a dry run)")

    lg = sub.add_parser("purge-small-logos",
                        help="delete cached company logos below the sharpness "
                             "gate so the next ingest/refresh refetches HQ ones")
    lg.add_argument("--apply", action="store_true",
                    help="actually delete (default is a dry run)")

    b = sub.add_parser("backup", help="atomic, verified snapshot of the SQLite DB")
    b.add_argument("--dir", default=str(DB_PATH.parent / "backups"),
                   help="where the rotating .db.gz copies live")
    b.add_argument("--keep", type=int, default=14, help="how many to retain (default 14)")
    b.add_argument("--email", help="also mail an encrypted copy here (needs "
                                   "BACKUP_PASSPHRASE + SMTP_* in .env)")
    b.add_argument("--apply", action="store_true",
                   help="actually write (default is a dry run)")

    args = ap.parse_args()
    if args.cmd == "migrate-watchlist":
        migrate_watchlist(args.email, args.apply)
    elif args.cmd == "set-password":
        set_user_password(args.email, args.password, args.apply)
    elif args.cmd == "enrich-execs":
        enrich_execs(args.reset)
    elif args.cmd == "backfill-industry":
        backfill_industry(args.apply)
    elif args.cmd == "purge-small-logos":
        purge_small_logos(args.apply)
    elif args.cmd == "backup":
        backup(args.dir, args.keep, args.email, args.apply)


if __name__ == "__main__":
    main()
