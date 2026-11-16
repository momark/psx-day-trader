#!/usr/bin/env python3
"""Capture one trading day of PSX Data Portal responses into tests/fixtures/raw/.

Every HTML page served by dps.psx.com.pk carries an inline

    window.__ps = {"lc":"en","tz":"Asia/Karachi","_k":"<key>","rv":"1"};

The site's own JavaScript reads `_k` and echoes it on every XHR as the
`x-req-id` header - the same shape that Rails/Django/Laravel use with a
`<meta name="csrf-token">`.  We do the same: load any page once to pick up
`_k`, then issue our follow-up JSON / POST requests with that same header.

We drive the browser through Chromium (via Playwright) so the TLS stack,
cookies and UA all match a normal browser visit.  Nothing in this script
runs at skill runtime; it is a one-shot fixture generator.
"""
from __future__ import annotations
import datetime as dt
import json
import os
import re
import sys
import time

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    print("Install Playwright first:  python -m pip install playwright", file=sys.stderr)
    print("                           python -m playwright install chromium", file=sys.stderr)
    sys.exit(2)

HOST = "https://dps.psx.com.pk"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
PAUSE_SEC = 0.5

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.abspath(os.path.join(HERE, "..", "fixtures", "raw"))
os.makedirs(FIX, exist_ok=True)

_PS_RE = re.compile(r'window\.__ps\s*=\s*(\{[^}]+\})')


def read_session_key(page) -> str | None:
    """Pull the `_k` session key from `window.__ps` on the currently loaded page."""
    html = page.content()
    m = _PS_RE.search(html)
    if not m:
        return None
    try:
        obj = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    return obj.get("_k")


def save(name: str, body: bytes) -> str:
    path = os.path.join(FIX, name)
    with open(path, "wb") as fh:
        fh.write(body)
    return path


def manifest_entry(name, url_or_path, method, form, status, ct, body, elapsed_ms):
    return {
        "name": name, "path": url_or_path, "method": method, "form": form,
        "status": status, "content_type": ct, "len": len(body), "ms": elapsed_ms,
        "file": os.path.join(FIX, name),
        "head": body[:300].decode("utf-8", errors="replace"),
    }


class Capturer:
    """Record responses the page itself makes so we can save them to disk."""

    def __init__(self, page):
        self.page = page
        self._buf: dict[str, tuple[int, str, bytes, float]] = {}
        page.on("response", self._on_response)

    def _on_response(self, resp):
        u = resp.url
        if "dps.psx.com.pk" not in u:
            return
        try:
            body = resp.body()
        except Exception:
            return
        ct = resp.headers.get("content-type", "") or ""
        self._buf[u] = (resp.status, ct, body, time.time())

    def pop(self, url_substring: str):
        hits = [(u, v) for u, v in self._buf.items() if url_substring in u]
        if not hits:
            return None
        hits.sort(key=lambda kv: kv[1][3], reverse=True)
        _u, (st, ct, body, _ts) = hits[0]
        return (st, ct, body)

    def clear(self) -> None:
        self._buf.clear()


def ctx_fetch(request_ctx, path: str, method: str, form: dict | None,
              session_key: str, referer: str):
    """Fetch via the browser context's own request stack (same TLS + cookies).

    Sends the session key the server gave us (`window.__ps._k`) as the `x-req-id`
    header, which is what the site's own XHR layer does.
    """
    headers = {
        "Accept": ("application/json, text/javascript, */*; q=0.01"
                   if method == "GET" else "text/html, */*; q=0.01"),
        "X-Requested-With": "XMLHttpRequest",
        "X-Req-Id": session_key,
        "Referer": referer,
    }
    url = HOST + path
    if method == "POST":
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        resp = request_ctx.post(url, headers=headers, form=(form or {}))
    else:
        resp = request_ctx.get(url, headers=headers)
    body = resp.body()
    ct = resp.headers.get("content-type", "") or ""
    return (resp.status, ct, body)


def main() -> int:
    manifest: list[dict] = []
    symbols = ["OGDC", "HUBC", "LUCK", "ENGROH"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=UA, locale="en-US",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"})
        page = ctx.new_page()
        cap = Capturer(page)
        _session_key = [None]  # mutable container

        def goto(path: str) -> None:
            t0 = time.time()
            page.goto(HOST + path, timeout=30000)
            try:
                page.wait_for_load_state("networkidle", timeout=20000)
            except PWTimeout:
                pass
            dt_ms = int((time.time() - t0) * 1000)
            print(f"  goto {path}  ({dt_ms} ms)")

        def refresh_key() -> None:
            k = read_session_key(page)
            if k:
                _session_key[0] = k

        def capture_as(name: str, url_frag: str, path: str, method: str,
                       form: dict | None) -> None:
            res = cap.pop(url_frag)
            if res is None:
                print(f"  MISS  {name}  ({url_frag})", file=sys.stderr)
                return
            st, ct, body = res
            save(name, body)
            manifest.append(manifest_entry(name, path, method, form, st, ct, body, 0))
            print(f"  {st:>3}  {name}  ({len(body)} B, {ct})")

        def ctx_fetch_and_save(name, path, method="GET", form=None,
                               referer=HOST + "/"):
            if _session_key[0] is None:
                print(f"  ERR   {name}  no session key yet", file=sys.stderr)
                return
            t0 = time.time()
            try:
                st, ct, body = ctx_fetch(ctx.request, path, method, form,
                                         _session_key[0], referer)
            except Exception as e:
                print(f"  ERR   {name}  {e}", file=sys.stderr)
                return
            save(name, body)
            ms = int((time.time() - t0) * 1000)
            manifest.append(manifest_entry(name, path, method, form, st, ct, body, ms))
            print(f"  {st:>3}  {name}  ({len(body)} B, {ct}, {ms} ms)")
            time.sleep(PAUSE_SEC)

        # ---- 1) warmup: load /company/OGDC to pick up the session key
        print("[1/8] warmup")
        goto("/company/OGDC")
        refresh_key()
        if _session_key[0]:
            print(f"  session key: {_session_key[0][:12]}...")
        else:
            print("  no session key found", file=sys.stderr)

        capture_as("symbols.json", "/symbols", "/symbols", "GET", None)

        # ---- 2) eligible scrips XML
        print("[2/8] eligible-scrips XML")
        ctx_fetch_and_save("eligible-scrips-REG.xml", "/data/eligible-scrips/REG.xml")

        # ---- 3) per-symbol company pages + their XHRs
        print("[3/8] per-symbol company pages + intraday ticks")
        for s in symbols:
            cap.clear()
            goto(f"/company/{s}")
            refresh_key()

            page_body = page.content().encode("utf-8")
            save(f"company-{s}.html", page_body)
            manifest.append(manifest_entry(
                f"company-{s}.html", f"/company/{s}", "GET", None,
                200, "text/html; charset=utf-8", page_body, 0))
            print(f"  200  company-{s}.html  ({len(page_body)} B, text/html)")

            capture_as(f"ts-int-{s}.json", f"/timeseries/int/{s}",
                       f"/timeseries/int/{s}", "GET", None)
            capture_as(f"company-reports-{s}.html", f"/company/reports/{s}",
                       f"/company/reports/{s}", "GET", None)
            capture_as(f"company-payouts-{s}.html", "/company/payouts",
                       "/company/payouts", "POST", {"symbol": s})

        # ---- 4) per-symbol EOD timeseries
        print("[4/8] per-symbol EOD timeseries")
        last_sym_referer = HOST + f"/company/{symbols[-1]}"
        for s in symbols:
            ctx_fetch_and_save(f"ts-eod-{s}.json", f"/timeseries/eod/{s}",
                               referer=last_sym_referer)

        # ---- 5) historical table for the last ~25 trading days
        print("[5/8] historical table (last ~25 trading dates)")
        cap.clear()
        goto("/historical")
        refresh_key()

        today = dt.date.today()
        want = 25
        collected = 0
        d = today
        while collected < want and (today - d).days < 50:
            if d.weekday() < 5:
                ds = d.isoformat()
                ctx_fetch_and_save(f"historical-{ds}.html", "/historical",
                                   method="POST", form={"date": ds},
                                   referer=HOST + "/historical")
                collected += 1
            d = d - dt.timedelta(days=1)

        # ---- 6) announcements pages
        print("[6/8] announcements pages")
        cap.clear()
        goto("/announcements")
        page_body = page.content().encode("utf-8")
        save("announcements-page.html", page_body)
        manifest.append(manifest_entry(
            "announcements-page.html", "/announcements", "GET", None,
            200, "text/html; charset=utf-8", page_body, 0))
        print(f"  200  announcements-page.html  ({len(page_body)} B)")

        cap.clear()
        goto("/announcements/companies")
        page_body = page.content().encode("utf-8")
        save("announcements-companies.html", page_body)
        manifest.append(manifest_entry(
            "announcements-companies.html", "/announcements/companies", "GET", None,
            200, "text/html; charset=utf-8", page_body, 0))
        print(f"  200  announcements-companies.html  ({len(page_body)} B)")

        # ---- 7) index constituents (HTML)
        print("[7/8] index constituents")
        for idx in ("KSE100", "KMI30", "KMIALLSHR"):
            cap.clear()
            try:
                page.goto(HOST + f"/indices/{idx}", timeout=25000)
                page.wait_for_load_state("networkidle", timeout=15000)
            except PWTimeout:
                pass
            page_body = page.content().encode("utf-8")
            if b"Page Not Found" in page_body or b"404" in page_body[:1024]:
                st = 404
            else:
                st = 200
            save(f"indices-{idx}.html", page_body)
            manifest.append(manifest_entry(
                f"indices-{idx}.html", f"/indices/{idx}", "GET", None,
                st, "text/html; charset=utf-8", page_body, 0))
            print(f"  {st}  indices-{idx}.html  ({len(page_body)} B)")

        # ---- 8) indices overview
        print("[8/8] indices overview")
        cap.clear()
        goto("/indices")
        page_body = page.content().encode("utf-8")
        save("indices.html", page_body)
        manifest.append(manifest_entry(
            "indices.html", "/indices", "GET", None,
            200, "text/html; charset=utf-8", page_body, 0))
        print(f"  200  indices.html  ({len(page_body)} B)")

        browser.close()

    man_path = os.path.join(FIX, "_manifest.json")
    with open(man_path, "w", encoding="utf-8") as fh:
        json.dump({"captured_at_utc": dt.datetime.now(dt.UTC).isoformat(),
                   "host": HOST, "ua": UA, "items": manifest}, fh, indent=2)

    ok = sum(1 for r in manifest if r["status"] == 200 and r["len"] > 0)
    bad = [r for r in manifest if not (r["status"] == 200 and r["len"] > 0)]
    print(f"\nOK {ok}/{len(manifest)}; failed/empty {len(bad)}")
    for r in bad:
        print(f"  FAIL {r['status']:>3}  {r['method']:<4} {r['path']}  "
              f"ct={r['content_type']!r}  len={r['len']}")
    print(f"manifest: {man_path}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
