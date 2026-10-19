# Polymarket CopyLab

A Python 3.9+ paper copy-trading prototype for Mac, Windows, and later cloud hosting. No dependencies, credentials, or real order placement.

## Run

From this folder on Mac:

```sh
python3 -m copylab leaderboard
python3 -m copylab watch --wallet 0xYOUR_SELECTED_40_CHARACTER_WALLET --poll 30
python3 -m copylab status
```

Replace the placeholder with a public wallet from the leaderboard's `proxyWallet` field. Repeat `--wallet ADDRESS` to follow multiple wallets. On Windows use `py` instead of `python3`. Stop with Ctrl+C. For a single scan add `--once`; the first scan only establishes the starting time. Run only one watcher per database.

Leaderboard rankings are research candidates, not verified profitable strategies. Select wallets manually; this version does not evaluate consistency or realized returns.

## Simulation rules

- $40 initial simulated cash; $2 maximum cumulative cost per outcome token.
- $8 maximum spending per UTC day. This is a spending cap, not a daily loss calculation.
- New BUY activity only, maximum age 120 seconds; startup history is skipped.
- Copies only at asking prices no more than 0.02 above the source price; walks visible ask depth and allows partial simulated fills.
- Adds a fixed 2% cost reserve, which is an assumption, not Polymarket's actual market-specific fee calculation.
- SQLite saves fills and processed events across restarts. `--db PATH` before the command selects another ledger.
- Open positions are valued by walking current bid depth and deducting a 2% estimated exit cost. Unfilled shares receive zero immediate liquidation value. This is a conservative estimate, not a guaranteed sale.
- Confirmed closed binary markets with exactly one winner settle automatically in the paper ledger: $1 per winning share, $0 per losing share. Closed markets without a confirmed winner remain open; ambiguous or nonbinary resolutions need separate handling.
- Settlement credits simulated cash exactly once. There are no actual redemptions or live orders. The original $40 cumulative purchase cap remains; payouts are not automatically reinvested.
- No source-wallet sell copying yet. Positions are held until settlement; open estimated profit can change before settlement.

Public polling can miss trades when a wallet produces more than 500 trades between polls. There is no historical backfill. Duplicate detection hashes the complete public trade record, so API changes to the same record can alter its identity. Current order-book snapshots cannot guarantee executable fills or simulate competition, latency, or profitability.

## Profit and progress

```sh
python3 -m copylab status
python3 -m copylab status --json
python3 -m copylab status --offline
```

`status` refreshes held positions from public APIs. The watcher also prints a fresh report after each polling cycle. Reports show:

- **Cash:** initial $40 minus purchase costs (including the entry reserve), plus simulated settlement payouts.
- **Open P/L:** estimated net bid liquidation value minus the cost of open positions.
- **Settled P/L:** confirmed simulated payouts minus the cost of settled positions.
- **Equity:** cash plus estimated open liquidation value.
- **Total P/L:** equity minus the initial $40; equals open plus settled P/L when valuation is complete.

Missing or failed quotes make aggregate equity and total P/L UNKNOWN. Offline quotes expire after 120 seconds; they are not presented as current profit. No trades means $40 equity and $0 profit. Profit figures are simulation results using fixed fee reserves, not evidence of executable returns.

## Verification

```sh
python3 -m unittest discover -s tests -v
python3 -m copylab --help
```

Offline tests cover duplicate suppression, per-token/day/total caps, stale signals, adverse entry prices, depth, fee reserves, open P/L, winning and losing settlement, restart-safe payouts, missing/stale quotes, and existing database migration. Live verification passed on October 19, 2026 with network permission: leaderboard lookup, public wallet trade feed, public order-book schema, and two watcher scans using a temporary ledger. Startup history was skipped and no simulated buys occurred during those scans. This verifies connectivity, not strategy profitability or real execution.

## Public API references

This prototype uses documented legacy Data API routes and the public CLOB book route. Legacy routes may need migration as Polymarket evolves its APIs.

- https://docs.polymarket.com/api-reference/core/get-trades-for-a-user-or-markets
- https://docs.polymarket.com/api-reference/core/get-trader-leaderboard-rankings
- https://docs.polymarket.com/

## Next milestones

Add current API migration, market-specific fees, source-wallet analysis, and source sell copying; then measure paper results over time. Live execution requires a separate implementation and eligibility checks. Never put wallet keys in Git or chat.
