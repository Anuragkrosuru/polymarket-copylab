# Polymarket CopyLab

A Python 3.9+ paper copy-trading bot for Mac, Windows, and later cloud hosting. No dependencies, credentials, or real order placement.

## Run

From this folder on Mac:

```sh
python3 -m copylab leaderboard
python3 -m copylab watch --wallet-file watchlist.json --poll 5
python3 -m copylab status
```

The included `watchlist.json` contains three public wallets selected for recent activity during setup, not a proven trading advantage. Edit its `wallets` list or use `--wallet ADDRESS` (repeatable) to select other wallets from the leaderboard's `proxyWallet` field. On Windows use `py` instead of `python3`. Stop with Ctrl+C. For a single scan add `--once`; the first scan only establishes the starting time. A cross-platform file lock prevents two watchers using the same database. Add `--duration 3600` for a one-hour trial; otherwise the watcher runs until stopped. Network calls can extend the duration. Status and history remain readable while the watcher runs.

Leaderboard rankings are research candidates, not verified profitable strategies. Select wallets manually; this version does not evaluate consistency or realized returns.

## Simulation rules

- $40 initial simulated cash; $2 maximum cumulative cost per market across all wallets and both outcomes, with a $2 per-outcome cap as well.
- $8 maximum spending per UTC day. This is a spending cap, not a daily loss calculation.
- New BUY and SELL activity only, maximum age 120 seconds; startup history is skipped.
- Enforces the order book minimum share size for buys and exits; a $2 budget may be too small for many markets.
- Copies only at asking prices no more than the smaller of 5% or 0.02 above the source price; walks visible ask depth and allows partial simulated fills.
- Adds a fixed 2% cost reserve, which is an assumption, not Polymarket's actual market-specific fee calculation.
- SQLite saves fills and processed events across restarts. `--db PATH` before the command selects another ledger.
- Open positions are valued by walking current bid depth and deducting a 2% estimated exit cost. Unfilled shares receive zero immediate liquidation value. This is a conservative estimate, not a guaranteed sale.
- Confirmed closed binary markets with exactly one winner settle automatically in the paper ledger: $1 per winning share, $0 per losing share. Closed markets without a confirmed winner remain open; ambiguous or nonbinary resolutions need separate handling.
- Settlement credits simulated cash exactly once. There are no actual redemptions or live orders. The original $40 cumulative purchase cap remains; payouts are not automatically reinvested.
- A source SELL requests a full exit of copied holdings from that same wallet and outcome token. This is a deliberate simplification, not an exact proportional copy of their portfolio. Exit bids must be within 0.02 of the source sale price. Partial sales are possible when liquidity is thin; unsold shares remain open. There is no automatic stop-loss. Unfilled copied sells retry for up to 120 seconds after the original source signal, at intervals of at least 5 seconds. Re-reading identical bid levels cannot create another fill. Requests apply only to the lots held when that source sell arrived, survive restart, and are superseded by a newer source sell. Expired requests leave any unsold holding open until another source signal or settlement.

Public polling can miss trades when a wallet produces more than 500 trades between polls. There is no historical backfill. Duplicate detection uses transaction, wallet, token, side, timestamp, price, and size; profile edits do not replay trades. The public API does not expose a unique fill index, so indistinguishable same-transaction fills may be collapsed. Feed saturation is reported as a warning. Current order-book snapshots cannot guarantee executable fills or simulate competition, latency, or profitability.

## Profit and progress

```sh
python3 -m copylab status
python3 -m copylab status --json
python3 -m copylab status --offline
```

`status` refreshes held positions from public APIs. The watcher also prints a fresh report about every 30 seconds while checking feeds on a five-second target cycle. Reports show:

- **Cash:** initial $40 minus purchase costs (including the entry reserve), plus net simulated sale proceeds and settlement payouts.
- **Open P/L:** estimated net bid liquidation value minus the cost of open positions.
- **Realized P/L:** net sale proceeds and confirmed simulated payouts minus their allocated entry costs. Sale and settlement P/L are also shown separately.
- **Equity:** cash plus estimated open liquidation value.
- **Total P/L:** equity minus the initial $40; equals open plus realized P/L when valuation is complete.

Missing or failed quotes make aggregate equity and total P/L UNKNOWN. Offline quotes expire after 120 seconds; they are not presented as current profit. No trades means $40 equity and $0 profit. Profit figures are simulation results using fixed fee reserves, not evidence of executable returns.

## Repeatable simulation and saved progress

```sh
python3 -m copylab simulate
python3 -m copylab history --limit 20
```

`simulate` runs three **invented integration scenarios**, using the same scanner, execution rules, fees, and accounting as the live paper watcher, in separate temporary ledgers. It writes `simulation-results.json`. Favorable, mixed, and adverse outcomes demonstrate how gains and losses are calculated; they are not historical results, win-rate estimates, or predictions. Never combine these results with the live paper ledger.

The watcher checks feeds on a five-second target cycle and saves reports and snapshots about every 30 seconds. `history` returns snapshots as JSON. `status --json` includes scan timestamps, source activity age, errors, and decisions explaining skipped trades. Network requests retry transient failures up to three times; a failed event does not block other events. Failed valuations stay unknown. The watcher runs only while its process is active and the computer is awake.

## Verification

```sh
python3 -m unittest discover -s tests -v
python3 -m copylab --help
```

The 56 offline tests cover duplicate suppression, per-token/day/total caps, stale signals, minimum order sizes, adverse entry and exit prices, depth, fee reserves, full and partial source exits, open P/L, winning and losing settlement, restart-safe payouts and sales, missing/stale quotes, request retries, isolated event failures, watcher locking, synthetic integration scenarios, and existing database migration. Live verification passed on October 19, 2026 with network permission: leaderboard lookup, public wallet trade feed, public order-book schema, and two watcher scans using a temporary ledger. Startup history was skipped and no simulated buys occurred during those scans. This verifies connectivity, not strategy profitability or real execution.

## Public API references

This prototype uses documented legacy Data API routes and the public CLOB book route. Legacy routes may need migration as Polymarket evolves its APIs.

- https://docs.polymarket.com/api-reference/core/get-trades-for-a-user-or-markets
- https://docs.polymarket.com/api-reference/core/get-trader-leaderboard-rankings
- https://docs.polymarket.com/

## Next milestones

Future work: current API migration, market-specific fees, proportional source exits, source-wallet analysis, persistent hosting, and longer paper trials. The implemented trial does not establish a profitable strategy. Live execution requires a separate implementation and eligibility checks. Never put wallet keys in Git or chat.

## Current local trial

The paper watcher was launched in the background with the included three-wallet watchlist. Its copied trades and settlements are in `paper.log`, operational details are in `paper-diagnostics.log`, its process ID is in `paper.pid`, and accounting and progress are in `paper.sqlite3`. These local runtime files are ignored by Git. GitHub contains code and the reproducible synthetic test output, not the live paper account.

On this Mac you can stop the background trial from this folder:

```sh
kill -TERM "$(cat paper.pid)"
```

To restart in the foreground:

```sh
python3 -u -m copylab watch --wallet-file watchlist.json --poll 5
```

There is no system startup service or cloud hosting configured. A sleeping or powered-off computer does not collect trades. A profitable-looking snapshot or synthetic scenario is not a validated profitable strategy.

## Updating Word trade report

```sh
python3 -m copylab report --output-dir reports
```

Exports every copied buy, sell, and settlement to `reports/trade-history.csv`, along with a current account report in Markdown and JSON. It reads the latest saved valuations; use `status` first if you need a manual price refresh. Add `--docx-python /path/to/python` to also generate `reports/trade-report.docx` using a Python environment with `python-docx` installed.

For automatic updates, start the watcher with `--report-dir reports --docx-python /path/to/python`. The current Mac background trial uses the bundled document Python and updates the same report about every 30 seconds. Other computers can use any Python with `python-docx` for the optional Word exporter. Core monitoring and CSV exports do not require extra packages.

The report separates cash changes, simulated fee reserves, realized sale and settlement gains, and estimated open gains. Source trade times are labeled when an older fill has no recorded copy time. Runtime reports remain local and are excluded from Git. Open the Word file again or reload it to see subsequent changes.

## Broad trader screening

```sh
python3 -m copylab discover --policy screening-policy.json --output-dir reports
python3 -m copylab discover --policy screening-policy.json --output-dir reports --repeat
python3 -m copylab watch --wallet-file reports/screened-watchlist.json --report-dir reports --poll 5
```

Discovery covers the top 100 positive-PnL entries in each global daily, weekly, monthly, and all-time leaderboard: up to 400 ranking slots, deduplicated by wallet. These global boards span markets, but this is not every profitable wallet on Polymarket. The default policy examines up to 1,500 recent trade fills and 200 closed positions per wallet within a 30-day window. Caps and data failures are visible in the export; sampled P/L is not a complete account return.

Main screening defaults:

- At least 50 distinct transaction hashes containing trades, not 50 partial fills from one transaction.
- At least 20 distinct closed markets and 5 observed trading days.
- Positive sampled closed-market P/L, still positive after excluding the largest winning market.
- Largest winning market at most 50% of gross positive market P/L; profit factor at least 1.2.
- A source trade observed within the past 24 hours.

The bot can select up to 40 main wallets and 2 experimental wallets. Among wallets that pass the same screens, those with a source trade in the past hour are prioritized before the sample score. Experimental candidates need at least 10 trade transactions, 3 closed markets, and 2 trading days, but can have profit concentrated in one winner. Their copied position cap is $1, with a $1 daily cap per experimental wallet and a $2 daily cap for the whole experimental group. The $2 main position limit, $8 daily spending cap, and $40 cumulative purchase cap remain. Multiple wallets cannot increase cumulative spending in the same market above $2 by choosing different outcomes. The ranking is a transparent heuristic based on sample size and spread of results, not a prediction of profit or a verified risk classification.

`reports/trader-screening.csv` lists every assessed wallet, observed counts, sampled profit, largest-winner dependence, capped history, classification, and exclusion reasons. `reports/trader-screening.json` retains detailed coverage and policy. The Word report adds the selection rules and selected traders. An empty selection pauses screened buys; it never silently weakens the criteria.

The discovery process refreshes hourly. The watcher reloads the published list every cycle, stops new screened entries if it is more than two hours old, and keeps monitoring sells for held positions when their source wallet leaves the selection. Discovery uses limited parallel public requests and publishes results atomically. Discovery and watcher are separate local processes; both require an awake computer.

Current limitations: closed-history samples may omit losses outside the fetched pages; open losing positions, capital, deposits, off-platform hedges, and account control relationships are not assessed. Multiple wallets can belong to one trader. Neither the leaderboard nor this screen establishes a copyable advantage. Main means passing these sample screens, not low risk.

Public endpoint reference for the realized P/L field: https://docs.polymarket.com/api-reference/core/get-closed-positions-for-a-user

Entry controls limit price deterioration relative to the source trade as well as in cents. For example, a source entry at 3.3 cents cannot be copied at 4.7 cents. No minimum trading frequency is enforced: a wider watchlist creates more opportunities to inspect, while the budget, minimum market share sizes, freshness rules, and entry price checks still determine whether a simulated buy is made. Several positions may be open simultaneously; $8 daily spending permits four full $2 entries, or more smaller entries when minimum sizes allow.

## Faster wallet monitoring

The default `--poll 5` targets five seconds between cycle starts rather than sleeping five seconds after all work completes. Four concurrent workers retrieve public wallet feeds, limited to ten request starts per second. Wallet feeds are processed as they arrive; all SQLite writes stay on the main thread. Quotes for candidate entries are fetched immediately before applying the entry checks. Reports remain on a separate `--report-every 30` interval so Word rendering does not need to run on every data check.

`status --json` includes a `polling` object with requested and observed cycle intervals. Network delays, upstream publication delays, retries, and bursts of candidate trades can make actual intervals longer. Faster polling cannot recover the source trader's original entry price. The public market WebSocket is not a replacement for identifying every arbitrary wallet trade, so this version uses measured, rate-limited public polling.

Polymarket documents a v1 `/trades` limit of 200 requests per 10 seconds: https://docs.polymarket.com/api-reference/rate-limits

## Entry quality and execution evidence

New buys need valid, noncrossed bid and ask books, enough visible bid depth for the copied share count, and an estimated immediate liquidation loss no greater than 10% of all-in entry cost after the fixed entry and exit reserves. This is a cost filter, not a forecast or a stop-loss. It complements the existing relative source-price guard and spending limits. Previously recorded trades are not rewritten to satisfy newer rules.

New trade sources record an execution-policy version. `reports/trade-history.csv` includes the source entry, copied entry, relative price difference, observed source-signal age, and entry drag when recorded. Older fills retain a legacy label and missing measurements remain blank. `reports/execution-quality.csv` records each checked execution attempt, including rejected entries and changed-book exit retries. Source exits are still requests to close the matching copied lots in full, not reconstructions of the trader's full portfolio.

## Trade only log

The current background watcher uses `--trade-log paper.log` and sends console diagnostics to `paper-diagnostics.log`. The trade log is rebuilt from the saved ledger each cycle, including earlier trades. Each line is one copied buy, aggregated copied sale, or confirmed paper settlement, with timestamp, market, outcome, shares, price, cash change, and realized P/L on exits. Portfolio valuation updates, wallet polling, rejected signals, and errors stay in diagnostics and the status/report commands. The old verbose log is preserved as `paper-verbose-backup.log`.
