#!/usr/bin/env python3
"""psx-analyst: list PSX financial reports + dividends for a symbol.

    facts.py SYMBOL [--latest-only] [--kind annual|quarterly|all]
                    [--dividends] [--offline PATH] [--json]
"""
from __future__ import annotations
import argparse
import datetime as dt
import html as html_module
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "_lib"))
import psx_http  # noqa: E402

OUT_DIR = "psx"
ANALYST_DIR = os.path.join(OUT_DIR, "analyst")

_ROW_RE = re.compile(r"<tr>(.*?)</tr>", re.DOTALL)
_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL)
_A_RE = re.compile(r'<a[^>]*href="([^"]+)"[^>]*>([^<]+)</a>')


def _strip_tags(s: str) -> str:
    return html_module.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def parse_reports(html: str) -> list[dict]:
    rows = _ROW_RE.findall(html)
    out = []
    for row in rows:
        tds = _TD_RE.findall(row)
        if len(tds) < 3:
            continue
        m = _A_RE.search(tds[0])
        if not m:
            continue
        pdf_url, kind = m.group(1), _strip_tags(m.group(2))
        if kind.lower() == "reports":
            continue  # header
        period = _strip_tags(tds[1])
        posted = _strip_tags(tds[2])
        out.append({
            "kind": kind,
            "period_ended": period,
            "posted": posted,
            "pdf": pdf_url,
        })
    return out


def parse_dividends(html: str) -> list[dict]:
    rows = _ROW_RE.findall(html)
    out = []
    for row in rows:
        tds = _TD_RE.findall(row)
        if len(tds) < 3:
            continue
        cells = [_strip_tags(x) for x in tds]
        out.append({"cells": cells})
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="psx-analyst")
    p.add_argument("symbol")
    p.add_argument("--latest-only", action="store_true")
    p.add_argument("--kind", choices=["annual", "quarterly", "all"], default="all")
    p.add_argument("--dividends", action="store_true")
    p.add_argument("--offline")
    p.add_argument("--json", action="store_true")
    p.add_argument("--brief", action="store_true")
    p.add_argument("--out")
    args = p.parse_args(argv)

    if args.offline:
        os.environ["PSX_FIXTURES_DIR"] = args.offline
        psx_http.FIXTURES_DIR = args.offline

    sym = args.symbol.upper()
    html = psx_http.company_reports(sym)
    reports = parse_reports(html)

    if args.kind != "all":
        wanted = args.kind.capitalize()
        reports = [r for r in reports if r["kind"].lower() == args.kind.lower()]

    reports_sorted = sorted(
        reports,
        key=lambda r: (r["posted"], r["period_ended"]),
        reverse=True)

    out = {"symbol": sym, "reports": []}

    if reports_sorted:
        latest = reports_sorted[0]
        out["latest_report"] = latest
        try:
            posted_d = dt.date.fromisoformat(latest["posted"])
            out["latest_report_lag_days"] = (dt.date.today() - posted_d).days
        except Exception:
            out["latest_report_lag_days"] = None

    if args.latest_only and reports_sorted:
        out["reports"] = [reports_sorted[0]]
    else:
        out["reports"] = reports_sorted

    if args.dividends:
        try:
            div_html = psx_http.company_payouts(sym)
            out["dividends"] = parse_dividends(div_html)
        except psx_http.PSXError as e:
            print(f"dividends: {e}", file=sys.stderr)
            out["dividends"] = []

    out_path = args.out or os.path.join(ANALYST_DIR, f"{sym}.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)

    if args.json:
        print(json.dumps(out, indent=2))
    else:
        lr = out.get("latest_report")
        print(f"{sym}: {len(out['reports'])} reports listed", file=sys.stderr)
        if lr:
            print(f"  latest: {lr['kind']}  period {lr['period_ended']}  "
                  f"posted {lr['posted']}  lag {out.get('latest_report_lag_days')} days",
                  file=sys.stderr)

    if args.brief:
        lr = out.get("latest_report")
        if not lr:
            print(f"Analyst says: no financial reports listed for {sym} "
                  f"on PSX Data Portal.")
        else:
            lag = out.get("latest_report_lag_days")
            freshness = ""
            if lag is not None:
                if lag <= 90:
                    freshness = f" ({lag} days old - fresh)"
                elif lag <= 180:
                    freshness = f" ({lag} days old - getting stale)"
                else:
                    freshness = f" ({lag} days old - STALE, be careful)"
            pdf_url = lr["pdf"] if lr["pdf"].startswith("http") \
                else f"https://dps.psx.com.pk{lr['pdf']}"
            print(f"Analyst says: {sym} has {len(out['reports'])} reports on file. "
                  f"Latest is the {lr['kind']} for period ending "
                  f"{lr['period_ended']}, posted {lr['posted']}{freshness}. "
                  f"PDF: {pdf_url}")
            if args.dividends and out.get("dividends"):
                print(f"Analyst says: {len(out['dividends'])} dividend/payout "
                      f"events on record for {sym}.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        psx_http.close()
