"""PSX Data Portal HTTP layer, shared by every skill that needs network data.

The dps.psx.com.pk portal serves its HTML pages with an inline
    window.__ps = {"lc":"en","tz":"Asia/Karachi","_k":"<KEY>","rv":"1"};
The portal's own JS reads that `_k` and echoes it as the `x-req-id` header on
every XHR, the same way a Rails/Django app does with a CSRF meta tag. We mirror
that: load one HTML page first to pick up `_k`, then issue our follow-up
requests with the same header.

We use Chromium (via Playwright) so the TLS stack, cookies and UA match a
normal browser visit. The UA is a plain Chrome string; the project discloses
itself through README and Referer, not through the UA.

Public API
----------
    psx_http.symbols()                       -> list[dict]   # full universe
    psx_http.intraday(symbol)                -> list[(ts, price, vol)]
    psx_http.eod(symbol)                     -> list[(ts, close, vol, open)]
    psx_http.historical(date_iso)            -> str          # HTML page
    psx_http.company_payouts(symbol)         -> str          # HTML snippet
    psx_http.company_reports(symbol)         -> str          # HTML snippet
    psx_http.index_constituents(idx)         -> str          # HTML page
    psx_http.announcements_page()            -> str          # HTML page
    psx_http.announcements_companies()       -> str          # HTML page
    psx_http.eligible_scrips_xml()           -> bytes        # XML
    psx_http.close()                         -> None         # tear down browser

Fixture mode (for tests and offline runs)
-----------------------------------------
    export PSX_FIXTURES_DIR=/path/to/tests/fixtures/raw
Every call then reads the matching file from that directory and the browser
never starts. Fixture names match the shapes written by tests/scripts/
capture_fixtures.py.

Rate limit
----------
Minimum 500 ms between non-navigation calls when running live. Override with
`PSX_PAUSE_MS`.

Errors
------
Raises `psx_http.PSXError` on anything non-200 or a network failure. Nothing
is retried; the caller decides.
"""
from __future__ import annotations
import atexit
import json
import os
import re
import sys
import time

HOST = "https://dps.psx.com.pk"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

PAUSE_SEC = float(os.environ.get("PSX_PAUSE_MS", "500")) / 1000.0
FIXTURES_DIR = os.environ.get("PSX_FIXTURES_DIR")  # if set, read from disk

_PS_RE = re.compile(r'window\.__ps\s*=\s*(\{[^}]+\})')


class PSXError(RuntimeError):
    pass


# ---------------------------------------------------------------- fixture mode

def _fixture_path(name: str) -> str:
    if not FIXTURES_DIR:
        raise PSXError("PSX_FIXTURES_DIR not set")
    return os.path.join(FIXTURES_DIR, name)


def _fixture_bytes(name: str) -> bytes:
    p = _fixture_path(name)
    if not os.path.exists(p):
        raise PSXError(f"fixture not found: {p}")
    with open(p, "rb") as fh:
        return fh.read()


def _fixture_text(name: str) -> str:
    return _fixture_bytes(name).decode("utf-8", errors="replace")


def _fixture_json(name: str):
    return json.loads(_fixture_text(name))


# ------------------------------------------------------------ browser lifecycle

_browser = None
_ctx = None
_page = None
_pw = None
_session_key: str | None = None
_last_call_ts: float = 0.0


def _init_browser():
    """Lazily start Chromium and warm up the session key."""
    global _browser, _ctx, _page, _pw, _session_key
    if _browser is not None:
        return
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise PSXError(
            "playwright not installed. Run:\n"
            "  python -m pip install playwright\n"
            "  python -m playwright install chromium"
        )
    _pw = sync_playwright().start()
    _browser = _pw.chromium.launch(headless=True)
    _ctx = _browser.new_context(
        user_agent=UA, locale="en-US",
        extra_http_headers={"Accept-Language": "en-US,en;q=0.9"})
    _page = _ctx.new_page()
    _page.goto(HOST + "/company/OGDC", timeout=30000)
    try:
        _page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass
    _session_key = _read_session_key(_page.content())
    if not _session_key:
        raise PSXError("could not read window.__ps._k from warmup page")
    atexit.register(close)


def _read_session_key(html: str) -> str | None:
    m = _PS_RE.search(html)
    if not m:
        return None
    try:
        obj = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    return obj.get("_k")


def _refresh_key_from_page():
    global _session_key
    k = _read_session_key(_page.content())
    if k:
        _session_key = k


def _pause():
    global _last_call_ts
    now = time.time()
    delta = now - _last_call_ts
    if delta < PAUSE_SEC:
        time.sleep(PAUSE_SEC - delta)
    _last_call_ts = time.time()


def close():
    global _browser, _ctx, _page, _pw
    if _browser is None:
        return
    try:
        _browser.close()
    except Exception:
        pass
    try:
        _pw.stop()
    except Exception:
        pass
    _browser = _ctx = _page = _pw = None


# ------------------------------------------------------------ request primitives

def _ctx_get_bytes(path: str, referer: str = HOST + "/") -> bytes:
    _init_browser()
    _pause()
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "X-Req-Id": _session_key,
        "Referer": referer,
    }
    resp = _ctx.request.get(HOST + path, headers=headers)
    if resp.status != 200:
        raise PSXError(f"GET {path} returned {resp.status}")
    return resp.body()


def _ctx_post_bytes(path: str, form: dict, referer: str) -> bytes:
    _init_browser()
    _pause()
    headers = {
        "Accept": "text/html, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "X-Req-Id": _session_key,
        "Referer": referer,
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    }
    resp = _ctx.request.post(HOST + path, headers=headers, form=form)
    if resp.status != 200:
        raise PSXError(f"POST {path} returned {resp.status}")
    return resp.body()


def _goto_and_bytes(path: str) -> bytes:
    """Navigate the page itself (needed for pages whose HTML the SPA builds)."""
    _init_browser()
    _pause()
    try:
        _page.goto(HOST + path, timeout=30000)
        _page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass
    _refresh_key_from_page()
    return _page.content().encode("utf-8")


# ---------------------------------------------------------------- public helpers

def symbols() -> list[dict]:
    """Return the full universe:  [{symbol, name, sectorName, isETF, isDebt}, ...]."""
    if FIXTURES_DIR:
        return _fixture_json("symbols.json")
    data = _ctx_get_bytes("/symbols")
    return json.loads(data.decode("utf-8"))


def intraday(symbol: str) -> list[list]:
    """Return today's intraday ticks for `symbol`.

    Each entry is `[unix_ts, price, volume]`.  Newest tick first.
    """
    if FIXTURES_DIR:
        obj = _fixture_json(f"ts-int-{symbol}.json")
    else:
        data = _ctx_get_bytes(f"/timeseries/int/{symbol}",
                              referer=HOST + f"/company/{symbol}")
        obj = json.loads(data.decode("utf-8"))
    if obj.get("status") != 1:
        raise PSXError(f"intraday({symbol}) returned status {obj.get('status')}")
    return obj.get("data", [])


def eod(symbol: str) -> list[list]:
    """Return ~5 years of end-of-day bars for `symbol`.

    Each entry is `[unix_ts, close, volume, open]`.  High and low are NOT
    served by this endpoint; use `historical(date)` and parse per-date rows
    when you need full OHLCV.
    """
    if FIXTURES_DIR:
        obj = _fixture_json(f"ts-eod-{symbol}.json")
    else:
        data = _ctx_get_bytes(f"/timeseries/eod/{symbol}",
                              referer=HOST + f"/company/{symbol}")
        obj = json.loads(data.decode("utf-8"))
    if obj.get("status") != 1:
        raise PSXError(f"eod({symbol}) returned status {obj.get('status')}")
    return obj.get("data", [])


def historical(date_iso: str) -> str:
    """Return the full-market historical table HTML for `date_iso` (YYYY-MM-DD).

    For a weekend or holiday the page returns a short (~903 B) "no data" snippet.
    """
    if FIXTURES_DIR:
        return _fixture_text(f"historical-{date_iso}.html")
    data = _ctx_post_bytes("/historical", {"date": date_iso},
                           referer=HOST + "/historical")
    return data.decode("utf-8")


def company_payouts(symbol: str) -> str:
    if FIXTURES_DIR:
        return _fixture_text(f"company-payouts-{symbol}.html")
    data = _ctx_post_bytes("/company/payouts", {"symbol": symbol},
                           referer=HOST + f"/company/{symbol}")
    return data.decode("utf-8")


def company_reports(symbol: str) -> str:
    if FIXTURES_DIR:
        return _fixture_text(f"company-reports-{symbol}.html")
    data = _ctx_get_bytes(f"/company/reports/{symbol}",
                          referer=HOST + f"/company/{symbol}")
    return data.decode("utf-8")


def index_constituents(index_name: str) -> str:
    """Return the full HTML page for one of KSE100, KMI30, KMIALL."""
    name = index_name.upper()
    if FIXTURES_DIR:
        return _fixture_text(f"indices-{name}.html")
    return _goto_and_bytes(f"/indices/{name}").decode("utf-8")


def announcements_page() -> str:
    if FIXTURES_DIR:
        return _fixture_text("announcements-page.html")
    return _goto_and_bytes("/announcements").decode("utf-8")


def announcements_companies() -> str:
    if FIXTURES_DIR:
        return _fixture_text("announcements-companies.html")
    return _goto_and_bytes("/announcements/companies").decode("utf-8")


def eligible_scrips_xml() -> bytes:
    """Authoritative XML export of the regular-market universe."""
    if FIXTURES_DIR:
        return _fixture_bytes("eligible-scrips-REG.xml")
    return _ctx_get_bytes("/data/eligible-scrips/REG.xml")


# --------------------------------------------------------------- CLI smoke test
if __name__ == "__main__":
    # `python psx_http.py smoke` - prints a one-line summary of each call.
    if len(sys.argv) >= 2 and sys.argv[1] == "smoke":
        try:
            s = symbols()
            print(f"symbols: {len(s)} entries")
            t = intraday("OGDC")
            print(f"intraday(OGDC): {len(t)} ticks, latest ts={t[0][0] if t else 'none'}")
            e = eod("OGDC")
            print(f"eod(OGDC): {len(e)} bars")
            h = historical("2026-11-13")
            print(f"historical(2026-11-13): {len(h)} chars")
        finally:
            close()
