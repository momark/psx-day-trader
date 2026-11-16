# psx-day-trader

A Claude Code skill pack for day-trading the **Pakistan Stock Exchange (PSX)**
on free public market data from the [PSX Data Portal](https://dps.psx.com.pk).
Ten skills that walk you through a day trade end to end: scan the universe,
check the news, find an entry, plan the exit, replay the plan, size the
position (with real PSX costs in PKR), pre-flight the risk, and print a paper
ticket you enter in your broker's app by hand.

A direct port of [jakeschincariol/day-trader-skill](https://github.com/jakeschincariol/day-trader-skill)
(same flow, same safety rules) adapted for PSX conventions: Asia/Karachi
clock, Mon-Thu + Friday split session, Ramadan hours, 8 % scrip circuit
breaker, flat PKR 0.01 tick, 1-share lot, T+1 settlement, PSX / NCCPL /
SECP / CDC fees, Sindh sales tax on brokerage, FBR CGT, KSE-100 / KMI-30
/ KMI-ALL (Shariah) filters, and PSX corporate announcements in place of
SEC EDGAR filings.

**None of this is a recommendation to trade or a prediction of any kind.
Most people who day-trade lose money. This pack does not change that.**

## What's inside

The pack has one shared HTTP helper (`.claude/skills/_lib/psx_http.py`) and
ten skills:

| Skill | What it does |
| --- | --- |
| **psx-scanner** | Build the universe (or filter to KSE-100 / KMI-30 / Shariah KMI-ALL), run daily closing-price scans (`breakout_20d`, `bull_flag`, `reclaim_ma20`, `oversold`, `oversold_bounce`) and an intraday opening-range breakout scan on 5-minute bars bucketed from PSX ticks. |
| **psx-news** | Pull PSX corporate announcements (dividends, book closures, financial results, material info, meetings), classify them and flag items on your watchlist. |
| **psx-entry** | Watch intraday ticks for a precise entry trigger: first 5-min bar to close above the OR high (or below the OR low) with a confirm rule. |
| **psx-exit** | Write a stop-and-target plan - stop at OR-mid / OR-low / OR-high / a fixed price / a % off entry, target as an R-multiple. |
| **psx-timemachine** | Replay the stop-and-target rule against the last ~60 sessions of EOD bars plus a 10,000-draw Monte Carlo. Verdicts: STRONG_TARGET / STRONG_STOP / COIN_FLIP / MANY_NEITHER / MIXED. |
| **psx-budget** | Four-limit sizing (max_loss / position_pct / max_order / cash) with every PSX cost on both legs: broker commission, PSX / NCCPL / SECP / CDC fees, provincial sales tax on brokerage, CGT on the gain. |
| **psx-risk** | 12 pre-flight checks: order shape, cash, position %, PSX scrip circuit breaker, stop side, min net R/R, daily loss budget, trading window + holidays, short-selling warning, Shariah filter, duplicate order via fingerprint, cost sanity. |
| **psx-ticket** | Print a paper ticket (plain text) you enter by hand in your broker's app; strict `yes` approval; record the fill after the trade. **PSX has no public retail broker API** - that's why this is a paper ticket, not a submit. |
| **psx-alerts** | Small price / time alert list (above, below, pct_move, time, opening_bell). |
| **psx-analyst** | Lists every quarterly / half-year / annual financial-report PDF URL on `financials.psx.com.pk` and the recency of the latest one. |

## Install

```bash
# 1. Clone next to your other Claude Code skills, or into any project folder.
git clone https://github.com/<you>/psx-day-trader.git

# 2. One-time Python setup.
python3 -m pip install playwright
python3 -m playwright install chromium

# 3. Inside the project you want the skills live in:
mkdir -p .claude/skills
cp -r psx-day-trader/.claude/skills/* .claude/skills/
# (or symlink each folder; or add the repo as a Claude Code plugin — see .claude-plugin/plugin.json)
```

Claude Code picks up every folder under `.claude/skills/` whose SKILL.md
has a `name:` field. The ten skills register as `psx-scanner`,
`psx-news`, etc.

## A typical trading day

All commands below use a fictitious trading day; replace symbols and
timing with what the scanner surfaces for you.

```bash
# 1. Build the watchlist for today (KSE-100 only, or Shariah only).
python3 .claude/skills/psx-scanner/scan.py universe --index KSE100
python3 .claude/skills/psx-scanner/scan.py daily --min-price 10 --min-volume 500000

# 2. See the overnight announcements for the symbols you now care about.
python3 .claude/skills/psx-news/news.py

# 3. After the opening range closes (~09:47 PKT), run the ORB scan.
python3 .claude/skills/psx-scanner/scan.py orb

# 4. Pick one symbol; watch for its ORB entry.
python3 .claude/skills/psx-entry/entry.py HUBC --rule orb --long

# 5. Plan the exit.
python3 .claude/skills/psx-exit/exit.py --stop or-low --rr 2

# 6. Sanity-check against history.
python3 .claude/skills/psx-timemachine/timemachine.py --horizon-days 3

# 7. Size the position with PSX costs (first run writes a default psx/config.json; edit it).
python3 .claude/skills/psx-budget/size.py

# 8. Pre-flight checks.
python3 .claude/skills/psx-risk/check.py --prev-close 207.33 --shariah warn

# 9. Print the paper ticket; type "yes" exactly to record it.
python3 .claude/skills/psx-ticket/ticket.py write --approve yes

# ... place the order BY HAND in your broker's app ...

# 10. Come back and record the actual fill.
python3 .claude/skills/psx-ticket/ticket.py fill <fingerprint> --price 208.25 --qty 240
```

Through Claude Code you also just say "scan the PSX", "ORB entry on HUBC",
"print the ticket", etc. and the right skill runs.

## A note on the PSX Data Portal

`dps.psx.com.pk` serves its JSON endpoints behind three layered gates: a
TLS fingerprint check, a UA check, and a per-page session key
(`window.__ps._k`, same shape as a CSRF meta tag) echoed as `x-req-id`
on every XHR. We drive Chromium via Playwright so TLS + UA line up, and
read `_k` from the first HTML page we load before issuing any XHR.

**This is not an official API.** PSX publishes this data for its own web
portal; there is no documented retail feed. The portal structure can
change at any time. If you hit a wall of 403s, re-check the fixtures in
`tests/fixtures/raw/` against what the portal is serving now, re-capture
with `python3 tests/scripts/capture_fixtures.py`, and adjust the parsers
in each skill. Alternatives for commercial use: the paid feed PSX itself
licenses via `marketdatarequest@psx.com.pk`, or licensed data vendors
(Reuters / Refinitiv, Bloomberg, Mufap for mutual funds).

## Costs, Shariah, taxes

Everything is editable in `psx/config.json` - the file is written on the
first `psx-budget` run with the default public tariffs as of 2026-06:

- Broker commission: 15 bps, min PKR 50, max PKR 10,000 per trade.
  **Your broker's rate card is what counts** - edit this.
- PSX fees per Rs 100,000 traded: PSX trading 3.50, PSX regulatory 0.62,
  NCCPL trade 0.81, NCCPL SECP 0.30. (Source:
  `psx.com.pk/psx/exchange/general/fees-and-charges`.)
- SECP levy: 0.0065 % of order value.
- CDC: 0.0036 % of value, min 5, max 25,000.
- Provincial sales tax on brokerage + PSX fee: default 13 % (Sindh).
  Punjab / KP / Balochistan / ICT are 16 / 15 / 15 / 16 - edit.
- CGT (TY 2026): flat 15 %, applied to the gain on close. (Source: FBR
  Finance Act 2024, confirmed by Finance Bill 2026.)

The Shariah filter (`--shariah` in the scanner, `--shariah strict|warn`
in risk) uses the KMI-ALL Shariah universe (`/indices/KMIALLSHR` on the
portal, ~300 symbols). The author only trades Shariah-compliant stocks,
but the default is **off** so public forks see every setup.

## PSX session, holidays, circuit breaker

Baked into `psx-risk`:

- Regular session: Mon-Thu 09:32-15:30 PKT, Friday split 09:17-12:00 /
  14:32-16:30, Ramadan Mon-Thu 09:17-13:30 / Friday 09:17-12:30.
- Scrip circuit breaker: 8 % or Re 1 (whichever higher) from previous
  close. (PSX May 2024 Enhancement notice.)
- 2026 PSX holidays from PSX notice PSX/N-89 (20 Jan 2026). Moon-dependent
  Eid dates are flagged as "to confirm" and warn rather than fail.
- 2027 PSX holidays are not yet published (as of 2026-11-17) - the risk
  check warns for 2027 dates until PSX issues the notice.

## Offline / fixture mode

`tests/scripts/capture_fixtures.py` writes a full day of real portal
responses into `tests/fixtures/raw/` (one symbol universe, 4 companies
at full detail, 25 past trading dates of full-market OHLCV, 3 indices,
announcements - ~5 MB on disk).

Pass `--offline tests/fixtures/raw` to any skill and the HTTP layer
reads from the local files - no network, no browser, no WAF. That's
what the tests run against; it also lets you backtest a strategy as of
a captured day.

```bash
python3 .claude/skills/psx-scanner/scan.py daily --offline tests/fixtures/raw
python3 .claude/skills/psx-entry/entry.py HUBC --rule orb --long --at 15:00 \
    --now "2026-11-16 15:00" --offline tests/fixtures/raw
```

To re-capture (a few minutes, polite 500 ms spacing):

```bash
python3 tests/scripts/capture_fixtures.py
```

## Running the tests

```bash
cd tests
python3 -m unittest discover
```

The tests run entirely offline against the captured fixtures. CI should
install Playwright + chromium but does not need network access.

## What this pack doesn't do

- **No broker auto-submit.** PSX retail brokers (AKD, JS Global, Alfalah,
  Arif Habib, MIT, Foundation TradeX, TradeCast, ...) all run proprietary
  platforms without a public retail API. The ticket is the handoff.
- **No short selling.** PSX short selling is only available through
  NCCPL's Securities Lending & Borrowing scheme; `psx-risk` warns when
  a `--short` order comes through.
- **No OHLCV in `ts-eod`.** The portal's EOD timeseries endpoint serves
  close, volume, open - no high or low. The Time Machine bars the daily
  range with a conservative ±0.5 % buffer around `[min(o,c), max(o,c)]`
  for its hit test. Full OHLCV **is** available per date via the
  `POST /historical` table; a future version can walk that for exact
  H / L.
- **No tick-level backtests.** Intraday ticks are only served for the
  current session; historical intraday is not a PSX Data Portal feature.
- **No position accounting beyond a FIFO ticket log.** This is a
  day-trading planner, not an accounting system.

## License

MIT - see [LICENSE](LICENSE).

Attribution to [jakeschincariol/day-trader-skill](https://github.com/jakeschincariol/day-trader-skill)
for the original US-markets architecture.
