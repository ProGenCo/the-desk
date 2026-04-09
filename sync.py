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


def load_json():
    with open(JSON_PATH) as f:
        return json.load(f)


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
        if acct.get("status") == "shelved":
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
                   if v.get("status") != "shelved" and k in ACCOUNT_MAP]
    for i, json_key in enumerate(active_keys):
        acct = accounts[json_key]
        line = build_account_line(json_key, acct, pnl_arrays.get(json_key, []))
        comma = "," if i < len(active_keys) - 1 else ","
        lines.append(line + comma)
    lines.append("],")

    # ── shelved ──
    lines.append("shelved:[")
    shelved_keys = [k for k, v in accounts.items() if v.get("status") == "shelved"]
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
    print(f"    Last updated: {data.get('last_updated')}")
    active = [k for k, v in data["accounts"].items() if v.get("status") != "shelved"]
    shelved = [k for k, v in data["accounts"].items() if v.get("status") == "shelved"]
    print(f"    Accounts: {len(active)} active, {len(shelved)} shelved")
    print(f"    Log entries: {len(data['daily_log']) - 1} trading days")

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
