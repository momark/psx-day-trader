#!/usr/bin/env python3
"""psx-alerts: lightweight price-and-time alert list.

    alerts.py add  SYMBOL RULE [VALUE]        # rules: above|below|pct_move|time|opening_bell
    alerts.py check [--now "YYYY-MM-DD HH:MM"] [--offline PATH]
    alerts.py list  [--status active|triggered|all]
    alerts.py clear --triggered | --all
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
import sys
import uuid
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "_lib"))
import psx_http  # noqa: E402

PKT = ZoneInfo("Asia/Karachi")

OUT_DIR = "psx"
STATE_DIR = os.path.join(OUT_DIR, "state")
ALERTS_JSON = os.path.join(STATE_DIR, "alerts.json")

OPEN_MONTHU = dt.time(9, 32)
OPEN_FRI = dt.time(9, 17)
OPEN_RAMADAN = dt.time(9, 17)


def _load() -> dict:
    if not os.path.exists(ALERTS_JSON):
        return {"alerts": []}
    with open(ALERTS_JSON, encoding="utf-8") as fh:
        return json.load(fh)


def _save(state: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(ALERTS_JSON, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)


def _now(override: str | None) -> dt.datetime:
    if override:
        d, t = override.split(" ", 1) if " " in override else (
            dt.date.today().isoformat(), override)
        y, mo, da = [int(x) for x in d.split("-")]
        hh, mm = [int(x) for x in t.split(":")]
        return dt.datetime(y, mo, da, hh, mm, tzinfo=PKT)
    return dt.datetime.now(PKT)


def _session_open(when: dt.datetime) -> dt.time:
    # Keep in sync with psx-risk's calendar.
    if when.date().weekday() == 4:
        return OPEN_FRI
    return OPEN_MONTHU


def cmd_add(args) -> int:
    rule = args.rule
    sym = args.symbol.upper()
    value: float | None = None
    if rule in ("above", "below", "pct_move"):
        if args.value is None:
            print(f"rule {rule} needs a value", file=sys.stderr)
            return 2
        value = float(args.value)
    elif rule == "time":
        if args.value is None:
            print("rule time needs a HH:MM value", file=sys.stderr)
            return 2
        hh, mm = args.value.split(":")
        value = int(hh) * 60 + int(mm)
    elif rule == "opening_bell":
        value = None
    else:
        print(f"unknown rule {rule}", file=sys.stderr)
        return 2
    state = _load()
    alert = {
        "id": uuid.uuid4().hex[:8],
        "symbol": sym, "rule": rule, "value": value,
        "created": dt.datetime.now(PKT).isoformat(timespec="seconds"),
        "status": "active",
    }
    state["alerts"].append(alert)
    _save(state)
    print(f"added {alert['id']}: {sym} {rule}"
          f"{' ' + str(args.value) if args.value is not None else ''}",
          file=sys.stderr)
    if args.brief:
        phrase = {
            "above": f"{sym} goes to PKR {args.value} or higher",
            "below": f"{sym} drops to PKR {args.value} or lower",
            "pct_move": f"{sym} moves {args.value}% from yesterday's close",
            "time": f"the clock hits {args.value} PKT",
            "opening_bell": "the market opens",
        }.get(rule, f"{sym} {rule} {args.value}")
        print(f"Alerts says: I'll watch for {phrase}. "
              f"Run `alerts.py check` to test it against the latest price.")
    return 0


def _latest(sym: str) -> tuple[float, float] | None:
    """Return (latest_price, prev_close) if available, else None."""
    try:
        ticks = psx_http.intraday(sym)
    except psx_http.PSXError as e:
        print(f"  {sym}: {e}", file=sys.stderr)
        return None
    if not ticks:
        return None
    latest = float(ticks[0][1])  # portal is newest-first
    try:
        bars = psx_http.eod(sym)
    except psx_http.PSXError:
        return (latest, latest)
    if not bars:
        return (latest, latest)
    bars = sorted(bars, key=lambda b: b[0])
    # the newest EOD bar's close = previous day's close (today not yet closed)
    prev_close = float(bars[-1][1])
    return (latest, prev_close)


def cmd_check(args) -> int:
    if args.offline:
        os.environ["PSX_FIXTURES_DIR"] = args.offline
        psx_http.FIXTURES_DIR = args.offline
    now = _now(args.now)
    state = _load()
    fired = []
    for a in state["alerts"]:
        if a["status"] != "active":
            continue
        rule = a["rule"]
        sym = a["symbol"]
        price_info = None
        if rule in ("above", "below", "pct_move"):
            price_info = _latest(sym)
            if price_info is None:
                continue
            latest, prev = price_info
            hit = False
            if rule == "above" and latest >= a["value"]:
                hit = True
            elif rule == "below" and latest <= a["value"]:
                hit = True
            elif rule == "pct_move":
                want = float(a["value"])
                if prev > 0:
                    pct = (latest - prev) / prev * 100
                    if want >= 0 and pct >= want:
                        hit = True
                    if want < 0 and pct <= want:
                        hit = True
            if hit:
                a["status"] = "triggered"
                a["triggered_at"] = now.isoformat(timespec="seconds")
                a["triggered_price"] = latest
                fired.append(a)
        elif rule == "time":
            hhmm = int(a["value"])
            cur = now.hour * 60 + now.minute
            if cur >= hhmm:
                a["status"] = "triggered"
                a["triggered_at"] = now.isoformat(timespec="seconds")
                fired.append(a)
        elif rule == "opening_bell":
            openT = _session_open(now)
            if now.time() >= openT:
                a["status"] = "triggered"
                a["triggered_at"] = now.isoformat(timespec="seconds")
                fired.append(a)
    _save(state)
    if args.json:
        print(json.dumps(fired, indent=2))
    else:
        if not fired:
            print("no alerts fired", file=sys.stderr)
        else:
            for a in fired:
                extra = (f" @ {a.get('triggered_price')}"
                         if a.get("triggered_price") is not None else "")
                print(f"  TRIGGERED  {a['id']}  {a['symbol']} {a['rule']} "
                      f"{a.get('value', '')}{extra}", file=sys.stderr)
    if args.brief:
        if not fired:
            active = sum(1 for a in state["alerts"] if a["status"] == "active")
            print(f"Alerts says: nothing fired. "
                  f"{active} alert(s) still watching.")
        else:
            parts = []
            for a in fired:
                tp = a.get("triggered_price")
                tail = f" (hit PKR {tp})" if tp is not None else ""
                parts.append(f"{a['symbol']} {a['rule']} {a.get('value','')}"
                             .strip() + tail)
            print(f"Alerts says: {len(fired)} alert(s) fired - "
                  + "; ".join(parts) + ".")
    return 0


def cmd_list(args) -> int:
    state = _load()
    alerts = state["alerts"]
    if args.status and args.status != "all":
        alerts = [a for a in alerts if a["status"] == args.status]
    if args.json:
        print(json.dumps(alerts, indent=2))
        return 0
    if not alerts:
        print("no alerts", file=sys.stderr)
        if args.brief:
            print("Alerts says: no alerts on file.")
        return 0
    for a in alerts:
        print(f"  {a['id']}  {a['symbol']:<10} {a['rule']:<13} "
              f"val={a.get('value')}  status={a['status']}")
    if args.brief:
        by_status: dict[str, int] = {}
        for a in alerts:
            by_status[a["status"]] = by_status.get(a["status"], 0) + 1
        parts = ", ".join(f"{n} {s}" for s, n in by_status.items())
        print(f"Alerts says: {len(alerts)} alert(s) on file ({parts}).")
    return 0


def cmd_clear(args) -> int:
    state = _load()
    before = len(state["alerts"])
    if args.triggered:
        state["alerts"] = [a for a in state["alerts"] if a["status"] != "triggered"]
    elif args.all:
        state["alerts"] = []
    else:
        print("pass --triggered or --all", file=sys.stderr)
        return 2
    _save(state)
    cleared = before - len(state["alerts"])
    print(f"cleared {cleared} alerts", file=sys.stderr)
    if args.brief:
        print(f"Alerts says: cleared {cleared} alert(s).")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="psx-alerts")
    sub = p.add_subparsers(dest="cmd", required=True)
    pa = sub.add_parser("add")
    pa.add_argument("symbol")
    pa.add_argument("rule",
                    choices=["above", "below", "pct_move", "time", "opening_bell"])
    pa.add_argument("value", nargs="?")
    pa.add_argument("--brief", action="store_true")
    pc = sub.add_parser("check")
    pc.add_argument("--offline")
    pc.add_argument("--now")
    pc.add_argument("--json", action="store_true")
    pc.add_argument("--brief", action="store_true")
    pl = sub.add_parser("list")
    pl.add_argument("--status", choices=["active", "triggered", "all"], default="all")
    pl.add_argument("--json", action="store_true")
    pl.add_argument("--brief", action="store_true")
    pcl = sub.add_parser("clear")
    pcl.add_argument("--triggered", action="store_true")
    pcl.add_argument("--all", action="store_true")
    pcl.add_argument("--brief", action="store_true")
    args = p.parse_args(argv)
    if args.cmd == "add": return cmd_add(args)
    if args.cmd == "check": return cmd_check(args)
    if args.cmd == "list": return cmd_list(args)
    if args.cmd == "clear": return cmd_clear(args)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        psx_http.close()
