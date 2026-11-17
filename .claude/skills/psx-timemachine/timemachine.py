#!/usr/bin/env python3
"""psx-timemachine: Monte Carlo + real-session replay of a stop-and-target plan.

    timemachine.py [--symbol S] [--entry E --stop S --target T]
                   [--draws 10000] [--horizon-days 5] [--seed 19]
                   [--pool-days 60] [--offline PATH] [--json]
"""
from __future__ import annotations
import argparse
import json
import os
import random
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "_lib"))
import psx_http  # noqa: E402

OUT_DIR = "psx"
PLAN_JSON = os.path.join(OUT_DIR, "plan.json")
TM_JSON = os.path.join(OUT_DIR, "timemachine.json")

COIN_FLIP = 5.0
STRONG = 15.0
MANY_NEITHER = 40.0


def daily_returns(bars_oldest_first: list[list]) -> list[float]:
    """Close-to-close returns (as decimals) for the pool."""
    closes = [float(b[1]) for b in bars_oldest_first]
    out = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0:
            out.append((closes[i] - closes[i - 1]) / closes[i - 1])
    return out


def sim_one_path(rng: random.Random, pool: list[float], entry: float,
                 stop: float, target: float, horizon: int,
                 side: str) -> tuple[str, float]:
    """Return (outcome, final_price).  Outcome is 'target' | 'stop' | 'neither'."""
    price = entry
    for _ in range(horizon):
        r = pool[rng.randrange(len(pool))]
        prev = price
        price = price * (1 + r)
        # conservative "daily range" bracket between prev and today's close
        lo = min(prev, price) * 0.995
        hi = max(prev, price) * 1.005
        if side == "long":
            if hi >= target:
                return "target", price
            if lo <= stop:
                return "stop", price
        else:  # short
            if lo <= target:
                return "target", price
            if hi >= stop:
                return "stop", price
    return "neither", price


def ascii_fan(finals: list[float], width: int = 48) -> str:
    """Tiny histogram of the final-price distribution."""
    if not finals:
        return "(no data)"
    lo, hi = min(finals), max(finals)
    if lo == hi:
        return f"all finals at {lo:.2f}"
    bins = 20
    counts = [0] * bins
    for v in finals:
        i = min(bins - 1, int((v - lo) / (hi - lo) * bins))
        counts[i] += 1
    peak = max(counts) or 1
    lines = []
    for i, c in enumerate(counts):
        bar = "#" * int(c / peak * width)
        edge = lo + (i + 0.5) * (hi - lo) / bins
        lines.append(f"  {edge:>8.2f}  |{bar}")
    return "\n".join(lines)


def verdict(t_pct: float, s_pct: float, n_pct: float) -> str:
    diff = t_pct - s_pct
    if n_pct > MANY_NEITHER:
        return "MANY_NEITHER"
    if abs(diff) < COIN_FLIP:
        return "COIN_FLIP"
    if diff >= STRONG:
        return "STRONG_TARGET"
    if -diff >= STRONG:
        return "STRONG_STOP"
    return "MIXED"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="psx-timemachine")
    p.add_argument("--symbol")
    p.add_argument("--entry", type=float)
    p.add_argument("--stop", type=float)
    p.add_argument("--target", type=float)
    p.add_argument("--draws", type=int, default=10_000)
    p.add_argument("--horizon-days", type=int, default=5)
    p.add_argument("--seed", type=int, default=19)
    p.add_argument("--pool-days", type=int, default=60)
    p.add_argument("--offline")
    p.add_argument("--json", action="store_true")
    p.add_argument("--brief", action="store_true")
    args = p.parse_args(argv)

    if args.offline:
        os.environ["PSX_FIXTURES_DIR"] = args.offline
        psx_http.FIXTURES_DIR = args.offline

    if args.symbol and args.entry and args.stop and args.target:
        symbol, entry, stop, target = (args.symbol, args.entry, args.stop, args.target)
        side = "long" if target > entry else "short"
    elif os.path.exists(PLAN_JSON):
        with open(PLAN_JSON, encoding="utf-8") as fh:
            plan = json.load(fh)
        symbol = plan["symbol"]
        entry = plan["entry"]
        stop = plan["stop"]
        target = plan["target"]
        side = plan["side"]
    else:
        print("provide --symbol/--entry/--stop/--target or run psx-exit first",
              file=sys.stderr)
        return 2

    try:
        bars = psx_http.eod(symbol)
    except psx_http.PSXError as e:
        print(f"eod({symbol}): {e}", file=sys.stderr)
        return 1
    bars = sorted(bars, key=lambda b: b[0])
    if len(bars) < args.pool_days + 1:
        print(f"only {len(bars)} bars available; need {args.pool_days+1}",
              file=sys.stderr)
        return 1
    pool_bars = bars[-(args.pool_days + 1):]
    pool = daily_returns(pool_bars)
    if not pool:
        print("no daily returns in pool", file=sys.stderr)
        return 1

    rng = random.Random(args.seed)
    tally = {"target": 0, "stop": 0, "neither": 0}
    finals: list[float] = []
    for _ in range(args.draws):
        outcome, final = sim_one_path(rng, pool, entry, stop, target,
                                      args.horizon_days, side)
        tally[outcome] += 1
        finals.append(final)

    total = args.draws
    t_pct = tally["target"] * 100 / total
    s_pct = tally["stop"] * 100 / total
    n_pct = tally["neither"] * 100 / total
    med = statistics.median(finals)
    v = verdict(t_pct, s_pct, n_pct)

    out = {
        "symbol": symbol, "entry": entry, "stop": stop, "target": target,
        "side": side,
        "horizon_days": args.horizon_days, "draws": total, "seed": args.seed,
        "pool_days": args.pool_days,
        "target_pct": round(t_pct, 2), "stop_pct": round(s_pct, 2),
        "neither_pct": round(n_pct, 2),
        "median_final_price": round(med, 2),
        "verdict": v,
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(TM_JSON, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)

    if args.json:
        print(json.dumps(out, indent=2))
    else:
        print(f"{symbol} {side}  entry={entry}  stop={stop}  target={target}",
              file=sys.stderr)
        print(f"  {args.draws:,} draws, {args.horizon_days}-day horizon, "
              f"pool={args.pool_days} days:", file=sys.stderr)
        print(f"  target={t_pct:5.1f}%  stop={s_pct:5.1f}%  neither={n_pct:5.1f}%",
              file=sys.stderr)
        print(f"  median final price: {med:.2f}", file=sys.stderr)
        print(f"  VERDICT: {v}", file=sys.stderr)
        print("\nfinal-price distribution:", file=sys.stderr)
        print(ascii_fan(finals), file=sys.stderr)

    if args.brief:
        verdict_eng = {
            "STRONG_TARGET": "the odds look GOOD - the target hit far more often than the stop",
            "STRONG_STOP": "the odds look BAD - the stop hit far more often than the target",
            "COIN_FLIP": "it's roughly a coin flip",
            "MANY_NEITHER": f"most runs ({n_pct:.0f}%) hit neither in {args.horizon_days} days",
            "MIXED": "the result is mixed - neither side has a clear edge",
        }.get(v, v)
        print(f"Time Machine says: replayed this {symbol} plan "
              f"{args.draws:,} times against the last {args.pool_days} days. "
              f"{t_pct:.0f}% hit the target, {s_pct:.0f}% hit the stop, "
              f"{n_pct:.0f}% hit neither. {verdict_eng.capitalize()}.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        psx_http.close()
