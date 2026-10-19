# Polymarket CopyLab

A Python 3.9+ paper copy-trading bot for Mac, Windows, and later cloud hosting. No dependencies, credentials, or real order placement.

## Run

From this folder on Mac:

```sh
python3 -m copylab leaderboard
python3 -m copylab watch --wallet-file watchlist.json --poll 30
python3 -m copylab status
```

The included `watchlist.json` contains three public wallets selected for recent activity during setup, not a proven trading advantage. Edit its `wallets` list or use `--wallet ADDRESS` (repeatable) to select other wallets from the leaderboard's `proxyWallet` field. On Windows use `py` instead of `python3`. Stop with Ctrl+C. For a single scan add `--once`; the first scan only establishes the starting time. A cross-platform file lock prevents two watchers using the same database. Add `--duration 3600` for a one-hour trial; otherwise the watcher runs until stopped. Network calls can extend the duration. Status and history remain readable while the watcher runs.

Leaderboard rankings are research candidates, not verified profitable strategies. Select wallets manually; this version does not evaluate consistency or realized returns.

## Simulation rules

- $40 initial simulated cash; $2 maximum cumulative cost per outcome token.
- $8 maximum spending per UTC day. This is a spending cap, not a daily loss calculation.
- New BUY and SELL activity only, maximum age 120 seconds; startup history is skipped.
- Enforces the order book minimum share size for buys and exits; a $2 budget may be too small for many markets.
- Copies only at asking prices no more than 0.02 above the source price; walks visible ask depth and allows partial simulated fills.
- Adds a fixed 2% cost reserve, which is an assumption, not Polymarket's actual market-specific fee calculation.
- SQLite saves fills and processed events across restarts. `--db PATH` before the command selects another ledger.
- Open positions are valued by walking current bid depth and deducting a 2% estimated exit cost. Unfilled shares receive zero immediate liquidation value. This is a conservative estimate, not a guaranteed sale.
- Confirmed closed binary markets with exactly one winner settle automatically in the paper ledger: $1 per winning share, $0 per losing share. Closed markets without a confirmed winner remain open; ambiguous or nonbinary resolutions need separate handling.
- Settlement credits simulated cash exactly once. There are no actual redemptions or live orders. The original $40 cumulative purchase cap remains; payouts are not automatically reinvested.
- A source SELL requests a full exit of copied holdings from that same wallet and outcome token. This is a deliberate simplification, not an exact proportional copy of their portfolio. Exit bids must be within 0.02 of the source sale price. Partial sales are possible when liquidity is thin; unsold shares remain open. No stop-loss or source-sell retry after the original event has been processed; later source sell events may exit the remainder.

Public polling can miss trades when a wallet produces more than 500 trades between polls. There is no historical backfill. Duplicate detection uses transaction, wallet, token, side, timestamp, price, and size; profile edits do not replay trades. The public API does not expose a unique fill index, so indistinguishable same-transaction fills may be collapsed. Feed saturation is reported as a warning. Current order-book snapshots cannot guarantee executable fills or simulate competition, latency, or profitability.

## Profit and progress

```sh
python3 -m copylab status
python3 -m copylab status --json
python3 -m copylab status --offline
```

`status` refreshes held positions from public APIs. The watcher also prints a fresh report after each polling cycle. Reports show:

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

The watcher saves a portfolio snapshot after every poll. `history` returns snapshots as JSON. `status --json` includes scan timestamps, source activity age, errors, and decisions explaining skipped trades. Network requests retry transient failures up to three times; a failed event does not block other events. Failed valuations stay unknown. The watcher runs only while its process is active and the computer is awake.

## Verification

```sh
python3 -m unittest discover -s tests -v
python3 -m copylab --help
```

The 24 offline tests cover duplicate suppression, per-token/day/total caps, stale signals, minimum order sizes, adverse entry and exit prices, depth, fee reserves, full and partial source exits, open P/L, winning and losing settlement, restart-safe payouts and sales, missing/stale quotes, request retries, isolated event failures, watcher locking, synthetic integration scenarios, and existing database migration. Live verification passed on October 19, 2026 with network permission: leaderboard lookup, public wallet trade feed, public order-book schema, and two watcher scans using a temporary ledger. Startup history was skipped and no simulated buys occurred during those scans. This verifies connectivity, not strategy profitability or real execution.

## Public API references

This prototype uses documented legacy Data API routes and the public CLOB book route. Legacy routes may need migration as Polymarket evolves its APIs.

- https://docs.polymarket.com/api-reference/core/get-trades-for-a-user-or-markets
- https://docs.polymarket.com/api-reference/core/get-trader-leaderboard-rankings
- https://docs.polymarket.com/

## Next milestones

Future work: current API migration, market-specific fees, proportional source exits, source-wallet analysis, persistent hosting, and longer paper trials. The implemented trial does not establish a profitable strategy. Live execution requires a separate implementation and eligibility checks. Never put wallet keys in Git or chat.

## Current local trial

The paper watcher was launched in the background with the included three-wallet watchlist. Its output is in `paper.log`, its process ID is in `paper.pid`, and accounting and progress are in `paper.sqlite3`. These local runtime files are ignored by Git. GitHub contains code and the reproducible synthetic test output, not the live paper account.

On this Mac you can stop the background trial from this folder:

```sh
kill -TERM "$(cat paper.pid)"
```

To restart in the foreground:

```sh
python3 -u -m copylab watch --wallet-file watchlist.json --poll 30
```

There is no system startup service or cloud hosting configured. A sleeping or powered-off computer does not collect trades. A profitable-looking snapshot or synthetic scenario is not a validated profitable strategy.
