#!/usr/bin/env python3
"""
update.py — One-command daily P&L logger for The Desk.

Usage:
    python3 update.py [OPTIONS]

Examples:
    # Full day (interactive prompts if flags missing)
    python3 update.py

    # Full day in one shot:
    python3 update.py --date 2026-04-21 --ts204 150.43 --ts676 150.43 --ts235 150.43 --ax100k 145 --ax50k 140

    # Skip an account that didn't trade:
    python3 update.py --date 2026-04-21 --ts204 150 --ts676 150 --ts235 150 --ax100k 145 --ax50k skip

    # Push to GitHub after update (default: yes):
    python3 update.py --no-push

What it does:
  1. Validates inputs and checks for breach risk
  2. Updates portfolio_data.json (balances, log entry, momentum)
  3. Runs sync.py (derive fields + rebuild dashboard)
  4. Git commits + pushes
  5. Prints a summary brief
"""

import argparse, json, os, sys, subprocess, re
from datetime import datetime, timedelta
from desk_io import ROOT, JSON_PATH, load_json, save_json, validate_data_shape

SYNC_PATH  = os.path.join(ROOT, "sync.py")

# Map CLI flag names to JSON account keys
ACCT_FLAGS = {
    "ts204":  "topstep_1_204",
    "ts676":  "topstep_2_676",
    "ts235":  "topstep_3_235",
    "ax100k": "apex_100k_811",
    "ax50k":  "apex_50k_812",
}
ACCT_LABELS = {
    "topstep_1_204":  "TS-204",
    "topstep_2_676":  "TS-676",
    "topstep_3_235":  "TS-235",
    "apex_100k_811":  "AX-100K",
    "apex_50k_812":   "AX-50K",
}

RED    = "\033[91m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"
DIM    = "\033[2m"

def color(s, c): return c + s + RESET
def red(s):    return color(s, RED)
def green(s):  return color(s, GREEN)
def yellow(s): return color(s, YELLOW)
def cyan(s):   return color(s, CYAN)
def bold(s):   return color(s, BOLD)
def dim(s):    return color(s, DIM)

def fp(n):
    """Format P&L with sign and ANSI color (terminal printing)."""
    if n is None: return "—"
    return (green(f"+${abs(n):,.2f}") if n >= 0 else red(f"-${abs(n):,.2f}"))

def fp_plain(n):
    """Format P&L with sign, no color codes (safe for JSON / files)."""
    if n is None: return "—"
    return f"+${abs(n):,.2f}" if n >= 0 else f"-${abs(n):,.2f}"

def fm(n):
    """Format dollar amount."""
    if n is None: return "—"
    return f"${abs(n):,.2f}"


def backup_json():
    """Copy portfolio_data.json to a timestamped .bak in the same dir.
    Cheap insurance — restoring takes one mv."""
    import shutil
    from datetime import datetime as _dt
    bak = JSON_PATH + "." + _dt.now().strftime("%Y%m%d-%H%M%S") + ".bak"
    shutil.copy2(JSON_PATH, bak)
    return bak


def validate_pnl_magnitudes(pnl_map, threshold=1000):
    """Return list of (label, pnl) for any |P&L| > threshold. Likely typos."""
    suspicious = []
    for json_key, pnl in pnl_map.items():
        if pnl == "SKIP" or pnl is None:
            continue
        if abs(pnl) > threshold:
            suspicious.append((ACCT_LABELS.get(json_key, json_key), pnl))
    return suspicious


def check_date_sanity(date_str, holidays=None):
    """Warn if the date is a weekend or full-close holiday.
    Returns (warning_string or None)."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    if dt.weekday() == 5:
        return "Saturday — markets closed."
    if dt.weekday() == 6:
        return "Sunday — markets closed."
    if holidays:
        for h in holidays:
            if h.get("date") == date_str and h.get("type") == "close":
                return f"{h.get('name', 'Holiday')} — full market closure."
    return None


def get_last_trading_date(data):
    log = data.get("daily_log", [])
    if len(log) < 2:
        return None
    return log[-1]["date"]


def next_trading_date(from_date_str):
    """Guess next trading date (skip weekends, rough)."""
    dt = datetime.strptime(from_date_str, "%Y-%m-%d")
    dt += timedelta(days=1)
    while dt.weekday() >= 5:  # 5=Sat, 6=Sun
        dt += timedelta(days=1)
    return dt.strftime("%Y-%m-%d")


def parse_pnl_arg(val):
    """Parse a P&L value: 'skip' → None, '+150.43' or '150.43' or '-286.41' → float."""
    if val is None: return None
    v = str(val).strip().lower()
    if v in ("skip", "s", "none", "n", "-"):
        return "SKIP"
    try:
        return float(v)
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid P&L value: '{val}'. Use a number like 150.43, -286.41, or 'skip'.")


def prompt_pnl(label, current_bal, current_buffer):
    """Interactive prompt for one account's daily P&L."""
    buf_str = (red(f"  ⚠ buffer {fm(current_buffer)}") if current_buffer < 200
               else yellow(f"  buffer {fm(current_buffer)}") if current_buffer < 700
               else dim(f"  buffer {fm(current_buffer)}"))
    prompt = f"  {bold(label)} (bal {fp(current_bal)}{buf_str}): "
    while True:
        val = input(prompt).strip()
        if val == "" or val.lower() in ("skip", "s"):
            return "SKIP"
        try:
            return float(val)
        except ValueError:
            print(f"  {red('Invalid')} — enter a number (e.g. 150.43, -286.41) or 'skip'")


def compute_fleet_total(pnl_map):
    vals = [v for v in pnl_map.values() if v != "SKIP" and v is not None]
    return sum(vals) if vals else 0


def get_dow_str(date_str):
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return dt.strftime("%A")  # "Monday", "Tuesday", etc.


def is_friday(date_str):
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return dt.weekday() == 4


def compute_momentum(data, new_fleet_pnl):
    """Recompute momentum from last 5 log entries (including today)."""
    log = data.get("daily_log", [])
    # Collect last 5 daily_pnl values (include today's new entry)
    recent = []
    for entry in log[1:]:  # skip pre-day
        p = entry["totals"].get("daily_pnl")
        if p is not None:
            recent.append(p)
    recent.append(new_fleet_pnl)
    last5 = recent[-5:]

    green_days = sum(1 for p in last5 if p > 0)
    total = len(last5)
    wr = round(green_days / total, 2) if total else 0

    # Streak
    streak = 0
    streak_dir = "red"
    all_pnl = recent  # includes new day
    for p in reversed(all_pnl):
        if streak == 0:
            streak_dir = "green" if p > 0 else "red"
            streak = 1
        elif (p > 0) == (streak_dir == "green"):
            streak += 1
        else:
            break

    cl = ("STRONG" if green_days >= 4 else
          "STEADY" if green_days == 3 else
          "FADING" if green_days == 2 else "COLD")

    pm = (1.0 if cl in ("STRONG", "STEADY") else
          0.75 if cl == "FADING" else 0.5)

    note = {
        "STRONG": f"STRONG. {green_days}/5 green ({int(wr*100)}%). {streak}-day {streak_dir} streak. Press the edge.",
        "STEADY": f"STEADY. {green_days}/5 green ({int(wr*100)}%). {streak}-day {streak_dir} streak. 60% model on track.",
        "FADING": f"FADING. {green_days}/5 green ({int(wr*100)}%). {streak}-day {streak_dir} streak. Reduce size. Capital preservation mode.",
        "COLD":   f"COLD. {green_days}/5 green ({int(wr*100)}%). {streak}-day {streak_dir} streak. MES only. Survival mode.",
    }[cl]

    return {
        "last_5_days": [round(p) for p in last5],
        "green_days_last_5": green_days,
        "total_days_last_5": total,
        "win_rate_last_5": wr,
        "classification": cl,
        "streak": streak,
        "streak_direction": streak_dir,
        "projection_multiplier": pm,
        "note": note,
    }


def apply_pnl(data, date_str, pnl_map):
    """
    Apply daily P&L values to portfolio_data.json.
    pnl_map: { "topstep_1_204": 150.43, "apex_100k_811": "SKIP", ... }
    Returns (new_balances, fleet_total, breach_warnings)
    """
    accounts = data["accounts"]
    new_balances = {}
    breach_warnings = []
    fleet_total = 0

    for json_key, pnl in pnl_map.items():
        if pnl == "SKIP" or pnl is None:
            continue
        acct = accounts[json_key]
        if acct.get("status") in ("shelved", "breached"):
            continue

        old_bal = acct.get("balance", 0) or 0
        new_bal = round(old_bal + pnl, 2)
        acct["balance"] = new_bal
        new_balances[json_key] = new_bal
        fleet_total = round(fleet_total + pnl, 2)

        # Update winning days for Topstep (>= $150 threshold)
        ty = (acct.get("type") or "").lower()
        if "topstep" in ty and pnl >= 150:
            acct["winning_days"] = (acct.get("winning_days") or 0) + 1

        # Update Apex equity_high and best_day
        if "apex" in ty and "eval" not in ty:
            eh = acct.get("equity_high", new_bal)
            if new_bal > eh:
                acct["equity_high"] = round(new_bal, 2)
            bd = acct.get("consistency_best_day", 0) or 0
            if pnl > bd:
                acct["consistency_best_day"] = round(pnl, 2)

        # Breach check
        floor = acct.get("floor") or acct.get("mll_current")
        if floor is not None:
            new_buf = round(new_bal - floor, 2)
            if new_buf <= 0:
                breach_warnings.append({
                    "key": json_key,
                    "label": ACCT_LABELS.get(json_key, json_key),
                    "buf": new_buf,
                    "breached": True,
                })
            elif new_buf < 200:
                breach_warnings.append({
                    "key": json_key,
                    "label": ACCT_LABELS.get(json_key, json_key),
                    "buf": new_buf,
                    "breached": False,
                })

    # Update last_updated
    data["last_updated"] = date_str

    # Build daily log entry
    acct_log = {}
    for json_key, pnl in pnl_map.items():
        if pnl == "SKIP": continue
        acct_log[json_key] = {
            "balance": accounts[json_key]["balance"],
            "daily_pnl": pnl,
        }

    log_entry = {
        "date": date_str,
        "totals": {"portfolio_balance": None, "daily_pnl": round(fleet_total, 2)},
        "accounts": acct_log,
    }

    # Add day note (use plain formatter — JSON shouldn't contain ANSI)
    dow = get_dow_str(date_str)
    log_entry["notes"] = f"Fleet {fp_plain(fleet_total)}. {dow}."

    data["daily_log"].append(log_entry)

    # Update momentum
    data["momentum"] = compute_momentum(data, fleet_total)

    return new_balances, fleet_total, breach_warnings


def run_sync():
    """Run sync.py and capture output."""
    result = subprocess.run(
        [sys.executable, SYNC_PATH],
        capture_output=True, text=True, cwd=ROOT
    )
    return result.returncode, result.stdout, result.stderr


def git_commit_push(date_str, fleet_total, pnl_map):
    """Commit and push to GitHub."""
    acct_parts = []
    for flag, json_key in ACCT_FLAGS.items():
        pnl = pnl_map.get(json_key)
        if pnl != "SKIP" and pnl is not None:
            sign = "+" if pnl >= 0 else ""
            acct_parts.append(f"{ACCT_LABELS[json_key]} {sign}{pnl:.2f}")
    acct_summary = " | ".join(acct_parts)

    sign = "+" if fleet_total >= 0 else ""
    msg = (
        f"{date_str} P&L — Fleet {sign}${fleet_total:,.2f}\n\n"
        f"{acct_summary}\n\n"
        f"Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
    )

    files = ["portfolio_data.json", "dashboard-v4.html", "index.html"]
    subprocess.run(["git", "add"] + files, cwd=ROOT, capture_output=True)
    result = subprocess.run(
        ["git", "commit", "-m", msg],
        cwd=ROOT, capture_output=True, text=True
    )
    if result.returncode != 0:
        return False, result.stderr

    push = subprocess.run(
        ["git", "push"],
        cwd=ROOT, capture_output=True, text=True
    )
    return push.returncode == 0, push.stdout + push.stderr


def print_brief(data, date_str, pnl_map, fleet_total, new_balances, breach_warnings):
    """Print end-of-day summary brief."""
    accounts = data["accounts"]
    mo = data["momentum"]
    dow = get_dow_str(date_str)

    print()
    print(bold("═" * 58))
    print(bold(f"  PAYOUT COMMAND BRIEF — {date_str} ({dow})"))
    print(bold("═" * 58))

    # Fleet pulse
    fleet_str = fp(fleet_total)
    print(f"\n  Fleet today:  {fleet_str}")

    # All-time
    all_pnl = [e["totals"]["daily_pnl"] for e in data["daily_log"][1:]
               if e["totals"].get("daily_pnl") is not None]
    all_green = sum(1 for p in all_pnl if p > 0)
    print(f"  All-time:     {all_green}/{len(all_pnl)} green ({int(all_green/max(len(all_pnl),1)*100)}%)")

    # Momentum
    cl = mo["classification"]
    cl_color = (GREEN if cl == "STRONG" else
                CYAN  if cl == "STEADY" else
                YELLOW if cl == "FADING" else RED)
    streak_info = f"  {mo['streak']}-day {mo['streak_direction']} streak"
    print(f"  Momentum:     {color(cl, cl_color)}{streak_info}  ({mo['green_days_last_5']}/5 green)")

    # Account table
    print(f"\n  {'Account':<10} {'Today':>10} {'Balance':>12} {'Buffer':>10} {'Status'}")
    print(f"  {'─'*10} {'─'*10} {'─'*12} {'─'*10} {'─'*12}")
    for flag, json_key in ACCT_FLAGS.items():
        acct = accounts.get(json_key)
        if not acct or acct.get("status") in ("shelved", "breached"):
            continue
        pnl = pnl_map.get(json_key)
        label = ACCT_LABELS[json_key]
        bal = acct["balance"]
        rk = acct.get("risk_level", "safe")
        rk_color = (RED if rk in ("critical","high") else
                    YELLOW if rk == "medium" else GREEN)

        floor = acct.get("floor") or acct.get("mll_current")
        if floor is not None:
            buf = bal - floor
            buf_str = (red(f"${buf:,.0f}") if buf < 200 else
                       yellow(f"${buf:,.0f}") if buf < 700 else
                       dim(f"${buf:,.0f}"))
        else:
            buf_str = dim("—")

        pnl_str = fp(pnl) if pnl != "SKIP" and pnl is not None else dim("skipped")
        bal_str = (green(f"${bal:,.2f}") if bal >= 0 else red(f"-${abs(bal):,.2f}"))
        rk_str = color(f"[{rk.upper()}]", rk_color)
        print(f"  {label:<10} {pnl_str:>10} {bal_str:>12} {buf_str:>10}  {rk_str}")

    # Breach warnings
    if breach_warnings:
        print()
        for w in breach_warnings:
            if w["breached"]:
                print(red(f"  🚨 {w['label']} BREACHED — mark as breached in JSON!"))
            else:
                print(red(f"  ⚠  {w['label']} BREACH IMMINENT — buffer ${w['buf']:.0f}. STOP TRADING."))

    # FOMC warning
    today = datetime.strptime(date_str, "%Y-%m-%d")
    for dd in data.get("danger_dates", []):
        if dd.get("type") == "FOMC":
            for iso in dd.get("dates", []):
                fdate = datetime.strptime(iso, "%Y-%m-%d")
                diff = (fdate - today).days
                if 0 < diff <= 3:
                    print(red(f"\n  🚨 FOMC in {diff} day{'s' if diff!=1 else ''} ({iso}) — DO NOT TRADE"))
                elif 3 < diff <= 7:
                    print(yellow(f"\n  ⚠  FOMC on {iso} ({diff} days out) — plan ahead"))

    # Friday skip
    if is_friday(date_str):
        print(yellow(f"\n  ⛔ FRIDAY — historically 0% win rate (avg -$1,777). Rest tomorrow."))

    # Momentum advice
    print()
    if cl == "COLD":
        print(red("  ⚡ COLD momentum. MES only on all accounts. Capital preservation. "))
        print(red("     Extend all projections 1.5x. Let the edge come back to you."))
    elif cl == "FADING":
        print(yellow("  ⚡ FADING momentum. Reduce size. Small green days to rebuild streak."))
    elif cl == "STRONG":
        print(green("  ⚡ STRONG momentum. Press the edge. 2 MES on Apex if setup is A+."))
    else:
        print(cyan("  ⚡ STEADY. 60% model. Normal size, A+ setups, respect the stops."))

    print()
    print(bold("═" * 58))
    print(f"  Dashboard: {cyan('https://progenco.github.io/the-desk')}")
    print(bold("═" * 58))
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Log daily P&L to The Desk portfolio tracker.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 update.py
  python3 update.py --date 2026-04-21 --ts204 150.43 --ts676 150.43 --ts235 150.43 --ax100k 145 --ax50k 140
  python3 update.py --date 2026-04-21 --ts204 -286 --ts676 -286 --ts235 -286 --ax100k -162 --ax50k -161
  python3 update.py --date 2026-04-21 --ts204 skip --ts676 150 --ts235 150 --ax100k 145 --ax50k 140 --no-push
        """
    )
    parser.add_argument("--date",   help="Trading date (YYYY-MM-DD). Default: next trading day after last log entry.")
    parser.add_argument("--ts204",  type=parse_pnl_arg, help="TS-204 daily P&L (or 'skip')")
    parser.add_argument("--ts676",  type=parse_pnl_arg, help="TS-676 daily P&L (or 'skip')")
    parser.add_argument("--ts235",  type=parse_pnl_arg, help="TS-235 daily P&L (or 'skip')")
    parser.add_argument("--ax100k", type=parse_pnl_arg, help="AX-100K daily P&L (or 'skip')")
    parser.add_argument("--ax50k",  type=parse_pnl_arg, help="AX-50K daily P&L (or 'skip')")
    parser.add_argument("--no-push", action="store_true", help="Don't push to GitHub")
    parser.add_argument("--dry-run", action="store_true", help="Validate only, don't write anything")
    args = parser.parse_args()

    print(bold(f"\n{'─'*58}"))
    print(bold(f"  update.py — The Desk Daily Logger"))
    print(bold(f"{'─'*58}\n"))

    # ── Load data ──────────────────────────────────────────
    data = load_json()
    try:
        validate_data_shape(data)
    except ValueError as e:
        print(red(f"  ✗ Data file invalid: {e}"))
        sys.exit(1)
    last_date = get_last_trading_date(data)
    print(f"  Last log entry: {dim(last_date)}")

    # ── Resolve date ───────────────────────────────────────
    if args.date:
        date_str = args.date
    else:
        suggested = next_trading_date(last_date) if last_date else datetime.today().strftime("%Y-%m-%d")
        val = input(f"  Trading date [{suggested}]: ").strip()
        date_str = val if val else suggested
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        print(red(f"  Invalid date format: '{date_str}'. Use YYYY-MM-DD."))
        sys.exit(1)

    dow = get_dow_str(date_str)
    print(f"  Logging for:    {bold(date_str)} ({dow})")
    if is_friday(date_str):
        print(yellow(f"  ⛔ WARNING: Friday — historically 0% win rate on your data."))

    # Weekend / closed-holiday sanity check
    sanity_warn = check_date_sanity(date_str, data.get("cme_holidays", []))
    if sanity_warn:
        print(red(f"  ⚠ {sanity_warn}"))
        confirm = input(red("  Log P&L anyway? (y/N): ")).strip().lower()
        if confirm != "y":
            print("  Aborted.")
            sys.exit(0)

    # Check duplicate
    existing_dates = [e["date"] for e in data["daily_log"][1:]]
    if date_str in existing_dates:
        print(red(f"\n  ⚠ Date {date_str} already exists in the log!"))
        confirm = input("  Overwrite? (y/N): ").strip().lower()
        if confirm != "y":
            print("  Aborted.")
            sys.exit(0)
        # Remove old entry
        data["daily_log"] = [e for e in data["daily_log"] if e.get("date") != date_str]

    # ── Collect P&L values ─────────────────────────────────
    print()
    pnl_map = {}  # json_key -> float or "SKIP"

    flag_values = {
        "ts204": args.ts204, "ts676": args.ts676, "ts235": args.ts235,
        "ax100k": args.ax100k, "ax50k": args.ax50k,
    }
    any_provided = any(v is not None for v in flag_values.values())

    if any_provided:
        # CLI mode — use provided values, prompt for missing
        for flag, json_key in ACCT_FLAGS.items():
            val = flag_values[flag]
            acct = data["accounts"].get(json_key, {})
            if acct.get("status") in ("shelved", "breached"):
                pnl_map[json_key] = "SKIP"
                continue
            if val is None:
                # Prompt for missing
                bal = acct.get("balance", 0)
                floor = acct.get("floor") or acct.get("mll_current")
                buf = bal - floor if floor is not None else None
                val = prompt_pnl(ACCT_LABELS[json_key], bal, buf)
            pnl_map[json_key] = val
    else:
        # Interactive mode — prompt for all accounts
        print(f"  Enter daily P&L for each account (or 'skip'):\n")
        for flag, json_key in ACCT_FLAGS.items():
            acct = data["accounts"].get(json_key, {})
            if acct.get("status") in ("shelved", "breached"):
                pnl_map[json_key] = "SKIP"
                continue
            bal = acct.get("balance", 0)
            floor = acct.get("floor") or acct.get("mll_current")
            buf = bal - floor if floor is not None else None
            pnl_map[json_key] = prompt_pnl(ACCT_LABELS[json_key], bal, buf)

    # ── Magnitude sanity ───────────────────────────────────
    suspicious = validate_pnl_magnitudes(pnl_map, threshold=1000)
    if suspicious:
        print(yellow(f"\n  ⚠ Unusual magnitude(s) (likely typo?):"))
        for label, pnl in suspicious:
            print(yellow(f"     {label}: {fp(pnl)}"))
        confirm = input(yellow("  These look right? (y/N): ")).strip().lower()
        if confirm != "y":
            print("  Aborted. Re-run with corrected values.")
            sys.exit(0)

    # ── Fleet total preview ────────────────────────────────
    fleet_total = compute_fleet_total(pnl_map)
    print(f"\n  Fleet total: {fp(fleet_total)}")

    # ── Breach pre-check ───────────────────────────────────
    for json_key, pnl in pnl_map.items():
        if pnl == "SKIP" or pnl is None: continue
        acct = data["accounts"].get(json_key, {})
        bal = acct.get("balance", 0) or 0
        floor = acct.get("floor") or acct.get("mll_current")
        if floor is not None:
            new_buf = bal + pnl - floor
            if new_buf <= 0:
                print(red(f"\n  🚨 {ACCT_LABELS[json_key]} would BREACH with this P&L (buffer → ${new_buf:.0f})"))

    # ── Dry run exit ───────────────────────────────────────
    if args.dry_run:
        print(dim("\n  [dry-run] No changes written."))
        sys.exit(0)

    confirm = input(f"\n  Confirm and update? (Y/n): ").strip().lower()
    if confirm == "n":
        print("  Aborted.")
        sys.exit(0)

    # ── Backup before write ────────────────────────────────
    bak_path = backup_json()
    print(f"  {dim('Backup → ' + os.path.basename(bak_path))}")

    # ── Apply changes ──────────────────────────────────────
    print(f"  Updating portfolio_data.json...")
    new_balances, fleet_total, breach_warnings = apply_pnl(data, date_str, pnl_map)
    save_json(data)
    print(f"  {green('✓')} JSON updated")

    # ── Run sync.py ────────────────────────────────────────
    print(f"  Running sync.py...")
    rc, stdout, stderr = run_sync()
    if rc != 0:
        print(red(f"  ✗ sync.py failed:\n{stderr}"))
        sys.exit(1)
    # Count changed fields from sync output
    changed_match = re.search(r"(\d+) field\(s\) changed", stdout)
    changed_n = changed_match.group(1) if changed_match else "?"
    print(f"  {green('✓')} Dashboard rebuilt ({changed_n} fields changed)")

    # ── Git commit + push ──────────────────────────────────
    if not args.no_push:
        print(f"  Committing and pushing to GitHub...")
        ok, msg = git_commit_push(date_str, fleet_total, pnl_map)
        if ok:
            print(f"  {green('✓')} Pushed to github.com/ProGenCo/the-desk")
        else:
            print(yellow(f"  ⚠ Push failed (check git): {msg}"))
    else:
        print(dim("  [--no-push] Skipped GitHub push"))

    # ── Load fresh data for brief ──────────────────────────
    data = load_json()

    # ── Print brief ────────────────────────────────────────
    print_brief(data, date_str, pnl_map, fleet_total, new_balances, breach_warnings)


if __name__ == "__main__":
    main()
