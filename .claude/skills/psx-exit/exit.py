#!/usr/bin/env python3
"""psx-exit: read entry.json + opening range, write plan.json with stop + target.

    exit.py [--stop or-mid|or-low|or-high|price:X|pct:X]
            [--rr 2] [--entry PATH] [--out PATH] [--json]
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from decimal import Decimal, ROUND_HALF_UP

OUT_DIR = "psx"
ENTRY_JSON = os.path.join(OUT_DIR, "entry.json")
PLAN_JSON = os.path.join(OUT_DIR, "plan.json")


def _d(x) -> Decimal:
    return Decimal(str(x))


def _round2(x: Decimal) -> float:
    return float(x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def compute_stop(side: str, entry: Decimal, or_high: Decimal, or_low: Decimal,
                 rule: str) -> tuple[Decimal, str, str]:
    """Return (stop_price, stop_label, stop_detail) for the given rule."""
    if rule == "or-mid":
        mid = (or_high + or_low) / 2
        return mid, "or-mid", "mid-point of the opening range"
    if rule == "or-low":
        return or_low, "or-low", "opening-range low (session open + OR window)"
    if rule == "or-high":
        return or_high, "or-high", "opening-range high (session open + OR window)"
    if rule.startswith("price:"):
        p = _d(rule.split(":", 1)[1])
        return p, f"price:{p}", "fixed price stop"
    if rule.startswith("pct:"):
        pct = _d(rule.split(":", 1)[1])
        if side == "long":
            p = entry * (1 - pct / 100)
        else:
            p = entry * (1 + pct / 100)
        return p, f"pct:{pct}", f"{pct}% away from entry"
    raise ValueError(f"unknown stop rule: {rule!r}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="psx-exit")
    p.add_argument("--stop", default="or-mid")
    p.add_argument("--rr", type=float, default=2.0)
    p.add_argument("--entry", default=ENTRY_JSON)
    p.add_argument("--out", default=PLAN_JSON)
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    if not os.path.exists(args.entry):
        print(f"no entry file at {args.entry}; run psx-entry first", file=sys.stderr)
        return 2
    with open(args.entry, encoding="utf-8") as fh:
        entry = json.load(fh)
    if entry.get("status") == "no_entry":
        print("entry file says no_entry; nothing to plan", file=sys.stderr)
        return 1

    side = entry["side"]
    entry_price = _d(entry["trigger_price"])
    or_high = _d(entry["or_high"])
    or_low = _d(entry["or_low"])

    try:
        stop, label, detail = compute_stop(side, entry_price, or_high, or_low, args.stop)
    except ValueError as e:
        print(f"bad --stop: {e}", file=sys.stderr)
        return 2

    # sanity: stop must be on the correct side of entry
    if side == "long" and stop >= entry_price:
        print(f"refusing: stop {stop} must be below entry {entry_price} for a long",
              file=sys.stderr)
        return 1
    if side == "short" and stop <= entry_price:
        print(f"refusing: stop {stop} must be above entry {entry_price} for a short",
              file=sys.stderr)
        return 1

    risk = abs(entry_price - stop)
    if side == "long":
        target = entry_price + risk * _d(args.rr)
    else:
        target = entry_price - risk * _d(args.rr)

    plan = {
        "symbol": entry["symbol"], "side": side,
        "entry": _round2(entry_price),
        "stop": _round2(stop),
        "stop_label": label,
        "stop_detail": detail,
        "target": _round2(target),
        "rr": float(args.rr),
        "risk_per_share": _round2(risk),
        "reward_per_share": _round2(risk * _d(args.rr)),
        "source_entry_time": entry.get("trigger_time"),
        "or_high": entry["or_high"], "or_low": entry["or_low"],
    }

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(plan, fh, indent=2)
    if args.json:
        print(json.dumps(plan, indent=2))
    else:
        print(f"wrote {args.out}: {plan['symbol']} {plan['side']} "
              f"entry {plan['entry']}  stop {plan['stop']} ({plan['stop_label']})  "
              f"target {plan['target']}  R/R={plan['rr']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
