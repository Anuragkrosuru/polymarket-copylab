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
- No sells, settlement, mark-to-market valuation, or realized profit tracking yet. Cash decreases as positions accumulate. Open cost is not account equity or profit.

Public polling can miss trades when a wallet produces more than 500 trades between polls. There is no historical backfill. Duplicate detection hashes the complete public trade record, so API changes to the same record can alter its identity. Current order-book snapshots cannot guarantee executable fills or simulate competition, latency, or profitability.

## Verification

```sh
python3 -m unittest discover -s tests -v
python3 -m copylab --help
```

Offline tests cover duplicate suppression, per-token/day/total caps, stale signals, adverse entry prices, depth, and cost reserves. Live API connectivity has not been verified in the build environment because DNS access was unavailable.

## Public API references

This prototype uses documented legacy Data API routes and the public CLOB book route. Legacy routes may need migration as Polymarket evolves its APIs.

- https://docs.polymarket.com/api-reference/core/get-trades-for-a-user-or-markets
- https://docs.polymarket.com/api-reference/core/get-trader-leaderboard-rankings
- https://docs.polymarket.com/

## Next milestones

Verify API responses from a connected machine; add current API migration, market-specific fees, source-wallet analysis, sell and settlement tracking, then measure paper results. Live execution requires a separate implementation and eligibility checks. Never put wallet keys in Git or chat.
