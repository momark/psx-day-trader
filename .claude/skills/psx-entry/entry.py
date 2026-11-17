#!/usr/bin/env python3
"""psx-entry: watch a symbol's intraday ticks and write a precise entry trigger.

    entry.py SYMBOL [--rule orb|pullback] [--long|--short]
             [--or-minutes 15] [--settle 60] [--at HH:MM]
             [--now "YYYY-MM-DD HH:MM"] [--offline PATH] [--json]
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
import sys
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "_lib"))
import psx_http  # noqa: E402
# Share bucket helper with the scanner so OR math stays in one place.
sys.path.insert(0, os.path.join(HERE, "..", "psx-scanner"))
from scan import ticks_to_5min_bars, opening_range, OPEN_T, now_pkt  # noqa: E402

PKT = ZoneInfo("Asia/Karachi")

OUT_DIR = "psx"
ENTRY_JSON = os.path.join(OUT_DIR, "entry.json")
ENTRIES_DIR = os.path.join(OUT_DIR, "entries")


def _bar_closed(bar_time: dt.datetime, now: dt.datetime, settle_s: int) -> bool:
    """A 5-minute bar is considered closed only `settle_s` seconds after its
    end: the portal can still write a trailing tick into the bucket for a
    second or two after the minute ticks over."""
    bar_end = bar_time + dt.timedelta(minutes=5)
    return now >= bar_end + dt.timedelta(seconds=settle_s)


def orb_trigger(bars, orange, side: str, cutoff_time: dt.time,
                or_minutes: int, now: dt.datetime, settle_s: int):
    or_end = dt.time(OPEN_T.hour, min(59, OPEN_T.minute + or_minutes))
    for b in bars:
        if b["t"].date() != bars[0]["t"].date():
            continue
        local_t = b["t"].timetz().replace(tzinfo=None)
        if local_t < or_end:
            continue
        if local_t > cutoff_time:
            break
        if not _bar_closed(b["t"], now, settle_s):
            break
        if side == "long" and b["c"] > orange["high"] and b["v"] > 0:
            return b
        if side == "short" and b["c"] < orange["low"] and b["v"] > 0:
            return b
    return None


def pullback_trigger(bars, orange, side: str, cutoff_time: dt.time,
                     now: dt.datetime, settle_s: int):
    broke = False
    for b in bars:
        if b["t"].date() != bars[0]["t"].date():
            continue
        local_t = b["t"].timetz().replace(tzinfo=None)
        if local_t > cutoff_time:
            break
        if not _bar_closed(b["t"], now, settle_s):
            break
        if side == "long":
            if not broke:
                if b["c"] > orange["high"]:
                    broke = True
                continue
            if b["l"] <= orange["high"] <= b["c"]:
                return b
        else:
            if not broke:
                if b["c"] < orange["low"]:
                    broke = True
                continue
            if b["h"] >= orange["low"] >= b["c"]:
                return b
    return None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="psx-entry")
    p.add_argument("symbol")
    p.add_argument("--rule", choices=["orb", "pullback"], default="orb")
    p.add_argument("--long", action="store_true")
    p.add_argument("--short", action="store_true")
    p.add_argument("--or-minutes", type=int, default=15)
    p.add_argument("--settle", type=int, default=60)
    p.add_argument("--at")
    p.add_argument("--now")
    p.add_argument("--offline")
    p.add_argument("--json", action="store_true")
    p.add_argument("--brief", action="store_true")
    p.add_argument("--force", action="store_true",
                   help="Overwrite psx/entry.json even if it holds a fired entry "
                        "for a different symbol.")
    args = p.parse_args(argv)

    if args.offline:
        os.environ["PSX_FIXTURES_DIR"] = args.offline
        psx_http.FIXTURES_DIR = args.offline

    side = "short" if args.short else "long"
    clock = now_pkt(args.now)
    cutoff = args.at or f"{clock.hour:02d}:{clock.minute:02d}"
    hh, mm = cutoff.split(":")
    cut_time = dt.time(int(hh), int(mm))

    try:
        ticks = psx_http.intraday(args.symbol)
    except psx_http.PSXError as e:
        print(f"intraday({args.symbol}): {e}", file=sys.stderr)
        return 1
    bars = ticks_to_5min_bars(ticks)
    if not bars:
        print(f"no intraday ticks for {args.symbol}", file=sys.stderr)
        return 1

    orange = opening_range(bars, OPEN_T, args.or_minutes)
    if orange is None:
        print(f"no opening range for {args.symbol}", file=sys.stderr)
        return 1

    if args.rule == "orb":
        trig = orb_trigger(bars, orange, side, cut_time,
                           args.or_minutes, clock, args.settle)
    else:
        trig = pullback_trigger(bars, orange, side, cut_time, clock, args.settle)

    out = {
        "symbol": args.symbol, "side": side, "rule": args.rule,
        "or_high": round(orange["high"], 2), "or_low": round(orange["low"], 2),
        "date": orange.get("date"),
    }
    if trig is None:
        out["status"] = "no_entry"
    else:
        out.update({
            "trigger_time": trig["t"].strftime("%H:%M"),
            "trigger_price": round(trig["c"], 2),
            "bar": {
                "t": trig["t"].isoformat(),
                "o": round(trig["o"], 2), "h": round(trig["h"], 2),
                "l": round(trig["l"], 2), "c": round(trig["c"], 2),
                "v": trig["v"],
            },
        })

    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(ENTRIES_DIR, exist_ok=True)
    # Always write a per-symbol audit copy so a later scan can't lose history.
    audit_path = os.path.join(ENTRIES_DIR, f"{args.symbol}.json")
    with open(audit_path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)

    # Guard psx/entry.json: don't let a scan of another symbol silently clobber
    # a fired entry that psx-exit / psx-budget / psx-ticket are chained to.
    skipped_entry_json = False
    if os.path.exists(ENTRY_JSON) and not args.force:
        try:
            with open(ENTRY_JSON, encoding="utf-8") as fh:
                existing = json.load(fh)
        except Exception:
            existing = None
        if (existing and existing.get("status") != "no_entry"
                and existing.get("symbol") != args.symbol):
            skipped_entry_json = True
            print(f"refusing to overwrite psx/entry.json: it holds a fired entry "
                  f"for {existing['symbol']} at "
                  f"PKR {existing.get('trigger_price')}. "
                  f"Audit copy saved to {audit_path}. "
                  f"Pass --force to replace, or delete psx/entry.json first.",
                  file=sys.stderr)
    if not skipped_entry_json:
        with open(ENTRY_JSON, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2)

    if args.json:
        print(json.dumps(out, indent=2))
    else:
        if out.get("status") == "no_entry":
            print(f"{args.symbol} {args.rule} {side}: no entry before {cutoff}",
                  file=sys.stderr)
        else:
            print(f"{args.symbol} {args.rule} {side}: "
                  f"TRIGGER at {out['trigger_time']} @ {out['trigger_price']}",
                  file=sys.stderr)

    if args.brief:
        sym = args.symbol
        direction = "buy" if side == "long" else "sell short"
        or_lo, or_hi = out["or_low"], out["or_high"]
        if args.rule == "orb":
            if side == "long":
                fired_eng = f"broke above the morning high of PKR {or_hi}"
                miss_eng = (f"has not broken above its morning range "
                            f"({or_lo}-{or_hi}) yet")
            else:
                fired_eng = f"broke below the morning low of PKR {or_lo}"
                miss_eng = (f"has not broken below its morning range "
                            f"({or_lo}-{or_hi}) yet")
        else:
            if side == "long":
                fired_eng = (f"broke above the morning high of PKR {or_hi} "
                             f"and pulled back to it")
                miss_eng = (f"has not pulled back to its morning range "
                            f"({or_lo}-{or_hi}) yet")
            else:
                fired_eng = (f"broke below the morning low of PKR {or_lo} "
                             f"and pulled back to it")
                miss_eng = (f"has not pulled back to its morning range "
                            f"({or_lo}-{or_hi}) yet")
        if out.get("status") == "no_entry":
            print(f"Entry says: {sym} {miss_eng}. No {direction} signal.")
        else:
            print(f"Entry says: {direction} {sym} at PKR {out['trigger_price']} "
                  f"({out['trigger_time']}) - it {fired_eng}.")
        if skipped_entry_json:
            print(f"Entry says: kept the existing plan in psx/entry.json "
                  f"(it already holds a fired entry for another symbol). "
                  f"This {sym} result is saved to {audit_path} - "
                  f"pass --force if you want it to become the active plan.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        psx_http.close()
