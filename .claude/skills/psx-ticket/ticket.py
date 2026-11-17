#!/usr/bin/env python3
"""psx-ticket: write/track a paper ticket; no auto-submit to any broker.

    ticket.py write  [--approve yes] [--order F] [--risk F]
    ticket.py list   [--status pending|filled|cancelled|all]
    ticket.py fill   FINGERPRINT --price P --qty Q
    ticket.py cancel FINGERPRINT
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
from psx_fingerprint import fingerprint  # noqa: E402

PKT = ZoneInfo("Asia/Karachi")

OUT_DIR = "psx"
STATE_DIR = os.path.join(OUT_DIR, "state")
ORDER_JSON = os.path.join(OUT_DIR, "order.json")
RISK_JSON = os.path.join(STATE_DIR, "risk.json")
BROKER_JSON = os.path.join(STATE_DIR, "broker.json")


def _parse_yes(s: str) -> bool:
    """Strict 'yes' - not y, ok, all, or phrases that contain yes."""
    return isinstance(s, str) and s.strip().lower() == "yes"


def _load_broker() -> dict:
    if not os.path.exists(BROKER_JSON):
        return {"orders": [], "fills": [], "lots": []}
    with open(BROKER_JSON, encoding="utf-8") as fh:
        return json.load(fh)


def _save_broker(state: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(BROKER_JSON, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)


def _print_ticket(order: dict, risk: dict) -> None:
    fp = fingerprint(order)
    side = order["side"].upper()
    print("=" * 56)
    print("================= PSX PAPER TICKET =================")
    print(f"Fingerprint: {fp}")
    print(f"Symbol:      {order['symbol']:<18}  Side: {side}")
    print(f"Order type:  LIMIT               TIF:  DAY")
    print(f"Entry:       PKR {order['entry']:<14.2f}  Qty:  {order['qty']}")
    stop_lbl = ""
    tgt_lbl = f" (R/R {order.get('rr', '-')})"
    # For stop_label we need the plan; get it if order has one, else leave blank
    if "stop_label" in order:
        stop_lbl = f" ({order['stop_label']})"
    print(f"Stop:        PKR {order['stop']:<14.2f}{stop_lbl}")
    print(f"Target:      PKR {order['target']:<14.2f}{tgt_lbl}")
    print("-" * 52)
    print(f"Gross cost:  PKR {order['gross_cost']:<14,.2f}  "
          f"Net R/R: {order['net_rr']} (after costs + CGT)")
    print(f"Risk net:    PKR {order['net_risk_pkr']:<14,.2f}  "
          f"Reward net: PKR {order['net_reward_pkr']:,.2f}")
    print("-" * 52)
    attn = [c["name"] for c in risk["checks"] if c["status"] != "PASS"]
    attn_s = ", ".join(attn) if attn else "none"
    print(f"Risk verdict: {risk['verdict']}   Attention: {attn_s}")
    print("-" * 52)
    print('Type EXACTLY "yes" to record this order:')
    print("=" * 56)


def cmd_write(args) -> int:
    if not os.path.exists(args.order):
        print(f"no order at {args.order}; run psx-budget first", file=sys.stderr)
        return 2
    if not os.path.exists(args.risk):
        print(f"no risk file at {args.risk}; run psx-risk first", file=sys.stderr)
        return 2
    with open(args.order, encoding="utf-8") as fh:
        order = json.load(fh)
    with open(args.risk, encoding="utf-8") as fh:
        risk = json.load(fh)

    fp = fingerprint(order)
    if risk.get("fingerprint") != fp:
        print(f"risk fingerprint {risk.get('fingerprint')} != order fingerprint {fp}; "
              f"re-run psx-risk after the change", file=sys.stderr)
        if args.brief:
            print(f"Ticket says: refused - the risk check was run on a "
                  f"different order. Re-run the risk check and try again.")
        return 1
    if risk.get("verdict") == "FAIL":
        print(f"risk verdict is FAIL; refusing to write ticket "
              f"(fix the failing checks or decide not to trade)", file=sys.stderr)
        if args.brief:
            print(f"Ticket says: refused to print - the risk check said FAIL. "
                  f"Fix the failing checks (or decide not to trade).")
        return 1

    _print_ticket(order, risk)
    if not _parse_yes(args.approve or ""):
        print("--approve was not exactly 'yes'; nothing recorded.",
              file=sys.stderr)
        if args.brief:
            print(f"Ticket says: ticket drafted but not recorded "
                  f"(you did not type exactly 'yes' to approve). "
                  f"Enter it in your broker by hand, then run "
                  f"`ticket.py write --approve yes` to log it.")
        return 1

    state = _load_broker()
    for o in state["orders"]:
        if o["fingerprint"] == fp and o["status"] == "pending":
            print(f"duplicate fingerprint {fp} already pending; cancel it first",
                  file=sys.stderr)
            if args.brief:
                print(f"Ticket says: refused - an identical pending order "
                      f"(fingerprint {fp}) is already in the log.")
            return 1
    state["orders"].append({
        "fingerprint": fp,
        "written_at": dt.datetime.now(PKT).isoformat(timespec="seconds"),
        "symbol": order["symbol"], "side": order["side"],
        "qty": int(order["qty"]),
        "entry": float(order["entry"]), "stop": float(order["stop"]),
        "target": float(order["target"]),
        "status": "pending",
    })
    _save_broker(state)
    print(f"recorded order {fp} ({order['symbol']} {order['side']})",
          file=sys.stderr)
    if args.brief:
        side_eng = "Buy" if order["side"] == "long" else "Short"
        print(f"Ticket says: {side_eng} {order['qty']} {order['symbol']} "
              f"at PKR {order['entry']} (limit, day). Stop PKR {order['stop']}, "
              f"target PKR {order['target']}. "
              f"Order {fp} is recorded as pending - "
              f"enter it in your broker's app by hand, then run "
              f"`ticket.py fill {fp} --price X --qty Y` once it fills.")
    return 0


def cmd_list(args) -> int:
    state = _load_broker()
    orders = state.get("orders", [])
    if args.status and args.status != "all":
        orders = [o for o in orders if o["status"] == args.status]
    if not orders:
        print("no orders", file=sys.stderr)
        if args.brief:
            print("Ticket says: no orders on file.")
        return 0
    if args.json:
        print(json.dumps(orders, indent=2))
        return 0
    for o in orders:
        fill = next((f for f in state.get("fills", [])
                     if f["fingerprint"] == o["fingerprint"]), None)
        tail = (f"  filled @ {fill['price']} x {fill['qty']}"
                if fill else "")
        print(f"  {o['fingerprint']}  {o['symbol']:<10} {o['side']:<6} "
              f"qty={o['qty']}  entry={o['entry']}  status={o['status']}{tail}")
    if args.brief:
        by_status: dict[str, int] = {}
        for o in orders:
            by_status[o["status"]] = by_status.get(o["status"], 0) + 1
        parts = ", ".join(f"{n} {s}" for s, n in by_status.items())
        print(f"Ticket says: {len(orders)} order(s) on file ({parts}).")
    return 0


def cmd_fill(args) -> int:
    state = _load_broker()
    order = next((o for o in state["orders"] if o["fingerprint"] == args.fingerprint), None)
    if not order:
        print(f"no order with fingerprint {args.fingerprint}", file=sys.stderr)
        if args.brief:
            print(f"Ticket says: no order with fingerprint {args.fingerprint}. "
                  f"Check `ticket.py list`.")
        return 1
    if order["status"] != "pending":
        print(f"order {args.fingerprint} status is {order['status']}; "
              f"only pending orders can be filled", file=sys.stderr)
        if args.brief:
            print(f"Ticket says: order {args.fingerprint} is already "
                  f"{order['status']} - can't fill it again.")
        return 1
    order["status"] = "filled"
    fill = {
        "fingerprint": args.fingerprint,
        "filled_at": dt.datetime.now(PKT).isoformat(timespec="seconds"),
        "date": dt.datetime.now(PKT).date().isoformat(),
        "price": float(args.price), "qty": int(args.qty),
    }
    state.setdefault("fills", []).append(fill)
    # FIFO lots (very simple accounting; a sell closes the oldest lot)
    lots = state.setdefault("lots", [])
    if order["side"] == "long":
        lots.append({"symbol": order["symbol"], "qty_open": int(args.qty),
                     "cost": float(args.price),
                     "opened_at": fill["filled_at"]})
    else:
        # closing (short cover) - decrement oldest short lot
        for lot in lots:
            if lot["symbol"] == order["symbol"] and lot.get("short"):
                lot["qty_open"] -= int(args.qty)
                break
    _save_broker(state)
    print(f"recorded fill for {args.fingerprint} @ {args.price} x {args.qty}",
          file=sys.stderr)
    if args.brief:
        side_eng = "bought" if order["side"] == "long" else "sold short"
        print(f"Ticket says: {side_eng} {args.qty} {order['symbol']} "
              f"at PKR {args.price}. The stop and target from the plan still apply - "
              f"watch the price.")
    return 0


def cmd_cancel(args) -> int:
    state = _load_broker()
    order = next((o for o in state["orders"] if o["fingerprint"] == args.fingerprint), None)
    if not order:
        print(f"no order with fingerprint {args.fingerprint}", file=sys.stderr)
        if args.brief:
            print(f"Ticket says: no order with fingerprint {args.fingerprint}.")
        return 1
    if order["status"] != "pending":
        print(f"order {args.fingerprint} status is {order['status']}; "
              f"only pending orders can be cancelled", file=sys.stderr)
        if args.brief:
            print(f"Ticket says: order {args.fingerprint} is already "
                  f"{order['status']} - can't cancel.")
        return 1
    order["status"] = "cancelled"
    _save_broker(state)
    print(f"cancelled {args.fingerprint}", file=sys.stderr)
    if args.brief:
        print(f"Ticket says: cancelled order for {order['symbol']} "
              f"(fingerprint {args.fingerprint}).")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="psx-ticket")
    sub = p.add_subparsers(dest="cmd", required=True)

    pw = sub.add_parser("write")
    pw.add_argument("--approve")
    pw.add_argument("--order", default=ORDER_JSON)
    pw.add_argument("--risk", default=RISK_JSON)
    pw.add_argument("--brief", action="store_true")

    pl = sub.add_parser("list")
    pl.add_argument("--status",
                    choices=["pending", "filled", "cancelled", "all"],
                    default="all")
    pl.add_argument("--json", action="store_true")
    pl.add_argument("--brief", action="store_true")

    pf = sub.add_parser("fill")
    pf.add_argument("fingerprint")
    pf.add_argument("--price", type=float, required=True)
    pf.add_argument("--qty", type=int, required=True)
    pf.add_argument("--brief", action="store_true")

    pc = sub.add_parser("cancel")
    pc.add_argument("fingerprint")
    pc.add_argument("--brief", action="store_true")

    args = p.parse_args(argv)
    if args.cmd == "write":
        return cmd_write(args)
    if args.cmd == "list":
        return cmd_list(args)
    if args.cmd == "fill":
        return cmd_fill(args)
    if args.cmd == "cancel":
        return cmd_cancel(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
