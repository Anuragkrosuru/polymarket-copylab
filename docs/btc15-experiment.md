# Robinhood BTC trend paper experiment

This is a separate $40 simulated account trading Polymarket's 15-minute BTC
Up/Down markets. It follows short-term trends and seeks a five-percent net return
on a position. It does not assume a ten-second Robinhood advantage or predict the
final winning outcome. No Robinhood or Polymarket orders are submitted.

## Current strategy

The default CLI strategy is `trend`. It records Robinhood midpoint and both
outcome books, targeting one-second polls. A candidate requires at least 30 fresh
observations spanning 45 seconds, with no observation gap longer than five seconds.
It confirms the same direction through all of these checks:

- At least one basis point (0.01%) of BTC movement over approximately 30 seconds.
- At least 0.3 basis points of continuation over approximately ten seconds.
- The ten-second average above the 45-second average for Up, or below for Down.
- Current BTC price beyond the fast average in that direction.
- At least four nonzero recent changes, with at least 55% in that direction.

These are experimental trend filters, not calibrated probabilities or evidence of
positive expectancy. Polymarket odds no longer have to stay unchanged. The ask
must be between $0.10 and $0.80 and the spread at most $0.03. The $3 position cap
and venue minimums can still prevent entry, especially on expensive outcomes.

A candidate waits at least one second before a fill is simulated at NEW asks.
The trend must still agree. Signals older than three seconds and asks worsening
by more than one cent are canceled. Fees and adverse execution costs are included;
immediate estimated sale proceeds must be at least 85% of purchase cost.

## Profit target and other exits

The bot requests an exit when available bid depth would return at least **105% of
total entry cost after modeled exit costs**. This is a 5% position profit target,
not a 5% BTC price move or five cents added to an outcome price. A small displayed
price increase may not cover fees and spreads. Actual simulated profit can exceed
the target when prices jump between observations.

Other exit triggers are a 20% position loss, confirmed opposite trend, two-minute
holding limit, or twenty seconds before the interval ends. The $4 daily equity-loss
pause also requests exits. Exit processing continues when Robinhood is unavailable.
If bids cannot fill the whole position, it remains open and the request retries on
later cycles; this prototype does not record partial exits. Missing valuations
prevent new entries. Targets and loss controls cannot guarantee a price or limit.

## Pullback and re-entry

After a sale, all new entries wait at least twenty seconds. Re-entering the same
market in the same direction additionally requires a post-exit BTC pullback of at
least 0.5 basis points from the observed favorable extreme and a recovery of at
least 0.25 basis points from the pullback extreme. The full trend checks must still
pass. An opposite confirmed direction can enter after the cooldown without the
same-direction pullback requirement.

At most four entries per interval and one open position at a time are allowed.
The $3 per-position, $12 UTC daily purchases, $40 lifetime purchases and $4 daily
loss pause remain. Gross purchases still consume those caps even when trades are
profitable, so this is a bounded experiment rather than unlimited reinvestment.
Daily baselines, trades, pending signals and observations survive restarts.
Existing legacy trades retain their old exit policy; migration preserves IDs and
history while allowing multiple later trades in the same interval.

No entries occur during the first twelve or final forty-five seconds of an
interval. Trend history usually imposes a longer initial wait. Closed markets
settle only after matching outcome IDs and one confirmed winner. Robinhood prices
never determine settlement.

## Data and execution model

The collector discovers the current `btc-updown-15m-{interval_start_unix}` market.
It validates ordinary Up/Down tokens, interval boundaries, explicit crypto fee
schedule and the current Chainlink 60-second TWAP resolution configuration.
Changed rules pause entries pending review. Market creation time is not interval
start. This is an HTTP polling prototype, not a low-latency execution engine.

Robinhood quote age must be at most three seconds; exchange book timestamp age
at most five seconds. Identity, finite levels, uncrossed books and depth are
checked. Cycles longer than three seconds disable entry signals. Clock
synchronization matters. Ask/bid depth is walked and the market fee formula
`amount * rate * p * (1-p)` is applied per level, rounded to five decimals. A
half-cent adverse cost per unit is added on entry and subtracted on exit. This is
a cash-equivalent approximation; actual token-fee accounting, competition, network
latency, partial fills and order rejection can change outcomes.

## Connection and operation

Open `scripts/connect-robinhood.command` on this Mac. It shows the public signing
key and asks for the API key returned by Robinhood. At
https://robinhood.com/account/crypto select Add key and enable **Read crypto quotes
only**. Register the displayed public key and enter the returned API key at the
local hidden prompt. Do not paste credentials into chat.

The private signing seed and API key are saved locally in `.env.robinhood.json`,
mode 600 and excluded from Git. The adapter only calls the fixed GET quote endpoint
and cannot submit orders. It reloads configuration each cycle. The bundled Python
includes the signing dependency; other installations can use
`python3 -m pip install '.[robinhood]'`.

`python3 -m copylab.btc15` runs the default trend collector. `--once` performs one
cycle, `--simulate` runs invented trend, profit-target, re-entry and loss examples
through the same engine in fresh $40 accounts. `--strategy lead` retains the old
three-basis-point latency experiment for comparison. Its rules require a move
while odds stay unchanged and request an exit after ten seconds; that is no
longer the default live policy.

Without a key, the collector records Polymarket quotes with Robinhood values null
and disables entries. It never invents live Robinhood prices. Keep the computer
awake; these are local processes.

## Reports

`btc15.sqlite3` contains raw observations and accounting. Readable reports in
`reports/btc15/` show prices and dollar amounts without quantities:

- `status.json`: strategy, current trend signal, decision, feed errors, cash,
  equity and PnL; unavailable valuations remain unknown.
- `trades.csv` and `trades.log`: every entry and exit, prices including modeled
  costs, dollars spent/received and realized gains or losses.

The status also retains rolling filter counts for the optional legacy lead
strategy. Those overlapping observations are not independent trades or returns.
Raw quote history grows while the collector runs; it is not automatically deleted.
Synthetic examples are saved separately from live performance.

## Official references checked October 19 2026

[Robinhood API documentation](https://docs.robinhood.com/crypto/trading/) covers
Ed25519 signing and quote timestamps; [API setup](https://robinhood.com/us/en/support/articles/crypto-api/)
covers credentials and action-scoped permissions.
[The sampled 15-minute market](https://polymarket.com/event/btc-updown-15m-1792445400)
uses Chainlink's 60-second BTC/USD TWAP. Each interval's rules and fees are fetched
and checked rather than assumed permanent.
[Polymarket fees](https://docs.polymarket.com/trading/fees) documents the taker
formula and precision. The trend thresholds and execution buffer above are our
paper-model choices, not recommendations from those sources.
