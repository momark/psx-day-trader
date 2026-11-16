#!/usr/bin/env python3
"""psx-budget: size a position against four limits and apply PSX costs.

    size.py [--risk R] [--position-pct P] [--max-order M] [--cash C]
            [--config FILE] [--plan FILE] [--out FILE] [--json]
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from decimal import Decimal, ROUND_HALF_UP

OUT_DIR = "psx"
CONFIG_JSON = os.path.join(OUT_DIR, "config.json")
PLAN_JSON = os.path.join(OUT_DIR, "plan.json")
ORDER_JSON = os.path.join(OUT_DIR, "order.json")

DEFAULT_CONFIG = {
    "cash_available": 500_000,
    "max_loss_per_trade": 5_000,
    "max_position_pct": 10,
    "max_order": 100_000,
    "broker": {"rate_bps": 15, "min_pkr": 50, "max_pkr": 10_000},
    "fees_per_100k": {
        "psx_trading": 3.50, "psx_regulatory": 0.62,
        "nccpl_trade": 0.81, "nccpl_secp": 0.30,
    },
    "secp_levy_bps": 0.065,
    "cdc": {"rate_bps": 0.36, "min_pkr": 5, "max_pkr": 25_000},
    "sales_tax_pct_on_brokerage": 13,
    "cgt_pct_short_term": 15,
    "_sources": "https://www.psx.com.pk/psx/exchange/general/fees-and-charges  "
                "https://www.nccpl.com.pk/en/products-and-services/post-trade-services  "
                "FBR CGT (TY 2026): flat 15 %  "
                "Sindh sales tax (default 13 %): revisit if Punjab / KP / ICT "
                "(16 / 15 / 16)",
}


def _d(x) -> Decimal:
    return Decimal(str(x))


def _round2(x: Decimal) -> float:
    return float(x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def load_config(path: str) -> dict:
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(DEFAULT_CONFIG, fh, indent=2)
        print(f"wrote default {path}; edit it to match your account.",
              file=sys.stderr)
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _brokerage(value: Decimal, cfg: dict) -> Decimal:
    rate = _d(cfg["broker"]["rate_bps"]) / _d(10_000)
    raw = value * rate
    return min(max(raw, _d(cfg["broker"]["min_pkr"])),
               _d(cfg["broker"]["max_pkr"]))


def _psx_fixed(value: Decimal, cfg: dict) -> Decimal:
    f = cfg["fees_per_100k"]
    per = (_d(f["psx_trading"]) + _d(f["psx_regulatory"])
           + _d(f["nccpl_trade"]) + _d(f["nccpl_secp"]))
    return value * per / _d(100_000)


def _secp_levy(value: Decimal, cfg: dict) -> Decimal:
    return value * _d(cfg["secp_levy_bps"]) / _d(10_000)


def _cdc(value: Decimal, cfg: dict) -> Decimal:
    rate = _d(cfg["cdc"]["rate_bps"]) / _d(10_000)
    raw = value * rate
    return min(max(raw, _d(cfg["cdc"]["min_pkr"])),
               _d(cfg["cdc"]["max_pkr"]))


def _sales_tax_on_brokerage(brokerage: Decimal, psx_fixed: Decimal, cfg: dict) -> Decimal:
    return (brokerage + psx_fixed) * _d(cfg["sales_tax_pct_on_brokerage"]) / _d(100)


def leg_costs(value: Decimal, cfg: dict) -> dict:
    """Returns the cost breakdown for one leg (buy or sell) at `value` PKR."""
    b = _brokerage(value, cfg)
    pf = _psx_fixed(value, cfg)
    st = _sales_tax_on_brokerage(b, pf, cfg)
    sl = _secp_levy(value, cfg)
    cdc = _cdc(value, cfg)
    return {"brokerage": b, "psx_fees": pf, "sales_tax": st,
            "secp_levy": sl, "cdc": cdc,
            "total": b + pf + st + sl + cdc}


def size_position(plan: dict, cfg: dict) -> tuple[int, str]:
    entry = _d(plan["entry"])
    stop = _d(plan["stop"])
    risk_per_share = abs(entry - stop)
    if risk_per_share == 0:
        return 0, "zero_risk"

    cash = _d(cfg["cash_available"])
    max_loss = _d(cfg["max_loss_per_trade"])
    pct = _d(cfg["max_position_pct"])
    max_order = _d(cfg["max_order"])

    q_loss = int(max_loss // risk_per_share)
    q_pct = int(((cash * pct / _d(100)) // entry) if entry > 0 else 0)
    q_order = int(max_order // entry)
    q_cash = int(cash // entry)

    candidates = {"max_loss_per_trade": q_loss, "max_position_pct": q_pct,
                  "max_order": q_order, "cash_available": q_cash}
    label = min(candidates, key=lambda k: candidates[k])
    return max(0, candidates[label]), label


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="psx-budget")
    p.add_argument("--risk", type=float)
    p.add_argument("--position-pct", type=float)
    p.add_argument("--max-order", type=float)
    p.add_argument("--cash", type=float)
    p.add_argument("--config", default=CONFIG_JSON)
    p.add_argument("--plan", default=PLAN_JSON)
    p.add_argument("--out", default=ORDER_JSON)
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    cfg = load_config(args.config)
    if args.risk is not None:
        cfg["max_loss_per_trade"] = args.risk
    if args.position_pct is not None:
        cfg["max_position_pct"] = args.position_pct
    if args.max_order is not None:
        cfg["max_order"] = args.max_order
    if args.cash is not None:
        cfg["cash_available"] = args.cash

    if not os.path.exists(args.plan):
        print(f"no plan at {args.plan}; run psx-exit first", file=sys.stderr)
        return 2
    with open(args.plan, encoding="utf-8") as fh:
        plan = json.load(fh)

    qty, limit = size_position(plan, cfg)
    if qty <= 0:
        print(f"size is 0 shares (limit: {limit}); nothing to size", file=sys.stderr)
        return 1

    entry = _d(plan["entry"])
    stop = _d(plan["stop"])
    target = _d(plan["target"])
    side = plan["side"]
    q = _d(qty)

    gross_buy = entry * q
    gross_sell_target = target * q
    gross_sell_stop = stop * q

    buy = leg_costs(gross_buy, cfg)
    sell_at_target = leg_costs(gross_sell_target, cfg)
    sell_at_stop = leg_costs(gross_sell_stop, cfg)

    if side == "long":
        gain_at_target = gross_sell_target - gross_buy
        loss_at_stop = gross_buy - gross_sell_stop
    else:
        gain_at_target = gross_buy - gross_sell_target
        loss_at_stop = gross_sell_stop - gross_buy
    cgt = max(_d(0), gain_at_target * _d(cfg["cgt_pct_short_term"]) / _d(100))

    round_trip_fixed = (buy["total"] + sell_at_target["total"])
    net_reward = gain_at_target - round_trip_fixed - cgt
    net_risk = loss_at_stop + (buy["total"] + sell_at_stop["total"])
    net_rr = (float(net_reward / net_risk) if net_risk > 0 else 0.0)

    order = {
        "symbol": plan["symbol"], "side": side,
        "qty": qty,
        "entry": float(entry), "stop": float(stop), "target": float(target),
        "gross_cost": _round2(gross_buy),
        "gross_proceeds_at_target": _round2(gross_sell_target),
        "costs": {
            "buy_brokerage": _round2(buy["brokerage"]),
            "buy_psx_fees": _round2(buy["psx_fees"]),
            "buy_sales_tax": _round2(buy["sales_tax"]),
            "buy_secp_levy": _round2(buy["secp_levy"]),
            "buy_cdc": _round2(buy["cdc"]),
            "sell_brokerage": _round2(sell_at_target["brokerage"]),
            "sell_psx_fees": _round2(sell_at_target["psx_fees"]),
            "sell_sales_tax": _round2(sell_at_target["sales_tax"]),
            "sell_secp_levy": _round2(sell_at_target["secp_levy"]),
            "sell_cdc": _round2(sell_at_target["cdc"]),
            "round_trip_fixed": _round2(round_trip_fixed),
            "cgt_on_gain": _round2(cgt),
        },
        "net_risk_pkr": _round2(net_risk),
        "net_reward_pkr": _round2(net_reward),
        "net_rr": round(net_rr, 2),
        "limit_hit": limit,
    }

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(order, fh, indent=2)
    if args.json:
        print(json.dumps(order, indent=2))
    else:
        print(f"wrote {args.out}: {order['symbol']} {side} "
              f"qty={qty} ({limit})  net R/R={order['net_rr']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
