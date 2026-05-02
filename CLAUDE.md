# The Desk — Codebase Conventions

A single-page prop firm portfolio dashboard for Josue (full-time futures trader, 5 active accounts, MES only).

Live: https://progenco.github.io/the-desk

## TL;DR for Claude

You almost never need to touch the dashboard HTML/JS by hand. The flow is:

1. User pastes daily P&L → run `python3 update.py` (or pass `--ts204 ... --ax50k ...` flags)
2. `update.py` writes `portfolio_data.json`, calls `sync.py`, commits, pushes
3. `sync.py` derives computed fields, regenerates the JS data block in `dashboard-v4.html`, copies to `index.html`
4. GitHub Pages serves `index.html`

Don't edit `dashboard-v4.html` data values by hand. Don't edit `index.html` ever — it's regenerated.

## Files

| File | Role | Edit by hand? |
|---|---|---|
| `portfolio_data.json` | Source of truth — accounts, daily log, momentum, FOMC dates, holidays | YES (sparingly) |
| `update.py` | Daily logger CLI — adds today's P&L, derives, syncs, commits, pushes | YES |
| `sync.py` | Reads JSON, derives computed fields, rebuilds JS data block in `dashboard-v4.html`, copies to `index.html` | YES |
| `dashboard-v4.html` | Master dashboard. Has data block markers `/* ══ DATA ══ */ ... /* ══ FORMATTERS ══ */` that sync.py rewrites | YES (CSS/JS/HTML) |
| `index.html` | Served file. **Auto-generated copy of dashboard-v4.html. Never edit directly.** | NO |
| `.claude/memory/` | Per-session memory (project_portfolio.md, account_structure.md, etc.) | YES |
| `CLAUDE.md` | This file | YES |

## Data flow contract

```
portfolio_data.json  →  derive_computed_fields()  →  build_data_block()  →  dashboard-v4.html  →  index.html
       (raw)              (sync.py, in-place)         (sync.py)               (regenerated)        (copy)
```

`derive_computed_fields()` writes back to JSON. So after `sync.py` runs, the JSON has fresh `profit`, `dist_to_breach`, `dist_dd`, `risk_level`, `consistency_best_day_pct`, `consistency_status`, `consistency_note`, etc. **The user only needs to update `balance` per account** — sync.py computes everything else.

## Account types & risk tiers

| Type prefix | Examples | Floor field | DD field | Win-day threshold |
|---|---|---|---|---|
| `topstep_*` | `topstep_1_204`, `topstep_2_676`, `topstep_3_235` | `mll_current` (trailing) | n/a | $150 |
| `tradeify_*` | `tradeify_286` (BREACHED) | `floor` (static after first payout) | n/a | $150 |
| `apex_legacy_*` | `apex_100k_811`, `apex_50k_812` | `floor` (trailing DD) | `dist_dd` | $50 |
| `apex_eval` | `apex_eval_101` (SHELVED) | n/a | n/a | n/a |

### Risk-level tiers (computed in `derive_computed_fields`)

**Topstep / Tradeify** (`dist_to_breach` based):
- `< 200` → `critical` — DO NOT TRADE
- `< 700` → `high` — 1c MES, A++ only
- `< 1500` → `medium` — Cautious, 1-2c
- otherwise → `safe`

**Apex** (`dist_dd` based):
- `< 400` → `critical`
- `< 1200` → `high`
- `< 2000` → `medium`
- otherwise → `safe`

## Hard rules (never violate)

1. **No FOMC trading.** From `D.fomc[]` array. March 18, 2026 cost $5,640.
2. **No contract rollover trading.** Quarterly Jun/Sep/Dec.
3. **Daily loss cap $400.** Stop for the day.
4. **30% rule for Apex.** No single day's P&L > 30% of total profit.
5. **Friday is a SKIP day.** Historic 0% win rate, avg -$1,777.

## Commit conventions

- Daily P&L commits use auto-generated messages from `git_commit_push()` in update.py
- Manual commits: short summary line, blank line, bullet body
- Always end with `Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>`
- Never `git push --force` to main without explicit user approval

## Sync.py contract

`sync.py` is idempotent. Running it twice in a row produces identical output. It:
1. Validates data shape (aborts if `accounts`/`daily_log`/`momentum` missing)
2. Calls `derive_computed_fields()` — writes back to JSON if anything changed
3. Runs sanity checks (warns about `balance < floor`, `equity_high < balance`, 30%-rule-blocked)
4. Builds new JS data block from accounts/log/momentum/fomc/holidays
5. Replaces the block between markers in `dashboard-v4.html`
6. Replaces the per-account `dt=[...]` date arrays
7. Copies `dashboard-v4.html` → `index.html`

If you add a new account, the only thing you have to do manually is add it to `portfolio_data.json` with the right `type` field. Sync.py will figure out the rest from `derive_computed_fields()`.

## update.py contract

`update.py` is the one-command daily flow. It:
1. Loads JSON, validates shape
2. Resolves date (CLI arg or interactive prompt, defaults to next trading day after last log)
3. Sanity-checks date (warns on weekend/holiday)
4. Collects per-account P&L (CLI flags or prompt)
5. Validates magnitudes (warns if any \|P&L\| > $1000)
6. Pre-checks for breach (warns if any account would breach)
7. Backs up JSON (`portfolio_data.json.YYYYMMDD-HHMMSS.bak`)
8. Applies P&L → updates balance, win days, equity high, best day
9. Adds daily log entry
10. Recomputes momentum
11. Saves JSON
12. Runs sync.py
13. Git commits + pushes (unless `--no-push`)
14. Prints end-of-day brief

Flags: `--date`, `--ts204`, `--ts676`, `--ts235`, `--ax100k`, `--ax50k`, `--no-push`, `--dry-run`. Each P&L flag accepts a number or `skip`.

## Adding a new dashboard section

1. Add a `function buildXxx()` near the other `build*` functions (alphabetical)
2. In `showFleetView()`, add `+buildXxx()` in the right place in the content string
3. Section pattern: `<div style="padding:0 0 40px"><div class="container"><div class="fade-up" style="margin-bottom:16px"><span class="eyebrow">Eyebrow</span><h2 class="h2">Title</h2></div>... cards/grid ...</div></div>`
4. Run `python3 sync.py` to regenerate index.html
5. Test with `python3 -c "import json; json.load(open('portfolio_data.json'))"` and node syntax check

## Adding a new full page

1. Add a `<div class="full-page" id="page-NAME">` block in HTML
2. Add a `function buildNAME()` that populates it
3. Wire it in `showPage()`: `if(page==='NAME')buildNAME();`
4. Add a nav link: `<button class="nav-link" id="nl-NAME" onclick="showPage('NAME')">Label</button>`

## Memory files (.claude/memory/)

- `project_portfolio.md` — Current portfolio snapshot
- `account_structure.md` — Per-account IDs, payout rules, thresholds
- `trading_rules.md` — Hard rules (FOMC, rollover, daily cap)
- `payout_optimization.md` — Strategy notes
- `dashboard_preferences.md` — Visual style preferences (Bloomberg terminal feel)
- `user_profile.md` — Background on Josue
- `prop_firm_research.md` — Industry research

Keep these updated when meaningful state changes (new account, breached account, big strategy shift).

## Things to avoid

- Don't add features Josue didn't ask for "in case useful"
- Don't celebrate work in commit messages or summaries ("dramatic", "stunning", "beautifully")
- Don't reach for visual polish before verifying what was built actually works
- Don't make the dashboard busier — data density is good, but visual hierarchy must show what matters most (breach > buffer warnings > 30% rule > FOMC > etc.)
- Don't use `git push --force` to main
- Don't edit `index.html` (regenerated)
- Don't trust auto-derived state without `python3 sync.py` first

## Quick test commands

```bash
# Check JSON validity
python3 -c "import json; d=json.load(open('portfolio_data.json')); print(len(d['accounts']))"

# Verify dashboard JS parses
node --input-type=module -e "import {readFileSync} from 'fs'; const h=readFileSync('index.html','utf8'); const s=h.indexOf('/* ══ DATA ══ */'); const e=h.indexOf('/* ══ SCROLL OBSERVER ══ */'); new Function(h.slice(s,e)); console.log('ok');"

# Dry-run an update
python3 update.py --date 2026-05-04 --ts204 -300 --ts676 -300 --ts235 -300 --ax100k -200 --ax50k -200 --dry-run
```
