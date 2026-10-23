# Copy bot code review — October 23, 2026

Scope: manual review of copy execution, source sizing, watcher operation and portfolio accounting; full automated test suite. Bots remain stopped. No strategy changes were made during this review.

## Findings

- **High: no order-book timestamp freshness gate.** `scan` verifies asset and condition identity, while execution validates prices, liquidity and spread. It does not reject a book based on its server timestamp. A successful HTTP response alone does not establish quote freshness. Add timestamp checks and stale/missing timestamp tests before relying on fills as realistic execution evidence.
- **High: source trades can be 120 seconds old.** This is especially consequential for five-minute markets. The five-second polling target does not guarantee five-second source-to-copy latency. Measure the full delay and evaluate stricter market-specific admission rules with chronological replay.
- **High: no account-wide daily loss cutoff or automatic stop-loss for copy positions.** Per-market spending limits constrain purchases, but exits depend on observed source sells or settlement. Open positions can lose value while the watcher is paused. A failed or stale source feed does not close holdings.
- **Medium: fees are estimates.** Copy buys reserve 2% and sales/valuations reserve 2%, rather than fetching each market's actual fee schedule. Modeled PnL can overstate or understate executable results.
- **Medium: spending limits use cumulative historical purchases.** Token and condition caps subtract lifetime buys rather than remaining open cost. Closed positions do not restore those particular market budgets, even with compounding enabled. This reduces re-entry and should be documented or deliberately redesigned.
- **Medium: position sizing is a proxy.** Relative observed gross buys are not trader confidence, net exposure or hedge-adjusted risk. The sampled source history can be incomplete.

## Existing safeguards checked

Stable event IDs and duplicate suppression; singleton watcher lock; cash and condition purchase limits; identity checks; bid/ask validation and entry drag gate; minimum order size; source exit retries; explicit binary winner validation; stale valuation handling; and retaining held wallets for source exits when the selected watchlist changes.

## Validation and publication checks

- All 124 automated tests passed with the configured Python runtime.
- All 141 reachable historical Git blobs were checked for selected Robinhood, GitHub and OpenAI credential patterns and private-key headers: no matches. This pattern scan is not an exhaustive secret-detection guarantee.
- No historical `.env`, SQLite ledger, `.pem` or `.key` filenames were found by the publication scan. Runtime reports, ledgers, credentials, logs and PID files are excluded by `.gitignore`.
- The tracked sample watchlist contains public research wallet addresses.

Passing tests establishes the tested software behavior, not profitability. These findings remain open; this review does not establish a trading advantage.
