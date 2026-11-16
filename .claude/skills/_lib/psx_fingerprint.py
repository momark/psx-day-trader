"""Shared order fingerprint used by psx-risk and psx-ticket.

The fingerprint is the same 16-hex-char tag that `psx-risk check` emits and
`psx-ticket` later matches on - the user can trust that if the tag survives
from the risk check to the ticket, nothing in the order changed in between.

Fields hashed (in order): symbol, side, qty, entry, stop, target.  Prices
are rounded to 2 decimals before hashing so a trailing-zero difference does
not shift the tag.
"""
from __future__ import annotations
import hashlib


def fingerprint(order: dict) -> str:
    pieces = [
        str(order["symbol"]).upper(),
        str(order["side"]).lower(),
        str(int(order["qty"])),
        f"{float(order['entry']):.2f}",
        f"{float(order['stop']):.2f}",
        f"{float(order['target']):.2f}",
    ]
    h = hashlib.sha256("|".join(pieces).encode("utf-8")).hexdigest()
    return h[:16]
