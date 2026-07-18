Work in ~/Desktop/InvestRight. Implement the portfolio spec files ONE AT A TIME,
in this order: 14, 15, 16, 17. For each spec, do the FULL cycle before moving to
the next — do not batch. These four are sequential and dependent: 15/16/17 all
build on 14, and 17 also builds on 16, so the order is not optional.

Setup once:
  export PATH="$HOME/.local/bin:$PATH"   # gh lives here
  git checkout main && git pull          # start clean (abandon any stale local branch)
  git status                             # if the tree is dirty with another
                                         # session's work, STOP and ask

Read specs/_CONTEXT.md ONCE first (it has the app's rules, tokens, file map,
schemas, workflow, and a test-client verify recipe). Do NOT read DESIGN.md,
README.md, or memory files — that wastes tokens.

Then for EACH spec in order (specs/14-portfolio-standalone.md,
specs/15-portfolio-import.md, specs/16-portfolio-signals.md,
specs/17-portfolio-newsletter.md):
  1. Read ONLY that spec + the files it names.
  2. git checkout main && git pull ; git checkout -b feature/<spec-slug>
  3. Implement it exactly as written. If the code doesn't match a spec anchor,
     STOP and tell me instead of improvising.
  4. .venv/bin/python -m pytest -q   (must stay green)
  5. Verify exactly as the spec's "Verify" section says (test-client one-liners).
     The suite has no authed-login fixture, so for authed flows create a
     throwaway user in the LOCAL db, set session uid/stok/csrf on the test
     client, exercise the flow, then delete the throwaway rows (never leave test
     data in data/investright.db, and never write to it from a GET route).
  6. Commit (imperative subject + why; trailer:
     Co-Authored-By: Claude <noreply@anthropic.com>), push, gh pr create --fill
     (or --title/--body when the PR body needs the owner's manual steps),
     gh pr merge --squash --delete-branch, then sync local main:
       git checkout main && git fetch origin && git reset --hard origin/main
  7. DEPLOY it:
     ssh -i ~/.ssh/investright_oracle ubuntu@170.9.255.191 'cd /opt/investright && ./deploy.sh'
     then curl the live page to confirm (e.g.
     curl -s -o /dev/null -w "%{http_code}\n" https://investright.us/portfolio).

Spec-specific notes:
  - 14 (standalone portfolio) is a PARTIAL REVERT of spec 11: it removes the
    holdings UI/columns/totals/donut from /watchlist and rehomes them under a new
    /portfolio tab. That teardown is intended — do it as the spec says.
  - 14 migration: the spec-11 → holdings data move lives in a NEW manage.py
    subcommand (migrate-holdings, dry-run by default), NOT in db._migrate. After
    14 is merged + deployed, run it on the VM over SSH:
      ssh -i ~/.ssh/investright_oracle ubuntu@170.9.255.191 \
        'cd /opt/investright && .venv/bin/python manage.py migrate-holdings'          # dry-run first
      ssh -i ~/.ssh/investright_oracle ubuntu@170.9.255.191 \
        'cd /opt/investright && .venv/bin/python manage.py migrate-holdings --commit' # then commit
  - Migration-race lesson (bit us on spec 12): a brand-new COLUMN added in
    db._migrate races the two gunicorn workers on first deploy — one worker 502s
    on "duplicate column" then systemd auto-restarts clean. Prefer new TABLES via
    CREATE IF NOT EXISTS (safe). If a deploy health check fails, check
    `journalctl -u investright -n50` on the VM and confirm the site self-healed
    (curl localhost:8700) before treating it as broken.
  - 15 (import): NOTHING is written to the DB until the user confirms on the
    symbol-match screen. Resolve broker symbols via the app's existing
    fetch.search/lookup; broker header signatures are best-effort (a miss falls
    back to the generic column-mapper, never an error).
  - 16 (signals): flags are "worth a look" ONLY — never buy/sell/trim/hold. Keep
    the standing "Not investment advice" disclaimer on the section. Signals are
    derived at request time from already-saved rows (no DB write in the GET).
  - 17 (newsletter): build it disabled-safe (sends nothing while SMTP_* unset).
    Do NOT install or change systemd units on the VM — spec 12's
    investright-weekly.timer already runs weekly.py. If that timer still isn't
    installed on the VM, repeat the install block in the PR body only (owner's
    manual step); it stays disabled until SMTP_* is set in the VM .env.

Constraints (from _CONTEXT.md): $0 spend, vanilla CSS/JS (no frameworks, no CDN;
charts = inline SVG built in Python), cron-writes-web-reads (never a DB write in
a GET route), honest copy (missing data degrades to text, never invented), AI is
Gemini/Groq (digest.py), never Claude. Reuse existing design tokens and
components. main is branch-protected — always go through a PR. Only one window
works this repo at a time — check for concurrent sessions before big work.

After all four are shipped, give me a one-line status per spec (PR # + prod
commit), and call out anything that still needs a manual owner step on the VM.
