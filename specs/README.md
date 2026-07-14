# specs/ — implementation playbooks

Each file is a **self-contained prompt** for building one improvement in a
fresh Claude window, written so a smaller/cheaper model can implement, test,
and ship it without exploring the codebase (that's what burns tokens).

## How to use (Arka)
Open a new Claude Code window in `~/Desktop/InvestRight` and paste exactly:

> Implement `specs/NN-<name>.md`. Read `specs/_CONTEXT.md` first, then the
> spec, then ONLY the files the spec lists. Follow it exactly — branch, build,
> run pytest, verify as written, open a PR, squash-merge. Do not deploy. Do
> not read DESIGN.md or other docs. If the code doesn't match a spec anchor,
> stop and say so instead of improvising.

One spec per window. Merge order within a tier doesn't matter, except:
- 11 (portfolio) is two sequential PRs (A then B).
- 12 (weekly email) sends nothing until SMTP is configured — safe any time.

After a few merges, deploy once: ask Claude to deploy, or on the VM
`cd /opt/investright && ./deploy.sh`.

## The specs

| # | file | what | size |
|---|------|------|------|
| 01 | 01-jump-nav.md | sticky section chips on the deep-dive | ~2h |
| 02 | 02-popular-chips.md | home chips: company name + day change | ~1h |
| 03 | 03-mobile-topbar.md | topbar 3 rows → 2 on mobile | ~1-2h |
| 04 | 04-digest-pager.md | /today: browse past nightly notes | ~1-2h |
| 05 | 05-pwa-manifest.md | PWA manifest + Otto home-screen icons | ~1-2h |
| 06 | 06-guest-watchlist.md | guest watchlist sells the feature | ~2h |
| 07 | 07-benchmark-overlay.md | S&P/NIFTY overlay on the trend chart | ~½ day |
| 08 | 08-price-alerts.md | one-shot above/below email alerts | ~½ day |
| 09 | 09-compare-view.md | /compare side-by-side (2-4 tickers) | ~½ day |
| 10 | 10-notes-hub.md | all-notes list on /account + CSV | ~2-3h |
| 11 | 11-portfolio.md | holdings, P&L, totals, donut (2 PRs) | weekend |
| 12 | 12-weekly-email.md | opt-in Sunday watchlist email | ~½ day |
| 13 | 13-earnings-calendar.md | next-earnings chip + /today list | ~½ day |

Recommended order: 01 → 02 → 03 → 05 → 04 → 06 (quick wins), then 07, then
pick from the rest.

## Keeping specs honest
Specs pin anchors (function names, line-ish locations, schemas) as of
2026-07-13. If the repo has drifted, the implementing model is instructed to
stop rather than improvise — update the spec here, then re-run.
