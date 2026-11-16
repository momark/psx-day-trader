#!/usr/bin/env python3
"""psx-risk: pre-flight 12 checks on psx/order.json.

    check.py [--order FILE] [--config FILE] [--prev-close P]
             [--daily-loss-cap L] [--shariah strict|warn|off]
             [--allow-circuit] [--now "YYYY-MM-DD HH:MM"] [--json]
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
import re
import sys
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "_lib"))
import psx_http  # noqa: E402
from psx_fingerprint import fingerprint  # noqa: E402

PKT = ZoneInfo("Asia/Karachi")

OUT_DIR = "psx"
STATE_DIR = os.path.join(OUT_DIR, "state")
ORDER_JSON = os.path.join(OUT_DIR, "order.json")
CONFIG_JSON = os.path.join(OUT_DIR, "config.json")
RISK_JSON = os.path.join(STATE_DIR, "risk.json")
BROKER_JSON = os.path.join(STATE_DIR, "broker.json")

# PSX 2026 holiday list (dates PSX trades are closed).  Moon-dependent Eid
# dates are flagged as "to confirm" and warn only.  Source: PSX/N-89,
# 20 Jan 2026.
PSX_2026_HOLIDAYS = {
    "2026-02-05": "Kashmir Day",
    "2026-03-21": "Eid-ul-Fitr (to confirm)",
    "2026-03-22": "Eid-ul-Fitr (to confirm)",
    "2026-03-23": "Pakistan Day / Eid-ul-Fitr (to confirm)",
    "2026-05-01": "Labour Day",
    "2026-05-26": "Eid-ul-Azha (to confirm)",
    "2026-05-27": "Eid-ul-Azha (to confirm)",
    "2026-05-28": "Youm-e-Takbeer / Eid-ul-Azha (to confirm)",
    "2026-06-25": "Ashura (to confirm)",
    "2026-06-26": "Ashura (to confirm)",
    "2026-08-14": "Independence Day",
    "2026-08-25": "Milad-un-Nabi (to confirm)",
    "2026-11-09": "Allama Iqbal Day",
    "2026-12-25": "Quaid-e-Azam Day",
}
# 2027 PSX holidays are not yet published (as of 2026-11-17).  Flagged
# separately so check #7 can WARN not FAIL.
PSX_2027_HOLIDAYS: dict[str, str] = {}


# ---------------- session windows (Asia/Karachi)

REGULAR_SESSION = (dt.time(9, 32), dt.time(15, 30))   # Mon-Thu
FRIDAY_MORNING = (dt.time(9, 17), dt.time(12, 0))
FRIDAY_AFTERNOON = (dt.time(14, 32), dt.time(16, 30))
# Ramadan 2026 (PSX/N-209, 18 Feb 2026): Mon-Thu 09:17-13:30, Fri 12:30.
RAMADAN_SESSION = (dt.time(9, 17), dt.time(13, 30))
RAMADAN_FRIDAY = (dt.time(9, 17), dt.time(12, 30))

RAMADAN_2026 = (dt.date(2026, 2, 18), dt.date(2026, 3, 20))  # approx


def in_trading_window(now: dt.datetime) -> tuple[bool, str]:
    d = now.date()
    iso = d.isoformat()
    if iso in PSX_2026_HOLIDAYS:
        return False, f"holiday: {PSX_2026_HOLIDAYS[iso]}"
    if iso in PSX_2027_HOLIDAYS:
        return False, f"holiday: {PSX_2027_HOLIDAYS[iso]}"
    if d.year == 2027 and iso not in PSX_2027_HOLIDAYS:
        # Warn tier for 2027 - calendar not yet published
        pass
    wd = d.weekday()
    if wd == 6:  # Sunday
        return False, "weekend (Sun)"
    if wd == 5:  # Saturday
        return False, "weekend (Sat)"
    t = now.time()
    is_ramadan = RAMADAN_2026[0] <= d <= RAMADAN_2026[1]
    if wd == 4:  # Friday
        a, b = RAMADAN_FRIDAY if is_ramadan else FRIDAY_MORNING
        if a <= t <= b:
            return True, "Friday session-1"
        if not is_ramadan:
            a2, b2 = FRIDAY_AFTERNOON
            if a2 <= t <= b2:
                return True, "Friday session-2"
        return False, "outside Friday window"
    # Mon-Thu
    a, b = RAMADAN_SESSION if is_ramadan else REGULAR_SESSION
    if a <= t <= b:
        return True, "regular session (Ramadan)" if is_ramadan else "regular session"
    return False, f"outside session {a:%H:%M}-{b:%H:%M}"


# ---------------- Shariah universe

def kmi_all_symbols(offline: str | None) -> set[str]:
    try:
        if offline:
            os.environ["PSX_FIXTURES_DIR"] = offline
            psx_http.FIXTURES_DIR = offline
        html = psx_http.index_constituents("KMIALLSHR")
    except Exception as e:
        print(f"could not fetch KMI-ALL: {e}", file=sys.stderr)
        return set()
    return set(re.findall(r'href="/company/([A-Z0-9\-]+)"', html))


# ---------------- individual checks

def _check(name, status, detail): return {"name": name, "status": status, "detail": detail}


def all_checks(order: dict, config: dict, args, now: dt.datetime,
               kmi_all: set[str]) -> list[dict]:
    out = []
    side = order["side"]
    qty = int(order["qty"])
    entry = float(order["entry"])
    stop = float(order["stop"])
    target = float(order["target"])
    gross_cost = float(order["gross_cost"])
    cash = float(config["cash_available"])
    pct = float(config["max_position_pct"])

    # 1. shape
    bad = []
    if qty <= 0: bad.append("qty")
    for p in (entry, stop, target):
        if p <= 0: bad.append("price")
    if side == "long" and stop >= entry: bad.append("stop_side")
    if side == "short" and stop <= entry: bad.append("stop_side")
    out.append(_check("shape",
                      "FAIL" if bad else "PASS",
                      f"qty={qty}, {side}" + (f"; bad={bad}" if bad else "")))

    # 2. cash
    out.append(_check("cash",
                      "PASS" if gross_cost <= cash else "FAIL",
                      f"{gross_cost} <= {cash}"))

    # 3. position_pct
    max_val = cash * pct / 100
    out.append(_check("position_pct",
                      "PASS" if gross_cost <= max_val else "FAIL",
                      f"{gross_cost} <= {max_val:.0f} ({pct}% cap)"))

    # 4. circuit_breaker
    prev = args.prev_close
    if prev is None:
        out.append(_check("circuit_breaker", "WARN",
                          "no --prev-close given; skip"))
    else:
        band = max(prev * 0.08, 1.0)
        hi, lo = prev + band, prev - band
        oob = []
        for name, p in (("stop", stop), ("target", target)):
            if p > hi or p < lo:
                oob.append(f"{name}={p:.2f} outside [{lo:.2f}, {hi:.2f}]")
        if oob and not args.allow_circuit:
            out.append(_check("circuit_breaker", "FAIL", "; ".join(oob)))
        elif oob:
            out.append(_check("circuit_breaker", "WARN",
                              "outside circuit; --allow-circuit passed"))
        else:
            out.append(_check("circuit_breaker", "PASS",
                              f"inside [{lo:.2f}, {hi:.2f}] (prev={prev}, 8% or Re 1)"))

    # 5. net_rr
    net_rr = float(order["net_rr"])
    out.append(_check("net_rr",
                      "PASS" if net_rr >= 0.5 else "FAIL",
                      f"net R/R={net_rr} (needs >= 0.5)"))

    # 6. daily_loss
    cap = args.daily_loss_cap or config.get("daily_loss_cap", 15_000)
    today_loss = _todays_realised_loss(now.date())
    net_risk = float(order["net_risk_pkr"])
    out.append(_check("daily_loss",
                      "PASS" if today_loss + net_risk <= cap else "FAIL",
                      f"today={today_loss} + this={net_risk} <= {cap}"))

    # 7. trading_window
    ok, why = in_trading_window(now)
    if ok:
        out.append(_check("trading_window", "PASS", why))
    else:
        tier = "WARN" if now.date().year == 2027 else "FAIL"
        out.append(_check("trading_window", tier, why))

    # 8. short_sell
    if side == "short":
        out.append(_check("short_sell", "WARN",
                          "PSX short selling requires NCCPL SLB; retail cash "
                          "accounts cannot do this"))
    else:
        out.append(_check("short_sell", "PASS", "long trade"))

    # 9. shariah
    mode = args.shariah or "off"
    if mode == "off":
        out.append(_check("shariah", "PASS", "off"))
    else:
        sym = order["symbol"]
        is_shariah = sym in kmi_all
        if is_shariah:
            out.append(_check("shariah", "PASS", f"{sym} in KMI-ALL"))
        else:
            out.append(_check("shariah",
                              "FAIL" if mode == "strict" else "WARN",
                              f"{sym} not in KMI-ALL"))

    # 10. duplicate
    fp = fingerprint(order)
    dup = _duplicate_fingerprint(fp)
    out.append(_check("duplicate",
                      "FAIL" if dup else "PASS",
                      "duplicate" if dup else "fresh"))

    # 11. fingerprint_stable (always PASS - surfaces the tag)
    out.append(_check("fingerprint_stable", "PASS", fp))

    # 12. cost_sanity
    rtf = float(order["costs"]["round_trip_fixed"])
    out.append(_check("cost_sanity",
                      "PASS" if rtf >= 50 else "WARN",
                      f"round-trip fixed = {rtf}"))

    return out


def _todays_realised_loss(today: dt.date) -> float:
    if not os.path.exists(BROKER_JSON):
        return 0.0
    try:
        with open(BROKER_JSON, encoding="utf-8") as fh:
            obj = json.load(fh)
    except Exception:
        return 0.0
    total = 0.0
    for row in obj.get("fills", []):
        d = row.get("date") or ""
        if d == today.isoformat() and row.get("realised_pkr", 0) < 0:
            total += -row["realised_pkr"]
    return total


def _duplicate_fingerprint(fp: str) -> bool:
    if not os.path.exists(BROKER_JSON):
        return False
    try:
        with open(BROKER_JSON, encoding="utf-8") as fh:
            obj = json.load(fh)
    except Exception:
        return False
    for order in obj.get("orders", []):
        if order.get("fingerprint") == fp and order.get("status") != "cancelled":
            return True
    return False


# ---------------- CLI

def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="psx-risk")
    p.add_argument("--order", default=ORDER_JSON)
    p.add_argument("--config", default=CONFIG_JSON)
    p.add_argument("--prev-close", type=float)
    p.add_argument("--daily-loss-cap", type=float)
    p.add_argument("--shariah", choices=["strict", "warn", "off"])
    p.add_argument("--allow-circuit", action="store_true")
    p.add_argument("--now")
    p.add_argument("--offline")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    if not os.path.exists(args.order):
        print(f"no order at {args.order}; run psx-budget first", file=sys.stderr)
        return 2
    if not os.path.exists(args.config):
        print(f"no config at {args.config}; run psx-budget first", file=sys.stderr)
        return 2
    with open(args.order, encoding="utf-8") as fh:
        order = json.load(fh)
    with open(args.config, encoding="utf-8") as fh:
        config = json.load(fh)

    if args.now:
        d, t = args.now.split(" ", 1) if " " in args.now else (
            dt.date.today().isoformat(), args.now)
        y, mo, da = [int(x) for x in d.split("-")]
        hh, mm = [int(x) for x in t.split(":")]
        now = dt.datetime(y, mo, da, hh, mm, tzinfo=PKT)
    else:
        now = dt.datetime.now(PKT)

    kmi_all = kmi_all_symbols(args.offline) if args.shariah and args.shariah != "off" else set()
    checks = all_checks(order, config, args, now, kmi_all)

    statuses = [c["status"] for c in checks]
    verdict = ("FAIL" if "FAIL" in statuses
               else "WARN" if "WARN" in statuses else "PASS")

    out = {
        "checked_at": now.isoformat(timespec="seconds"),
        "fingerprint": fingerprint(order),
        "checks": checks,
        "verdict": verdict,
    }
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(RISK_JSON, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)

    if args.json:
        print(json.dumps(out, indent=2))
    else:
        for c in checks:
            print(f"  {c['status']:<4}  {c['name']:<20} {c['detail']}")
        print(f"\nverdict: {verdict}  (fingerprint {out['fingerprint']})")
    return 0 if verdict != "FAIL" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        psx_http.close()
