#!/usr/bin/env python3
"""
sync.py — Build dashboard-v4.html data block from portfolio_data.json
and copy to index.html. No external dependencies.
"""

import json, re, shutil, os, sys
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
JSON_PATH  = os.path.join(ROOT, "portfolio_data.json")
DASH_PATH  = os.path.join(ROOT, "dashboard-v4.html")
INDEX_PATH = os.path.join(ROOT, "index.html")

# ── Account key / short-name / firm mapping ──────────────────────────
ACCOUNT_MAP = {
    "topstep_1_204":  dict(k="ts_204",  s="TS-204",  f="Topstep Express", ty="ts"),
    "topstep_2_676":  dict(k="ts_676",  s="TS-676",  f="Topstep Express", ty="ts"),
    "topstep_3_235":  dict(k="ts_235",  s="TS-235",  f="Topstep Express", ty="ts"),
    "tradeify_286":   dict(k="tf_286",  s="TF-286",  f="Tradeify",        ty="tf"),
    "apex_100k_811":  dict(k="ax_100k", s="AX-100K", f="Apex Trader",     ty="ax"),
    "apex_50k_812":   dict(k="ax_50k",  s="AX-50K",  f="Apex Trader",     ty="ax"),
}

def js_num(v):
    """Format a number for JS: null for None, int if whole, else float."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v == int(v) and abs(v) < 1e12:
        return str(int(v))
    return str(v)

def js_str(v):
    # Escape single quotes
    return "'" + str(v).replace("\\", "\\\\").replace("'", "\\'") + "'"

def fmt_date_short(iso):
    """'2026-03-17' -> '3/17'"""
    d = datetime.strptime(iso, "%Y-%m-%d")
    return f"{d.month}/{d.day}"

def fmt_date_label(iso):
    """'2026-03-17' -> 'Mar 17'"""
    d = datetime.strptime(iso, "%Y-%m-%d")
    return d.strftime("%b %-d")

def fmt_fomc(iso):
    """'2026-04-29' -> 'Apr 29'"""
    d = datetime.strptime(iso, "%Y-%m-%d")
    return d.strftime("%b %-d")


def validate_data_shape(data):
    """Sanity-check the loaded JSON has the keys we depend on."""
    required = ["accounts", "daily_log", "momentum"]
    missing = [k for k in required if k not in data]
    if missing:
        raise ValueError(f"portfolio_data.json missing required keys: {missing}")
    if not isinstance(data["accounts"], dict) or not data["accounts"]:
        raise ValueError("portfolio_data.json: 'accounts' must be a non-empty dict")
    return True


def sanity_check_derived(data):
    """After derive_computed_fields, run sanity checks. Returns list of warnings."""
    warnings = []
    for key, acct in data["accounts"].items():
        if acct.get("status") in ("shelved", "breached"):
            continue
        bal = acct.get("balance")
        floor = acct.get("floor") or acct.get("mll_current")
        if bal is not None and floor is not None and bal < floor:
            warnings.append(f"  ⚠ {key}: balance {bal} < floor {floor} — already breached?")
        eh = acct.get("equity_high")
        if eh is not None and bal is not None and eh < bal:
            warnings.append(f"  ⚠ {key}: equity_high {eh} < balance {bal} — impossible (should auto-update)")
        # 30% rule informational
        if acct.get("consistency_pct") and acct.get("profit", 0) > 0:
            bd = acct.get("consistency_best_day", 0) or 0
            if bd > acct["profit"]:
                warnings.append(f"  ℹ {key}: best_day ${bd} > profit ${acct['profit']} → 30%-rule blocked until profit catches up")
    return warnings


def load_json():
    with open(JSON_PATH) as f:
        return json.load(f)


def derive_computed_fields(data):
    """Auto-compute derived fields from raw inputs.

    After a user updates 'balance' (and 'equity_high' if a new high was hit),
    this function recalculates:
      - profit = balance - starting_balance
      - Topstep: dist_to_breach = balance - floor
      - Apex:    dist_dd = balance - floor
                 dist_to_safety_net = safety_net - balance
                 consistency_best_day_pct = best_day / profit
                 consistency_status / consistency_note
      - risk_level (critical / high / medium / safe) based on buffer
    Mutates data["accounts"] in-place.
    Returns True if any field changed.
    """
    changed = False
    for key, acct in data["accounts"].items():
        status = acct.get("status", "funded")
        if status in ("shelved", "breached"):
            continue

        ty = (acct.get("type") or "").lower()
        bal = acct.get("balance", 0) or 0
        sb = acct.get("starting_balance", 0) or 0

        # ── profit ──────────────────────────────────────────────────
        new_profit = round(bal - sb, 2)
        if acct.get("profit") != new_profit:
            acct["profit"] = new_profit
            changed = True

        # ── Topstep / Tradeify (trailing MLL) ───────────────────────
        if "topstep" in ty or "tradeify" in ty:
            floor = acct.get("floor") or acct.get("mll_current")
            if floor is not None:
                new_db = round(bal - floor, 2)
                if acct.get("dist_to_breach") != new_db:
                    acct["dist_to_breach"] = new_db
                    changed = True
                # Topstep MLL trailing: update mll_current if balance hit new high
                mll_size = acct.get("mll", 2000)
                mll_floor = acct.get("mll_current", -mll_size)
                if bal > 0:
                    # trailing floor = max(old floor, balance - mll_size)
                    new_mll = round(max(mll_floor, bal - mll_size), 2)
                    if new_mll != mll_floor:
                        acct["mll_current"] = new_mll
                        acct["floor"] = new_mll
                        acct["dist_to_breach"] = round(bal - new_mll, 2)
                        changed = True
                # risk_level from buffer
                db = acct.get("dist_to_breach", 9999)
                new_rk = ("critical" if db < 200 else
                          "high"     if db < 700 else
                          "medium"   if db < 1500 else "safe")
                if acct.get("risk_level") != new_rk:
                    acct["risk_level"] = new_rk
                    changed = True

        # ── Apex ────────────────────────────────────────────────────
        elif "apex" in ty and "eval" not in ty:
            floor = acct.get("floor")
            if floor is not None:
                new_dd = round(bal - floor, 2)
                if acct.get("dist_dd") != new_dd:
                    acct["dist_dd"] = new_dd
                    changed = True
            sn = acct.get("safety_net")
            if sn is not None:
                new_ds = round(sn - bal, 2)
                if acct.get("dist_to_safety_net") != new_ds:
                    # dist_to_safety_net not a stored field normally — skip
                    pass
            # equity_high
            eh = acct.get("equity_high", bal)
            if bal > (eh or 0):
                acct["equity_high"] = round(bal, 2)
                changed = True
            # 30% consistency fields
            profit = acct.get("profit", 0) or 0
            best_day = acct.get("consistency_best_day", 0) or 0
            if profit > 0 and best_day > 0:
                new_pct = round(best_day / profit, 4)
                if acct.get("consistency_best_day_pct") != new_pct:
                    acct["consistency_best_day_pct"] = new_pct
                    changed = True
                # consistency_note
                gap = max(0, round(best_day / 0.30 - profit, 0))
                status_str = "CRITICAL" if best_day > profit * 0.30 else "CLEAR"
                note = (f"🔴 Best day ${best_day:.0f} = {new_pct*100:.1f}% of ${profit:.0f} profit. "
                        f"Need ${best_day/0.30:.0f} to clear. Gap: ${gap:.0f}."
                        if best_day > profit * 0.30
                        else f"✅ Best day ${best_day:.0f} = {new_pct*100:.1f}% of ${profit:.0f} profit. CLEAR.")
                if acct.get("consistency_note") != note:
                    acct["consistency_note"] = note
                    acct["consistency_status"] = status_str
                    changed = True
            # risk_level from dist_dd
            # Apex DD thresholds: tighter because Apex losses run larger per day
            dd = acct.get("dist_dd", 9999)
            new_rk = ("critical" if dd < 400 else
                      "high"     if dd < 1200 else
                      "medium"   if dd < 2000 else "safe")
            if acct.get("risk_level") != new_rk:
                acct["risk_level"] = new_rk
                changed = True

    return changed


def infer_starting_balance(acct):
    """Get starting_balance, inferring from account type/name if missing."""
    sb = acct.get("starting_balance")
    if sb is not None:
        return sb
    # Check type and name for size hints
    hint = (acct.get("type", "") + " " + acct.get("name", "")).lower()
    if "100k" in hint:
        return 100000
    elif "50k" in hint:
        return 50000
    return 0


def build_pnl_arrays(data):
    """Build per-account PNL arrays from daily_log.

    The first entry in the log is the 'pre' snapshot (date index 0).
    For each account, collect starting profit from entry[0], then daily_pnl
    from subsequent entries.
    """
    log = data["daily_log"]
    accounts = data["accounts"]
    result = {}  # json_key -> [numbers]

    # Pre-day is log[0]; trading days are log[1:]
    pre = log[0]
    for json_key, acct in accounts.items():
        if acct.get("status") in ("shelved", "breached"):
            # Build shelved PNL too
            pnl = []
            starting = infer_starting_balance(acct)
            if json_key in pre.get("accounts", {}):
                pre_bal = pre["accounts"][json_key]["balance"]
                pnl.append(round(pre_bal - starting) if pre_bal is not None else None)
            else:
                pnl.append(None)
            for entry in log[1:]:
                ea = entry.get("accounts", {}).get(json_key)
                if ea is None:
                    break  # account no longer tracked
                dpnl = ea.get("daily_pnl")
                pnl.append(round(dpnl) if dpnl is not None else None)
            result[json_key] = pnl
            continue

        pnl = []
        starting = infer_starting_balance(acct)
        # Pre value
        if json_key in pre.get("accounts", {}):
            pre_bal = pre["accounts"][json_key]["balance"]
            if pre_bal is not None:
                pnl.append(round(pre_bal - starting))
            else:
                pnl.append(None)
        else:
            pnl.append(None)
        # Daily PNL from each subsequent log entry
        for entry in log[1:]:
            ea = entry.get("accounts", {}).get(json_key)
            if ea is not None:
                dpnl = ea.get("daily_pnl")
                pnl.append(round(dpnl) if dpnl is not None else None)
            else:
                pnl.append(None)
        result[json_key] = pnl
    return result


def compute_avg_daily(pnl_arr):
    """Average of positive daily P&L values (for estimating days to target).
    Uses only green days to estimate pace. Falls back to 150."""
    vals = [v for v in pnl_arr[1:] if v is not None and v > 0]
    if not vals:
        return 150  # fallback
    return sum(vals) / len(vals)


def build_account_line(json_key, acct, pnl_arr):
    """Build one account JS object string."""
    m = ACCOUNT_MAP[json_key]
    ty = m["ty"]

    parts = []
    parts.append(f"k:{js_str(m['k'])}")
    parts.append(f"s:{js_str(m['s'])}")
    parts.append(f"f:{js_str(m['f'])}")
    parts.append(f"ty:{js_str(ty)}")
    parts.append(f"bal:{js_num(acct['balance'])}")
    parts.append(f"pft:{js_num(acct['profit'])}")

    if ty == "ts":
        parts.append(f"mll:{js_num(acct.get('mll_current'))}")
        parts.append(f"mm:{js_num(acct.get('mll', 2000))}")
        parts.append(f"db:{js_num(acct.get('dist_to_breach'))}")
        parts.append(f"wd:{js_num(acct.get('winning_days'))}")
        parts.append(f"wr:{js_num(acct.get('winning_days_required'))}")
        parts.append(f"sp:{js_num(acct.get('split'))}")
        parts.append(f"cap:{js_num(acct.get('payout_cap'))}")
        # eligible: wd >= wr AND bal > 0
        el = (acct.get("winning_days", 0) >= acct.get("winning_days_required", 5)
              and acct.get("balance", 0) > 0)
        parts.append(f"el:{js_num(el)}")
        parts.append(f"rk:{js_str(acct.get('risk_level', 'safe'))}")
    elif ty == "tf":
        parts.append(f"db:{js_num(acct.get('dist_to_breach', round(acct['balance'] - acct.get('floor', 0))))}")
        parts.append(f"wd:{js_num(acct.get('winning_days'))}")
        parts.append(f"wr:{js_num(acct.get('winning_days_required'))}")
        parts.append(f"sp:{js_num(acct.get('split'))}")
        parts.append(f"cap:{js_num(acct.get('payout_cap'))}")
        el = (acct.get("winning_days", 0) >= acct.get("winning_days_required", 5)
              and acct.get("profit", 0) > 0)
        parts.append(f"el:{js_num(el)}")
        parts.append(f"rk:{js_str(acct.get('risk_level', 'safe'))}")
    elif ty == "ax":
        parts.append(f"fl:{js_num(acct.get('floor'))}")
        parts.append(f"db:{js_num(acct.get('dist_dd'))}")
        sn = acct.get("safety_net")
        parts.append(f"sn:{js_num(sn)}")
        ds = round(sn - acct["balance"], 2) if sn else 0
        parts.append(f"ds:{js_num(ds)}")
        parts.append(f"sp:{js_num(acct.get('split'))}")
        cp = acct.get("consistency_pct", 0.30)
        parts.append(f"cp:{js_num(cp)}")
        parts.append(f"bd:{js_num(acct.get('consistency_best_day'))}")
        # Apex eligible: balance >= safety_net
        el = acct.get("balance", 0) >= (sn or 999999)
        parts.append(f"el:{js_num(el)}")
        parts.append(f"rk:{js_str(acct.get('risk_level', 'safe'))}")

    # PNL array
    pnl_str = "[" + ",".join(js_num(v) for v in pnl_arr) + "]"
    parts.append(f"pnl:{pnl_str}")

    # Notes
    notes = acct.get("notes", "")
    parts.append(f"n:{js_str(notes)}")

    # Target
    avg = compute_avg_daily(pnl_arr)
    profit = acct.get("profit", 0)
    bal = acct.get("balance", 0)
    if ty in ("ts", "tf"):
        gap = max(0, 2000 - profit)
        days = max(1, round(gap / abs(avg))) if avg > 0 and gap > 0 else 0
        parts.append(f"tgt:{{l:'$2k Buffer',v:2000,g:{js_num(round(gap))},d:{js_num(days)}}}")
    elif ty == "ax":
        sn = acct.get("safety_net", 0)
        gap = max(0, sn - bal)
        days = max(1, round(gap / abs(avg))) if avg > 0 and gap > 0 else 0
        parts.append(f"tgt:{{l:'Safety Net',v:{js_num(sn)},g:{js_num(round(gap))},d:{js_num(days)}}}")

    return "{" + ",".join(parts) + "}"


def build_shelved_line(json_key, acct, pnl_arr):
    """Build one shelved account JS object string."""
    parts = []
    # Determine short code
    short_map = {
        "apex_eval_101": ("ax_eval", "AX-EVAL", "Apex Trader", "ev"),
    }
    if json_key in short_map:
        k, s, f, ty = short_map[json_key]
    else:
        k = json_key
        s = json_key.upper()
        f = "Unknown"
        ty = "unk"

    bal = acct.get("balance", 0) or 0
    profit = acct.get("profit")
    if profit is None:
        starting = infer_starting_balance(acct)
        profit = round(bal - starting, 2) if bal else 0

    parts.append(f"k:{js_str(k)}")
    parts.append(f"s:{js_str(s)}")
    parts.append(f"f:{js_str(f)}")
    parts.append(f"ty:{js_str(ty)}")
    parts.append(f"bal:{js_num(bal)}")
    parts.append(f"pft:{js_num(profit)}")
    parts.append(f"date:{js_str(acct.get('shelved_date', ''))}")
    parts.append(f"reason:{js_str(acct.get('shelved_reason', ''))}")
    lesson = acct.get("notes", "")
    # Extract lesson if present
    parts.append(f"lesson:{js_str('Do not force the timeline. If the math requires oversizing, the math is wrong.')}")

    pnl_str = "[" + ",".join(js_num(v) for v in pnl_arr) + "]"
    parts.append(f"pnl:{pnl_str}")

    return "{" + ",".join(parts) + "}"


def build_data_block(data):
    """Build the full const D={...}; block."""
    pnl_arrays = build_pnl_arrays(data)
    log = data["daily_log"]
    accounts = data["accounts"]

    lines = []
    lines.append("const D={")

    # ── accounts ──
    lines.append("accounts:[")
    active_keys = [k for k, v in accounts.items()
                   if v.get("status") not in ("shelved", "breached") and k in ACCOUNT_MAP]
    for i, json_key in enumerate(active_keys):
        acct = accounts[json_key]
        line = build_account_line(json_key, acct, pnl_arrays.get(json_key, []))
        comma = "," if i < len(active_keys) - 1 else ","
        lines.append(line + comma)
    lines.append("],")

    # ── shelved ──
    lines.append("shelved:[")
    shelved_keys = [k for k, v in accounts.items() if v.get("status") in ("shelved", "breached")]
    for i, json_key in enumerate(shelved_keys):
        acct = accounts[json_key]
        line = build_shelved_line(json_key, acct, pnl_arrays.get(json_key, []))
        comma = "," if i < len(shelved_keys) - 1 else ","
        lines.append(line + comma)
    lines.append("],")

    # ── Collect FOMC dates from danger_dates ──
    fomc_iso_dates = set()
    for dd in data.get("danger_dates", []):
        if dd.get("type") == "FOMC":
            for iso in dd.get("dates", []):
                fomc_iso_dates.add(iso)

    # ── log ──
    log_entries = []
    for entry in log[1:]:  # skip pre-day
        d = fmt_date_short(entry["date"])
        p = entry["totals"]["daily_pnl"]
        p_val = round(p) if p is not None else 0
        tag = ""
        notes = entry.get("notes", "")
        # Tag as FOMC only if the date is an actual FOMC date, or the note
        # starts with / explicitly flags FOMC as the event (not just a mention)
        is_fomc_day = entry["date"] in fomc_iso_dates
        is_fomc_note = notes.upper().startswith("FOMC") or "FOMC +" in notes.upper() or "FOMC —" in notes.upper()
        if is_fomc_day or is_fomc_note:
            tag = ",t:'FOMC'"
        log_entries.append(f"{{d:'{d}',p:{p_val}{tag}}}")
    lines.append("log:[" + ",".join(log_entries) + "],")

    # ── momentum ──
    mo = data.get("momentum", {})
    wr = mo.get("win_rate_last_5", 0)
    g = mo.get("green_days_last_5", 0)
    tot = mo.get("total_days_last_5", 5)
    st = mo.get("streak", 0)
    sd = mo.get("streak_direction", "red")
    cl = mo.get("classification", "COLD")
    lines.append(f"mo:{{wr:{js_num(wr)},g:{js_num(g)},tot:{js_num(tot)},st:{js_num(st)},sd:'{sd}',cl:'{cl}'}},")

    # ── fomc ──
    danger = data.get("danger_dates", [])
    fomc_dates = []
    for dd in danger:
        if dd.get("type") == "FOMC":
            for iso in dd.get("dates", []):
                fomc_dates.append(fmt_fomc(iso))
    lines.append("fomc:[" + ",".join(f"'{d}'" for d in fomc_dates) + "],")

    # ── holidays ──
    holiday_entries = []
    for h in data.get("cme_holidays", []):
        d = h["date"]
        n = h["name"].replace("'", "\\'")
        t = h["type"]
        holiday_entries.append(f"{{d:'{d}',n:'{n}',t:'{t}'}}")
    lines.append("holidays:[" + ",".join(holiday_entries) + "]")
    lines.append("};")

    return "\n".join(lines)


def build_date_labels(data):
    """Build the const dt=[...] array content."""
    log = data["daily_log"]
    labels = ["'Pre'"]
    for entry in log[1:]:
        labels.append(f"'{fmt_date_label(entry['date'])}'")
    return "const dt=[" + ",".join(labels) + "];"


def extract_old_values(old_block):
    """Pull a few key values from old data block for diff display."""
    vals = {}
    # Extract account balances
    for m in re.finditer(r"s:'([^']+)'[^}]*bal:([\d.\-]+)", old_block):
        vals[m.group(1) + ".bal"] = m.group(2)
    for m in re.finditer(r"s:'([^']+)'[^}]*pft:([\d.\-]+)", old_block):
        vals[m.group(1) + ".pft"] = m.group(2)
    # Log count
    log_count = len(re.findall(r"\{d:'", old_block))
    vals["log_entries"] = str(log_count)
    return vals


def main():
    print("=" * 60)
    print("  sync.py — Portfolio Data -> Dashboard")
    print("=" * 60)

    # ── Load ──
    print(f"\n[1] Reading {JSON_PATH}")
    data = load_json()
    try:
        validate_data_shape(data)
    except ValueError as e:
        print(f"    ✗ ABORT: {e}")
        sys.exit(1)
    print(f"    Last updated: {data.get('last_updated')}")
    active = [k for k, v in data["accounts"].items() if v.get("status") not in ("shelved", "breached")]
    shelved = [k for k, v in data["accounts"].items() if v.get("status") in ("shelved", "breached")]
    print(f"    Accounts: {len(active)} active, {len(shelved)} shelved/breached")
    print(f"    Log entries: {len(data['daily_log']) - 1} trading days")

    # ── Derive computed fields ──
    print(f"\n[1b] Deriving computed fields (profit, buffers, risk levels, 30% rule)...")
    had_changes = derive_computed_fields(data)
    if had_changes:
        with open(JSON_PATH, "w") as f:
            json.dump(data, f, indent=2)
        print(f"     ✓ Derived fields written back to JSON")
    else:
        print(f"     ✓ All derived fields already current")

    # ── Sanity warnings ──
    sw = sanity_check_derived(data)
    if sw:
        print(f"\n[1c] Sanity warnings:")
        for w in sw:
            print(w)

    # ── Build new data block ──
    print(f"\n[2] Building data block...")
    new_block = build_data_block(data)
    new_dt = build_date_labels(data)

    # ── Read dashboard ──
    print(f"\n[3] Reading {DASH_PATH}")
    with open(DASH_PATH) as f:
        html = f.read()

    # ── Extract old block for diff ──
    old_match = re.search(
        r'/\* ══ DATA ══ \*/\n(.*?)\n/\* ══ FORMATTERS ══ \*/',
        html, re.DOTALL
    )
    if not old_match:
        print("    ERROR: Could not find data block markers in dashboard-v4.html")
        sys.exit(1)

    old_block = old_match.group(1)
    old_vals = extract_old_values(old_block)

    # ── Replace data block ──
    print(f"\n[4] Replacing data block...")
    new_section = f"/* ══ DATA ══ */\n{new_block}\n\n/* ══ FORMATTERS ══ */"
    html = html[:old_match.start()] + new_section + html[old_match.end():]

    # ── Replace date label arrays ──
    old_dt_pattern = r"const dt=\[.*?\];"
    dt_matches = list(re.finditer(old_dt_pattern, html))
    print(f"    Found {len(dt_matches)} date label arrays to update")
    # Replace from end to preserve positions
    for m in reversed(dt_matches):
        html = html[:m.start()] + new_dt + html[m.end():]

    # ── Diff summary ──
    new_vals = extract_old_values(new_block)
    print(f"\n[5] Diff summary:")
    print(f"    {'Field':<20} {'Old':>12} {'New':>12}")
    print(f"    {'─'*20} {'─'*12} {'─'*12}")
    all_keys = sorted(set(list(old_vals.keys()) + list(new_vals.keys())))
    changes = 0
    for key in all_keys:
        ov = old_vals.get(key, "—")
        nv = new_vals.get(key, "—")
        marker = " *" if ov != nv else ""
        if ov != nv:
            changes += 1
        print(f"    {key:<20} {ov:>12} {nv:>12}{marker}")
    if changes == 0:
        print("    (no changes detected)")
    else:
        print(f"\n    {changes} field(s) changed")

    # ── Write dashboard ──
    print(f"\n[6] Writing {DASH_PATH}")
    with open(DASH_PATH, "w") as f:
        f.write(html)

    # ── Copy to index.html ──
    print(f"\n[7] Copying to {INDEX_PATH}")
    shutil.copy2(DASH_PATH, INDEX_PATH)

    print(f"\n{'=' * 60}")
    print(f"  Done. Dashboard updated from {data.get('last_updated')} data.")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
