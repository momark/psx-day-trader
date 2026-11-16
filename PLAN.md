# psx-day-trader — Build Plan

A PSX (Pakistan Stock Exchange) adaptation of
[day-trader-skill](https://github.com/jakeschincariol/day-trader-skill),
with PSX data, PSX rules, and PKR costs. Pure standard-library Python 3.8+,
no `pip install`, no API keys. Paper trading by default. Not financial advice.

Status: **plan — not yet implemented. Waiting for user OK.**

---

## 1. The pack

**Name:** `psx-day-trader`  ·  **10 skills** (reference had 11; `trader-pelosi` is dropped;
`trader-broker` becomes `psx-ticket`).

| # | Skill | What changes from the US version |
|---|---|---|
| 1 | `psx-scanner`     | Universe from PSX `/data/eligible-scrips/REG.xml` (567 names today) with KSE-100, KMI-30 and KMI-ALL filters. Daily setups from `POST /historical` (one request per date, full-market OHLC). ORB scan iterates a watchlist, pulls `/timeseries/int/SYM` ticks, aggregates into 5-min candles in Python, then first close > opening-range high. |
| 2 | `psx-news`        | Scrapes `dps.psx.com.pk/announcements` filtered by watchlist symbols + today's date. Tags announcements by type (Financial Results, Dividend, Board Meeting, Material Info). Flags `MOVING` when `/timeseries/int` last tick is ≥ 3% from LDCP. No SEC/EDGAR. |
| 3 | `psx-entry`       | Same ORB rule, on PSX 5-min bars aggregated from ticks. Opening range = first 15 min after the real session-open (09:32 Mon–Thu, 09:17 Fri, 09:17 Ramadan). Asia/Karachi clock throughout. |
| 4 | `psx-exit`        | Same stop/target math, respects the PSX scrip circuit breaker (± 8% or Re 1) as a hard sanity bound. Flat-by defaults to session-end − 5 min (15:25 Mon–Thu, 11:55/16:25 Fri, 13:25 Ramadan). PKR 0.01 rounding. |
| 5 | `psx-timemachine` | Monte Carlo on PSX 5-min bars built from the last ~60 PSX sessions. Pool from ticks for recent weeks + `POST /historical` per date for older sessions. Same seed/replay math. Session length adapts per day (Mon–Thu 73 bars; Friday split; Ramadan). |
| 6 | `psx-budget`      | Sizes in PKR. Reads `costs.json` (next section) and prints an all-in cost line on every plan: commission, PSX/NCCPL/SECP/CDC fees, provincial sales tax on brokerage, and estimated CGT on target gain. No fractional shares (PSX lot = 1). |
| 7 | `psx-risk`        | Same gate pattern. Adds PSX checks: whole-share qty; limit price on the Rs 0.01 tick grid; entry within today's ± 8% CB band. Settlement label **T+1 (effective 2026-02-09)**. |
| 8 | `psx-ticket`      | **No broker API.** Writes a plain-English order ticket (BUY/SELL, symbol, qty, limit, stop, target, max loss PKR) you copy into your broker's app. `ticket.py fill` records what actually filled, updates `trader/portfolio.json`. Paper-trading log by default (`paper: true`). `flatten` builds the sell-to-close ticket by the flat-by time. |
| 9 | `psx-alerts`      | Watches `/timeseries/int` last-tick vs. plan stop/target + flat-by. Clock knows Mon-Thu, Friday split, Ramadan, and 2026 holidays. |
| 10 | `psx-analyst`    | Replaces SEC/XBRL. Reads `dps.psx.com.pk/company/reports/SYM` for the quarterly/annual PDF list and `psx.com.pk/psx/announcement/financial-announcements` for recent EPS, profit-before-tax, dividend rows. Tells you plainly: last quarter EPS, YoY growth, next expected report. No valuation calls. |

Dropped: `trader-pelosi` (no PSX equivalent).

---

## 2. Files I'll create

```
psx-day-trader/
├── .claude-plugin\
│   ├── plugin.json           (name "psx-day-trader", version 0.1.0)
│   └── marketplace.json
├── .claude\skills\
│   ├── psx-scanner\     SKILL.md, scan.py,
│   │                    kse100.seed.json, kmi30.seed.json, kmi_all.seed.json,
│   │                    universe.seed.json
│   ├── psx-news\        SKILL.md, news.py
│   ├── psx-entry\       SKILL.md, entry.py
│   ├── psx-exit\        SKILL.md, exit.py
│   ├── psx-timemachine\ SKILL.md, timemachine.py
│   ├── psx-budget\      SKILL.md, size.py, config.example.json, costs.example.json
│   ├── psx-risk\        SKILL.md, check.py
│   ├── psx-ticket\      SKILL.md, ticket.py, manual_log.example.json
│   ├── psx-alerts\      SKILL.md, alerts.py, alerts.example.json
│   └── psx-analyst\     SKILL.md, financials.py
├── tests\
│   ├── _load.py
│   ├── fixtures\        one PSX trading day of real data: OGDC, HUBC, LUCK, ENGROH + KSE-100
│   └── test_*.py        10 test files, offline
├── README.md            setup + real-world how-to + FAQ
├── PLAN.md              this file
└── reference\           cloned day-trader-skill, kept for diff (added to .gitignore)
```

Each skill is **drop-in independent** — helpers (clock, HTTP, candle aggregation)
are copied into each `.py` file, not shared via a module. This mirrors the reference
pack's convention: you can copy one folder and it works alone.

---

## 3. Shared design decisions

### HTTP layer
- Every call carries:
  - `X-Requested-With: XMLHttpRequest`   (required — without it dps.psx.com.pk returns 404 HTML)
  - `User-Agent: psx-day-trader/0.1 (+contact)`
  - `Accept: application/json, text/html, */*`
- 300 ms pause between requests, one retry on 429/5xx after 5 s.
- **Pluggable source:** every scrape/fetch function returns a documented shape,
  so a second source (e.g., sarmaaya.pk) can be plugged in later as a fallback.

### Ticks → candles
`bars(ticks, minutes=5)` groups ticks by `floor(ts / 300)`; `o = first tick`,
`h = max`, `l = min`, `c = last`, `v = sum`. Live (not-yet-complete) 5-min bar
never counts.

### Clock (`psx_clock` helper copied into every tool)
- **Mon–Thu regular:** pre-open 09:15–09:30, break 09:30–09:32, open 09:32–15:30 PKT.
- **Friday:** 09:17–12:00 then 14:32–16:30 PKT (plan skill treats these as two
  separate sessions; `flat_by` applies per-session).
- **Ramadan mode:** open 09:17; close 13:30 Mon–Thu, 12:30 Fri. Ramadan date window
  lives in `psx_calendar.json` per year (2026 populated; 2027 pending notice).
- **Asia/Karachi is always UTC+05:00** — no DST, so no DST math needed.

### Calendar (`psx_calendar.json`)
- 2026: all 17 holidays from **PSX Notice PSX/N-89 (20 Jan 2026)**; moon-dependent
  ones carry `"confirm": "moon"`.
- 2027: `[]` with a visible NOTICE line — "PSX has not yet published the 2027
  holiday calendar; check https://dps.psx.com.pk/announcements for the notice."
  One JSON edit to update.
- Ramadan 2026 window included; 2027 pending.

### Market constants
- **Scrip circuit breaker:** `CB_PCT = 8.0`, `CB_MIN_PKR = 1.0` (PSX May 2024
  enhancement notice). Source URL in each SKILL.md.
- **Tick size:** `TICK = Decimal("0.01")` — flat across all price bands.
- **Lot size:** 1 share. No fractional.
- **Settlement:** **T+1**, effective 2026-02-09 (SECP / NCCPL / PSX).
- **Index halt:** KSE-30 ± 5% triggers a market-wide halt.

---

## 4. Shariah compliance (for public release)

- Three universe files, refreshed quarterly from PSX recomposition notices:
  - `kse100.seed.json` — the KSE-100 (top 100 by free-float market cap)
  - `kmi30.seed.json` — the KMI-30 Shariah top-30
  - `kmi_all.seed.json` — the **KMI-ALL Shares** full Shariah universe (~200 names)
- **`config.json → "shariah_only": false`** by default (so forkers get everything).
  When `true`, the scanner filters its universe to KMI-ALL and downstream skills
  inherit it (they operate on the scanner's watchlist).
- CLI overrides per run: `--kse100`, `--kmi30`, `--kmi-all`, `--shariah`, `--all`.
- **`psx-scanner refresh-lists`** command re-pulls the three lists from PSX
  notices (run it once a quarter).

---

## 5. Costs config (PKR)

`.claude/skills/psx-budget/costs.example.json` — you copy to `trader/costs.json`
and edit your broker's numbers:

```json
{
  "currency": "PKR",
  "broker_commission_pct": 0.15,
  "broker_commission_min_pkr_per_share": 0.03,
  "psx_trading_fee_per_lac": 3.50,
  "psx_regulatory_fee_per_lac": 0.62,
  "secp_levy_pct": 0.00065,
  "nccpl_trade_fee_per_lac": 0.81,
  "nccpl_secp_fee_per_lac": 0.30,
  "cdc_transaction_pct": 0.0036,
  "cdc_transaction_min_pkr": 5,
  "cdc_transaction_max_pkr": 25000,
  "provincial_sales_tax_pct": 13.0,
  "cvt_pct": 0.0,
  "is_filer": true,
  "cgt_pct_tax_year": 15.0,
  "wht_dividend_filer_pct": 15.0,
  "wht_dividend_non_filer_pct": 30.0,
  "source_notes": "PSX trading fees + NCCPL + CDC per Rs 100,000 from PSX regComSchedule. CGT 15% flat TY2026 per Finance Act 2024. CVT on share trades is 0 (abolished 2020). Replace broker_commission_pct with your broker's rate."
}
```

Every plan prints a one-block cost table like:

```
Trade cost at entry: PKR 15,810.00 (100 OGDC @ 158.10)
  Broker commission        PKR 23.72   (0.15%)
  PSX + SECP + NCCPL fees  PKR 0.82
  CDC transaction          PKR 5.69
  Provincial sales tax     PKR 3.19    (Sindh, 13% on brokerage+PSX)
  ────────────────
  Entry total              PKR 15,843.42
  Exit total (est.)        PKR 15,856.80
  Round-trip cost          PKR 90.22
  CGT at target (est.)     PKR 92.27   (15% of 6.15/share gain)
```

---

## 6. Data endpoints (verified live)

| Endpoint | Method | Shape |
|---|---|---|
| `/symbols` | GET JSON | `[{symbol, name, sectorName, isETF, isDebt}, ...]` |
| `/timeseries/int/<SYM>` | GET JSON | `{data: [[unix_sec, price, volume], ...]}` — ticks |
| `/timeseries/eod/<SYM>` | GET JSON | `{data: [[unix_sec, close, volume, open], ...]}` — ~5y history |
| `POST /historical` | POST `date=YYYY-MM-DD&sector=symbols` | HTML table, full-market OHLC for that date |
| `/company/<SYM>` | GET | HTML quote page (last, bid/ask, 52w, P/E, CB, VAR) |
| `/company/reports/<SYM>` | GET | HTML table of quarterly/annual PDF links |
| `POST /company/payouts` | POST `symbol=<SYM>` | HTML dividend history |
| `/announcements/companies` | GET + filters | HTML announcements |
| `/data/eligible-scrips/REG.xml` | GET XML | Regular Market universe |

All require `X-Requested-With: XMLHttpRequest`.

---

## 7. Endpoint-breakage risk & mitigation

- dps.psx.com.pk is PSX's public web portal — **not a documented API**. PSX can
  change the frontend and break us. No SLA.
- We read like any browser; we do **not** redistribute data. (PSX's Legal Notice
  requires a license for redistribution; personal analysis use is fine.)
- Politeness: single-threaded, 300 ms pause, polite User-Agent, one retry.
- Mitigation: every scrape function is small and isolated. An endpoint change =
  one-function swap. Every skill has `--offline` mode so tests and replays never
  touch the network.
- No viable free fallback for v1 (sarmaaya.pk / TradingView / Yahoo are all
  inferior or paid for PSX). HTTP layer is designed to plug one in later.

---

## 8. Broker integration

- PSX retail brokers (AKD, JS Global, Alfalah, Arif Habib, MIT, TradeCast, CTS)
  have **no public REST/OAuth API**. Mobile-app reverse-engineering is fragile
  and against ToS.
- `psx-ticket` writes a plain, copy-pasteable order ticket; `fill` records what
  actually filled; `flatten` builds the sell-to-close ticket. Paper mode by
  default.
- Adapter hook left in `psx-ticket.py` for if any PSX broker ever publishes an API.

---

## 9. Short selling

- PSX retail cannot short the Ready Market. Shorts live in Deliverable Futures
  (DFC) and Securities Lending & Borrowing (SLB), which are institutional.
- **Plan: long side only.** Same as the reference pack. DFC can be added later
  as a separate skill if wanted.

---

## 10. Testing

Before writing any skill code I'll capture **one full PSX trading day of real
data** and save it as offline fixtures: OGDC, HUBC, LUCK, ENGROH + KSE-100 levels.
For each symbol:

- `/timeseries/int/SYM` — full-day ticks → `scanner` / `entry` / `alerts` fixtures
- `/timeseries/eod/SYM` — ~60 sessions → `timemachine` fixture
- `POST /historical` for 20 past trading dates → full-market daily OHLC fixtures
- `/company/SYM` — quote snapshot → `news` price-move line fixture
- `/announcements/companies` filtered to those symbols → `news` / `analyst` fixtures

Then 10 `test_*.py` files mirroring the reference pack, offline. They assert:
- ORB trigger reproduces with real numbers
- Monte Carlo odds are deterministic with `seed=19`
- Risk gates fire on contrived orders
- Cost math matches the config
- Circuit-breaker boundary refuses an over-cap plan
- Shariah filter trims the universe correctly

Then **one live end-to-end run on one PSX stock with today's data**, showing
every step's output.

Then a `grep` pass to prove no US-specific strings (NYSE, $, 9:30, 15:55, Yahoo,
Alpaca, SEC, Pelosi, EDGAR) remain.

---

## 11. Deliverables

1. The 10 skills under `.claude/skills/` so you can use `/psx-scanner`,
   `/psx-news`, etc. in this project.
2. Offline fixtures + 10 test files, all passing.
3. A short `README.md` with:
   - 3-step install
   - "What to type each morning / during the session / at close"
   - A worked example on one real PSX stock
   - Paper-trading walkthrough
   - FAQ (circuit breakers, T+1, moon-dependent Eid, why fills differ from your
     ticket, how to update KSE-100 / KMI lists)
4. This `PLAN.md` kept as the design reference.

---

## 12. Not in scope (v1)

- No broker API (manual ticket only).
- No short selling.
- No DFC / SLB / MTS.
- No options.
- No web UI. (Scope hook left in `--json` outputs so a local dashboard can be
  built in v2 if you want it.)

---

## 13. Open items (explicit, flagged)

1. **2027 PSX holiday calendar** — not yet published. Ships as empty with a
   NOTICE line; update when PSX issues it.
2. **Moon-dependent Eid / Ashura / Milad dates** — marked `"confirm": "moon"`.
   `psx-alerts clock` warns the day before.
3. **Exact scrip CB %** — using **8% (or Re 1)** per PSX May 2024 enhancement
   notice. If PSX changed it since, one constant.
4. **FED on brokerage** — treated as subsumed into provincial sales tax; user
   can override in `costs.json`.
5. **Ramadan 2027 window** — not known until PSX notice.

---

## 14. Build order (once approved)

1. Capture one trading day of fixtures (so you see real data first).
2. Build the 10 skills in order: scanner → news → entry → exit → timemachine
   → budget → risk → ticket → alerts → analyst.
3. Write the 10 test files, make them pass.
4. Live end-to-end run on one PSX stock.
5. `grep` pass for US-specific leftovers.
6. Write README.

---

## 15. Safety stanza (same across every SKILL.md, in PSX wording)

- Education and tooling, not financial advice. A PASS is a rules check, not a
  recommendation.
- Paper trading by default (`"paper": true`). Going live is your own decision.
- Nothing in this pack places an order. `psx-ticket` writes a plain ticket for
  you to enter in your broker's app yourself.
- No options, margin, short selling or leverage.
- Most people who day trade lose money. These tools do not change that.
