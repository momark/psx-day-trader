#!/usr/bin/env python3
"""psx-scanner: build the PSX universe, scan daily closes, scan intraday ORB.

Three sub-commands:

    scan.py universe [--index KSE100|KMI30|KMIALL] [--shariah]
    scan.py daily    [--symbols S,...] [--watchlist FILE] [--limit N]
    scan.py orb      [--at HH:MM] [--or-minutes 15] [--max-confirm 200]

Shared flags:
    --min-price 5         Skip stocks whose latest close is below this (PKR).
    --min-volume N        Skip stocks whose last-day volume is below this.
    --now "YYYY-MM-DD HH:MM"   Pin Asia/Karachi clock (for tests).
    --offline PATH        Read from this fixtures directory instead of the network.
    --json                Print the watchlist as JSON to stdout.
    --brief               Print one plain-English sentence to stdout.

Writes:
    psx/universe.txt      One symbol per line.
    psx/watchlist.json    {scope, date, setups: [{symbol, rule, ...}, ...]}
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
import re
import sys
from zoneinfo import ZoneInfo

# Pull in the shared HTTP layer.
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "_lib"))
import psx_http  # noqa: E402

PKT = ZoneInfo("Asia/Karachi")

OUT_DIR = "psx"
UNIVERSE_TXT = os.path.join(OUT_DIR, "universe.txt")
WATCHLIST_JSON = os.path.join(OUT_DIR, "watchlist.json")

OPEN_T = dt.time(9, 32)      # Mon-Thu regular open
CLOSE_T = dt.time(15, 30)    # Mon-Thu regular close
MIN_ORB_AFTER = dt.time(9, 47)  # refuse ORB before this unless --at is passed


# ---------------------------------------------------------------- shared utils

def now_pkt(override: str | None = None) -> dt.datetime:
    if override:
        # "YYYY-MM-DD HH:MM" or "HH:MM"
        if " " in override:
            d, t = override.split(" ", 1)
        else:
            d = dt.date.today().isoformat()
            t = override
        hh, mm = t.split(":")
        return dt.datetime(
            *[int(x) for x in d.split("-")],
            hour=int(hh), minute=int(mm), tzinfo=PKT)
    return dt.datetime.now(PKT)


def to_pkt_ts(unix_ts: int) -> dt.datetime:
    return dt.datetime.fromtimestamp(unix_ts, tz=PKT)


def mkdir_p(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def parse_index_symbols(html: str) -> list[str]:
    """Extract constituent symbols from an /indices/<NAME> HTML page."""
    out = []
    # Each row's symbol is marked as:  href="/company/SYM"
    for m in re.finditer(r'href="/company/([A-Z0-9]+)"', html):
        sym = m.group(1)
        if sym not in out:
            out.append(sym)
    return out


# ---------------------------------------------------------------- universe cmd

def cmd_universe(args) -> int:
    symbols = psx_http.symbols()
    # Keep Regular-Market common stocks only
    eq = [s for s in symbols if not s.get("isETF") and not s.get("isDebt")]
    keep_syms = {s["symbol"] for s in eq}

    index = args.index
    if args.shariah:
        index = index or "KMIALLSHR"
    # PSX's full Shariah-compliant universe sits at /indices/KMIALLSHR;
    # we accept the short form KMIALL as an alias.
    if index == "KMIALL":
        index = "KMIALLSHR"

    if index:
        html = psx_http.index_constituents(index)
        idx_syms = parse_index_symbols(html)
        if not idx_syms:
            print(f"warning: no constituents parsed from /indices/{index}",
                  file=sys.stderr)
        keep_syms = {s for s in idx_syms if s in keep_syms}
        scope_label = index
        print(f"  {len(keep_syms)} symbols in {index} (of {len(idx_syms)} listed)",
              file=sys.stderr)
    else:
        scope_label = "all common stocks"
        print(f"  {len(keep_syms)} common-stock symbols", file=sys.stderr)

    mkdir_p(OUT_DIR)
    with open(UNIVERSE_TXT, "w", encoding="utf-8") as fh:
        for sym in sorted(keep_syms):
            fh.write(sym + "\n")
    print(f"wrote {UNIVERSE_TXT} ({len(keep_syms)} symbols)")
    if getattr(args, "brief", False):
        print(f"Scanner says: built the universe to watch today - "
              f"{len(keep_syms)} stocks ({scope_label}).")
    return 0


def read_universe() -> list[str]:
    if not os.path.exists(UNIVERSE_TXT):
        print(f"no universe file at {UNIVERSE_TXT}; run `scan.py universe` first",
              file=sys.stderr)
        return []
    with open(UNIVERSE_TXT, encoding="utf-8") as fh:
        return [line.strip() for line in fh if line.strip()]


# ---------------------------------------------------------------- rule math

def rsi14(closes: list[float]) -> float | None:
    """Wilder RSI14 on a sequence of closes (oldest first)."""
    if len(closes) < 15:
        return None
    gains, losses = 0.0, 0.0
    for i in range(1, 15):
        d = closes[i] - closes[i - 1]
        if d >= 0:
            gains += d
        else:
            losses -= d
    avg_g = gains / 14
    avg_l = losses / 14
    for i in range(15, len(closes)):
        d = closes[i] - closes[i - 1]
        g = d if d > 0 else 0
        l = -d if d < 0 else 0
        avg_g = (avg_g * 13 + g) / 14
        avg_l = (avg_l * 13 + l) / 14
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return round(100 - 100 / (1 + rs), 2)


def sma(values: list[float], n: int) -> float | None:
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


def daily_rules(sym: str, bars_oldest_first: list[list]) -> list[dict]:
    """Apply the 5 daily rules to one symbol's EOD bars.

    `bars_oldest_first` is a list of `[unix_ts, close, volume, open]` (as served
    by the portal) sorted oldest-first.  Returns zero or more setup dicts.
    """
    if len(bars_oldest_first) < 25:
        return []
    closes = [float(b[1]) for b in bars_oldest_first]
    vols = [float(b[2]) for b in bars_oldest_first]
    opens = [float(b[3]) for b in bars_oldest_first]
    last_close = closes[-1]
    prev_close = closes[-2]
    last_vol = vols[-1]
    out = []

    # breakout_20d
    prev_20_high = max(closes[-21:-1])
    if last_close > prev_20_high:
        out.append({"symbol": sym, "rule": "breakout_20d",
                    "close": round(last_close, 2),
                    "prev_20d_high": round(prev_20_high, 2),
                    "volume": int(last_vol)})

    # bull_flag: a 2-10 day move up of >=10 %, then 3-7 day pullback of 3-8 %,
    # last bar with lower volume than the pullback's start.
    def _bull_flag(closes: list[float], vols: list[float]) -> dict | None:
        n = len(closes)
        for move_len in range(10, 1, -1):
            for pull_len in range(3, 8):
                if n < move_len + pull_len + 1:
                    continue
                move_start = closes[-(move_len + pull_len) - 1]
                move_top = closes[-pull_len - 1]
                if move_start <= 0:
                    continue
                move_pct = (move_top - move_start) / move_start
                if move_pct < 0.10:
                    continue
                pull_bottom = min(closes[-pull_len:])
                pull_pct = (move_top - pull_bottom) / move_top
                if not (0.03 <= pull_pct <= 0.08):
                    continue
                # volume on last bar must be <= pullback's start
                if vols[-1] > vols[-pull_len - 1]:
                    continue
                return {"move_len": move_len, "pull_len": pull_len,
                        "move_pct": round(move_pct * 100, 1),
                        "pull_pct": round(pull_pct * 100, 1)}
        return None

    f = _bull_flag(closes, vols)
    if f:
        out.append({"symbol": sym, "rule": "bull_flag",
                    "close": round(last_close, 2), **f})

    # reclaim_ma20
    ma20 = sma(closes, 20)
    if ma20 is not None:
        was_below = all(c < (sma(closes[:i + 1], 20) or 0)
                        for i, c in enumerate(closes[-6:-1], start=len(closes) - 6))
        if last_close > ma20 and was_below:
            out.append({"symbol": sym, "rule": "reclaim_ma20",
                        "close": round(last_close, 2),
                        "ma20": round(ma20, 2)})

    # oversold (RSI14 < 30)
    rsi = rsi14(closes)
    if rsi is not None and rsi < 30:
        out.append({"symbol": sym, "rule": "oversold",
                    "close": round(last_close, 2), "rsi14": rsi})

    # oversold_bounce
    rsi_prev = rsi14(closes[:-1])
    if (rsi_prev is not None and rsi_prev < 30
            and last_close > prev_close and last_vol > vols[-2]):
        out.append({"symbol": sym, "rule": "oversold_bounce",
                    "close": round(last_close, 2),
                    "prev_rsi14": rsi_prev,
                    "volume": int(last_vol)})

    return out


# ---------------------------------------------------------------- daily cmd

def _bars_oldest_first(sym: str) -> list[list]:
    """EOD bars oldest-first (portal serves newest-first)."""
    bars = psx_http.eod(sym)
    # portal returns newest-first; sort by timestamp ascending
    bars = sorted(bars, key=lambda b: b[0])
    return bars


def cmd_daily(args) -> int:
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    elif args.watchlist and os.path.exists(args.watchlist):
        with open(args.watchlist, encoding="utf-8") as fh:
            obj = json.load(fh)
        symbols = [row["symbol"] for row in obj.get("setups", [])]
    else:
        symbols = read_universe()

    if args.limit:
        symbols = symbols[: args.limit]
    if not symbols:
        print("no symbols to scan", file=sys.stderr)
        return 1

    setups = []
    skipped = 0
    for sym in symbols:
        try:
            bars = _bars_oldest_first(sym)
        except psx_http.PSXError as e:
            print(f"  {sym}: {e}", file=sys.stderr)
            skipped += 1
            continue
        if len(bars) < 25:
            skipped += 1
            continue
        last = bars[-1]
        if args.min_price and float(last[1]) < args.min_price:
            skipped += 1
            continue
        if args.min_volume and float(last[2]) < args.min_volume:
            skipped += 1
            continue
        setups.extend(daily_rules(sym, bars))

    today = now_pkt(args.now).date().isoformat()
    out = {"scope": "daily", "date": today, "setups": setups}

    mkdir_p(OUT_DIR)
    with open(WATCHLIST_JSON, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)
    if args.json:
        print(json.dumps(out, indent=2))
    print(f"wrote {WATCHLIST_JSON}: {len(setups)} setups "
          f"(scanned {len(symbols)}, skipped {skipped})", file=sys.stderr)
    if args.brief:
        if not setups:
            print(f"Scanner says: looked at {len(symbols)} stocks. "
                  f"Nothing interesting today.")
        else:
            # Group by rule for readability.
            by_rule: dict[str, list[str]] = {}
            for s in setups:
                by_rule.setdefault(s["rule"], []).append(s["symbol"])
            phrase = {
                "breakout_20d": "breaking out to a new 20-day high",
                "bull_flag": "forming a bull flag",
                "reclaim_ma20": "reclaiming the 20-day average",
                "oversold": "oversold",
                "oversold_bounce": "bouncing from oversold",
            }
            parts = [f"{len(syms)} {phrase.get(rule, rule)} "
                     f"({', '.join(syms[:4])}"
                     f"{f' and {len(syms)-4} more' if len(syms) > 4 else ''})"
                     for rule, syms in by_rule.items()]
            print(f"Scanner says: looked at {len(symbols)} stocks. "
                  f"Found {len(setups)} setups - " + "; ".join(parts) + ".")
    return 0


# ---------------------------------------------------------------- orb cmd

def ticks_to_5min_bars(ticks: list[list]) -> list[dict]:
    """Bucket ticks (newest-first) into 5-minute OHLCV bars on the Asia/Karachi
    clock. Bars are returned oldest-first with `{t, o, h, l, c, v}`."""
    # Sort ticks oldest-first
    ticks = sorted(ticks, key=lambda t: t[0])
    if not ticks:
        return []
    buckets: dict[dt.datetime, dict] = {}
    for ts, price, vol in ticks:
        d = to_pkt_ts(int(ts))
        # floor to 5-minute bucket
        key = d.replace(second=0, microsecond=0,
                        minute=(d.minute // 5) * 5)
        b = buckets.get(key)
        if b is None:
            buckets[key] = {"t": key, "o": price, "h": price, "l": price,
                            "c": price, "v": int(vol)}
        else:
            b["h"] = max(b["h"], price)
            b["l"] = min(b["l"], price)
            b["c"] = price
            b["v"] += int(vol)
    return sorted(buckets.values(), key=lambda b: b["t"])


def opening_range(bars: list[dict], session_open: dt.time,
                  minutes: int) -> dict | None:
    """Combine the first `minutes` of bars after `session_open` into one range.

    Pins the window to the date of the bars themselves (not `today()`), so
    offline runs over yesterday's fixtures still find the morning window.
    """
    if not bars:
        return None
    bar_date = bars[0]["t"].date()
    cutoff = dt.datetime.combine(bar_date, session_open, tzinfo=PKT)
    highs, lows, vols = [], [], 0
    last_close = None
    for b in bars:
        if b["t"].date() != bar_date:
            continue
        delta = (b["t"] - cutoff).total_seconds() / 60
        if delta < 0 or delta >= minutes:
            continue
        highs.append(b["h"])
        lows.append(b["l"])
        last_close = b["c"]
        vols += b["v"]
    if not highs:
        return None
    return {"high": max(highs), "low": min(lows),
            "close": last_close, "vol_total": vols,
            "date": bar_date.isoformat()}


def cmd_orb(args) -> int:
    clock = now_pkt(args.now)
    if clock.time() < MIN_ORB_AFTER and not args.at and not args.now:
        print(f"ORB scan refuses to run before {MIN_ORB_AFTER:%H:%M} PKT. "
              f"Pass --at or --now to override.", file=sys.stderr)
        return 1
    cutoff = args.at or f"{clock.hour:02d}:{clock.minute:02d}"
    hh, mm = cutoff.split(":")
    cut_time = dt.time(int(hh), int(mm))

    if args.watchlist and os.path.exists(args.watchlist):
        with open(args.watchlist, encoding="utf-8") as fh:
            obj = json.load(fh)
        symbols = [row["symbol"] for row in obj.get("setups", [])]
    else:
        symbols = read_universe()

    if args.limit:
        symbols = symbols[: args.limit]

    setups = []
    for sym in symbols:
        try:
            ticks = psx_http.intraday(sym)
        except psx_http.PSXError as e:
            print(f"  {sym}: {e}", file=sys.stderr)
            continue
        bars = ticks_to_5min_bars(ticks)
        if not bars:
            continue
        orange = opening_range(bars, OPEN_T, args.or_minutes)
        if orange is None:
            continue
        # look for a confirming bar after the OR window
        or_cutoff = dt.time(OPEN_T.hour,
                            min(59, OPEN_T.minute + args.or_minutes))
        bar_date = bars[0]["t"].date()
        for b in bars:
            if b["t"].date() != bar_date:
                continue
            local = b["t"].timetz().replace(tzinfo=None)
            if local <= or_cutoff or local > cut_time:
                continue
            if b["c"] > orange["high"] and b["v"] > 0:
                setups.append({"symbol": sym, "rule": "orb_breakout_up",
                               "or_high": round(orange["high"], 2),
                               "or_low": round(orange["low"], 2),
                               "confirm_time": b["t"].strftime("%H:%M"),
                               "confirm_close": round(b["c"], 2),
                               "confirm_vol": b["v"]})
                break
            if b["c"] < orange["low"] and b["v"] > 0:
                setups.append({"symbol": sym, "rule": "orb_breakout_down",
                               "or_high": round(orange["high"], 2),
                               "or_low": round(orange["low"], 2),
                               "confirm_time": b["t"].strftime("%H:%M"),
                               "confirm_close": round(b["c"], 2),
                               "confirm_vol": b["v"]})
                break
        if args.max_confirm and len(setups) >= args.max_confirm:
            break

    today = clock.date().isoformat()
    out = {"scope": "orb", "date": today,
           "or_minutes": args.or_minutes, "cutoff": cutoff,
           "setups": setups}
    mkdir_p(OUT_DIR)
    with open(WATCHLIST_JSON, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)
    if args.json:
        print(json.dumps(out, indent=2))
    print(f"wrote {WATCHLIST_JSON}: {len(setups)} setups "
          f"(scanned {len(symbols)})", file=sys.stderr)
    if args.brief:
        if not setups:
            print(f"Scanner says: watched {len(symbols)} stocks after the open. "
                  f"None broke their first {args.or_minutes}-minute range yet.")
        else:
            ups = [s for s in setups if s["rule"] == "orb_breakout_up"]
            downs = [s for s in setups if s["rule"] == "orb_breakout_down"]
            parts = []
            if ups:
                names = ", ".join(f"{s['symbol']} at {s['confirm_time']} "
                                  f"(PKR {s['confirm_close']})" for s in ups[:3])
                more = f" and {len(ups)-3} more" if len(ups) > 3 else ""
                parts.append(f"{len(ups)} broke up - {names}{more}")
            if downs:
                names = ", ".join(f"{s['symbol']} at {s['confirm_time']} "
                                  f"(PKR {s['confirm_close']})" for s in downs[:3])
                more = f" and {len(downs)-3} more" if len(downs) > 3 else ""
                parts.append(f"{len(downs)} broke down - {names}{more}")
            print(f"Scanner says: watched {len(symbols)} stocks. "
                  f"{'; '.join(parts)}.")
    return 0


# ---------------------------------------------------------------- CLI glue

def _apply_offline(path: str | None) -> None:
    if path:
        os.environ["PSX_FIXTURES_DIR"] = path
        # psx_http reads FIXTURES_DIR at module import; reload is unnecessary
        # because it checks env on every call too (see module source).
        psx_http.FIXTURES_DIR = path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="psx-scanner")
    sub = p.add_subparsers(dest="cmd", required=True)

    pu = sub.add_parser("universe")
    pu.add_argument("--index",
                    choices=["KSE100", "KMI30", "KMIALL", "KMIALLSHR"])
    pu.add_argument("--shariah", action="store_true")
    pu.add_argument("--offline")
    pu.add_argument("--brief", action="store_true")

    pd = sub.add_parser("daily")
    pd.add_argument("--symbols")
    pd.add_argument("--watchlist")
    pd.add_argument("--limit", type=int)
    pd.add_argument("--min-price", type=float, default=5.0)
    pd.add_argument("--min-volume", type=float, default=100_000)
    pd.add_argument("--json", action="store_true")
    pd.add_argument("--brief", action="store_true")
    pd.add_argument("--now")
    pd.add_argument("--offline")

    po = sub.add_parser("orb")
    po.add_argument("--watchlist", default=WATCHLIST_JSON)
    po.add_argument("--limit", type=int)
    po.add_argument("--or-minutes", type=int, default=15)
    po.add_argument("--max-confirm", type=int, default=200)
    po.add_argument("--at")
    po.add_argument("--json", action="store_true")
    po.add_argument("--brief", action="store_true")
    po.add_argument("--now")
    po.add_argument("--offline")

    args = p.parse_args(argv)
    _apply_offline(getattr(args, "offline", None))

    if args.cmd == "universe":
        return cmd_universe(args)
    if args.cmd == "daily":
        return cmd_daily(args)
    if args.cmd == "orb":
        return cmd_orb(args)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        psx_http.close()
