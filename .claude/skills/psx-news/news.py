#!/usr/bin/env python3
"""psx-news: pull PSX corporate announcements and flag watchlist items.

    news.py [--symbols SYM,...] [--since YYYY-MM-DD] [--kind K]
            [--limit N] [--offline PATH] [--json]
"""
from __future__ import annotations
import argparse
import datetime as dt
import html as html_module
import json
import os
import re
import sys
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "_lib"))
import psx_http  # noqa: E402

PKT = ZoneInfo("Asia/Karachi")

OUT_DIR = "psx"
STATE_DIR = os.path.join(OUT_DIR, "state")
NEWS_JSON = os.path.join(STATE_DIR, "news.json")
WATCHLIST_JSON = os.path.join(OUT_DIR, "watchlist.json")

ROLLING_DAYS = 30

_ROW_RE = re.compile(r"<tr>(.*?)</tr>", re.DOTALL)
_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL)
_SYM_RE = re.compile(r'href="/company/([A-Z0-9\-]+)"')
_NAME_RE = re.compile(r"<strong>([^<]+)</strong>")
_DOC_RE = re.compile(r'/download/document/(\d+)\.pdf')
_IMG_RE = re.compile(r'data-images="(\d+-\d+\.gif)"')
_REVOKED_RE = re.compile(r"tag--def[^>]*>\s*REVOKED", re.IGNORECASE)


def _strip_tags(s: str) -> str:
    return html_module.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def _classify(title: str) -> str:
    t = title.lower()
    if "material information" in t:
        return "material"
    if "dividend" in t or "payout" in t or "book closure" in t:
        return "dividend"
    if ("general meeting" in t or "board meeting" in t or "agm" in t
            or "egm" in t or "briefing" in t):
        return "meeting"
    if ("quarterly" in t or "annual" in t or "half-year" in t
            or "half year" in t or "interim financial" in t):
        return "financial_result"
    if "shariah" in t:
        return "shariah"
    return "other"


def _parse_date(date_s: str) -> str | None:
    # "Nov 16, 2026"
    try:
        d = dt.datetime.strptime(date_s.strip(), "%b %d, %Y").date()
        return d.isoformat()
    except ValueError:
        return None


def _parse_time(time_s: str) -> str | None:
    try:
        t = dt.datetime.strptime(time_s.strip().upper(), "%I:%M %p").time()
        return t.strftime("%H:%M")
    except ValueError:
        return None


def parse_announcements(html: str) -> list[dict]:
    rows = _ROW_RE.findall(html)
    out = []
    for row in rows:
        tds = _TD_RE.findall(row)
        if len(tds) < 5:
            continue
        date = _parse_date(_strip_tags(tds[0]))
        time_ = _parse_time(_strip_tags(tds[1]))
        if not date:
            continue
        sym_m = _SYM_RE.search(tds[2])
        name_m = _NAME_RE.search(tds[3])
        sym = sym_m.group(1) if sym_m else ""
        name = _strip_tags(name_m.group(1)) if name_m else _strip_tags(tds[3])
        revoked = bool(_REVOKED_RE.search(tds[4]))
        title = _strip_tags(_REVOKED_RE.sub("", tds[4]))
        last_col = tds[5] if len(tds) > 5 else ""
        doc_m = _DOC_RE.search(last_col)
        img_m = _IMG_RE.search(last_col)
        doc_id = doc_m.group(1) if doc_m else (
            img_m.group(1).split("-")[0] if img_m else "")
        out.append({
            "doc_id": doc_id,
            "date": date, "time": time_ or "",
            "symbol": sym, "name": name,
            "title": title, "kind": _classify(title),
            "revoked": revoked,
            "image_url": f"/download/image/{img_m.group(1)}" if img_m else "",
            "pdf_url": f"/download/document/{doc_m.group(1)}.pdf" if doc_m else "",
        })
    return out


def load_state() -> dict:
    if not os.path.exists(NEWS_JSON):
        return {"fetched_at": None, "seen_ids": [], "items": []}
    with open(NEWS_JSON, encoding="utf-8") as fh:
        return json.load(fh)


def save_state(state: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(NEWS_JSON, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)


def _watchlist_symbols() -> set[str]:
    if not os.path.exists(WATCHLIST_JSON):
        return set()
    with open(WATCHLIST_JSON, encoding="utf-8") as fh:
        obj = json.load(fh)
    return {row["symbol"] for row in obj.get("setups", [])}


def prune(items: list[dict], today: dt.date) -> list[dict]:
    cutoff = (today - dt.timedelta(days=ROLLING_DAYS)).isoformat()
    return [it for it in items if it["date"] >= cutoff]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="psx-news")
    p.add_argument("--symbols")
    p.add_argument("--since")
    p.add_argument("--kind",
                   choices=["material", "dividend", "meeting",
                            "financial_result", "shariah", "other", "all"],
                   default="all")
    p.add_argument("--limit", type=int)
    p.add_argument("--offline")
    p.add_argument("--json", action="store_true")
    p.add_argument("--brief", action="store_true")
    args = p.parse_args(argv)

    if args.offline:
        os.environ["PSX_FIXTURES_DIR"] = args.offline
        psx_http.FIXTURES_DIR = args.offline

    html = psx_http.announcements_companies()
    items = parse_announcements(html)

    today = dt.datetime.now(PKT).date()
    if args.since:
        try:
            cutoff = args.since
        except ValueError:
            print(f"bad --since date: {args.since}", file=sys.stderr)
            return 2
        items = [it for it in items if it["date"] >= cutoff]

    # filter by kind
    if args.kind != "all":
        items = [it for it in items if it["kind"] == args.kind]

    # filter by symbols or watchlist
    syms_filter: set[str] | None = None
    if args.symbols:
        syms_filter = {s.strip().upper() for s in args.symbols.split(",")
                       if s.strip()}
    else:
        wl = _watchlist_symbols()
        if wl:
            syms_filter = wl
    if syms_filter:
        items = [it for it in items if it["symbol"] in syms_filter]

    if args.limit:
        items = items[: args.limit]

    # merge into state
    state = load_state()
    seen = set(state.get("seen_ids", []))
    new_ids = []
    for it in items:
        if it["doc_id"] and it["doc_id"] not in seen:
            new_ids.append(it["doc_id"])

    all_items = {it["doc_id"]: it for it in state.get("items", []) if it.get("doc_id")}
    for it in items:
        if it["doc_id"]:
            all_items[it["doc_id"]] = it

    merged = sorted(all_items.values(),
                    key=lambda x: (x["date"], x["time"]), reverse=True)
    merged = prune(merged, today)

    state = {
        "fetched_at": dt.datetime.now(PKT).isoformat(timespec="seconds"),
        "seen_ids": [it["doc_id"] for it in merged if it["doc_id"]],
        "items": merged,
    }
    save_state(state)

    if args.json:
        print(json.dumps(items, indent=2))
    else:
        print(f"wrote {NEWS_JSON}: {len(items)} items "
              f"({len(new_ids)} new, {len(merged)} in rolling 30-day log)",
              file=sys.stderr)
        for it in items[:10]:
            star = " NEW" if it["doc_id"] in new_ids else ""
            rev = " [REVOKED]" if it["revoked"] else ""
            sym = it["symbol"] or "-"
            print(f"  {it['date']} {it['time']}  {sym:<10} {it['kind']:<16} "
                  f"{it['title'][:60]}{rev}{star}")

    if args.brief:
        scope = "your watchlist" if syms_filter else "PSX"
        if not items:
            print(f"News says: no announcements for {scope} in the window.")
        else:
            counts: dict[str, int] = {}
            for it in items:
                counts[it["kind"]] = counts.get(it["kind"], 0) + 1
            by_kind = ", ".join(f"{n} {k.replace('_', ' ')}"
                                for k, n in sorted(counts.items(),
                                                   key=lambda kv: -kv[1]))
            latest = items[0]
            sample = (f"{latest['symbol']}: {latest['title'][:60]}"
                      if latest.get("symbol") else latest["title"][:60])
            print(f"News says: {len(items)} announcements for {scope} "
                  f"({by_kind}). Latest - {sample}.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        psx_http.close()
