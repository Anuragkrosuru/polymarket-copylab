# Robinhood / Polymarket BTC 15-minute paper experiment

This is a separate $40 simulated account. No Robinhood or Polymarket orders are
submitted. The claim that Robinhood leads by ten seconds remains a hypothesis.
No other exchange is silently substituted for Robinhood.

## Connection

Open `scripts/connect-robinhood.command` on this Mac, or run it from Terminal.
It shows the public signing key and asks for the API key returned by Robinhood.
At https://robinhood.com/account/crypto select Add key and enable **Read crypto
quotes only**. Register the displayed public key. Enter the returned API key at
the local hidden prompt. Do not paste credentials into chat. The collector reloads
configuration each cycle; no restart is required.

The private signing seed and API key are saved in `.env.robinhood.json`, mode 600,
excluded from Git. They remain on this machine. The adapter uses only the fixed
GET `/api/v1/crypto/marketdata/best_bid_ask/?symbol=BTC-USD` endpoint. It does not
read holdings or submit any orders. The optional signing dependency is
`cryptography`; the Mac runner uses the bundled Python that already includes it.
For other installations use `python3 -m pip install '.[robinhood]'`.

## Experiment rules

- Discover the current `btc-updown-15m-{interval_start_unix}` market. Validate
  ordinary Up/Down tokens, current interval, explicit crypto fee schedule and
  the current 60-second Chainlink TWAP resolution configuration. Changed rules
  pause entries pending review. The market creation time is not the interval start.
- Poll Robinhood midpoint and both outcome books concurrently, targeting once
  per second. Record source timestamps, receipt times and quote books in SQLite.
  This is an HTTP prototype, not a low-latency WebSocket execution engine.
- Require Robinhood quote age at most three seconds, book timestamp age at most
  five seconds, valid identities, noncrossed prices and visible depth. Cycles
  longer than three seconds disable entry signals. Clock synchronization matters.
- Compare with the nearest observation 8–12 seconds ago in the same interval.
  A BTC move of at least 0.03% (three basis points) queues the matching direction
  only if its ask has moved no more than one cent, spread is at most three cents,
  and ask is between 10 and 65 cents. These thresholds are experimental choices,
  not calibrated estimates of fair probability or evidence of positive expectancy.
- Wait at least one second before filling against a NEW book. Cancel signals
  older than three seconds or asks that worsen by more than one cent. No initial
  quote is retroactively filled. Enforce venue minimum sizes (at least five shares).
- Walk ask depth, apply the market fee schedule `shares * rate * p * (1-p)`
  per level, round fees to five decimal places, and add a half-cent adverse cost
  per share. Sale proceeds walk bids, subtract fees and half a cent per share.
  Fee modeling is a cash-equivalent approximation; live token-fee accounting,
  network latency, queue competition and order rejection can change results.
- At most $3 per position, $12 UTC daily purchases and $40 lifetime purchases;
  no reinvestment increases the lifetime cap. One open position at a time and one
  trade per interval. Minimum share sizes may prevent entry under the $3 cap.
  Immediate estimated liquidation value must be at least 85% of purchase cost.
- Request exit ten seconds after entry, or sooner at an estimated 20% position
  loss. A $4 decline from the day's first known equity pauses entries for the rest
  of the UTC day and requests exits. The baseline and pause survive restarts.
  Missing full-depth valuations make equity unknown and prevent entries. These
  are triggers, not guaranteed loss caps: missing bids can prevent exits. Exit
  servicing continues when Robinhood fails or the current market lacks quotes.
- Do not enter in the first twelve or last thirty seconds of an interval.
  Unfilled exits remain exposed; closed markets settle only after matching token
  IDs and exactly one confirmed winner. Robinhood never determines settlement.

This tests short-term repricing within 15-minute markets, rather than assuming a
spot-price lead predicts the final winning outcome. Samples must be analyzed over
many intervals, including no-trade periods and failed/stale requests, before any
claim that an executable lead exists. There is no historical backtest here.

## Operation and reports

`python3 -m copylab.btc15` starts the collector. Without a key, it records
Polymarket quotes with Robinhood values null, keeps $40 untouched and explicitly
reports that entries are disabled. It never invents Robinhood prices.
`--once` performs one cycle; `--simulate` runs invented scenarios through the same
engine in fresh in-memory accounts. `btc15-synthetic-results.json` records those
examples separately from the live account.

`btc15.sqlite3` contains samples, pending signals, trades and daily risk state.
`reports/btc15/status.json` shows feed state, current decision, cycle duration,
errors, sample count, cash, equity and PnL. Missing values stay unknown.
`reports/btc15/trades.csv` and `trades.log` show trades only, including exits,
proceeds and realized gains/losses. Raw observations are in SQLite, not the trade
log. Keep the computer awake; these are local processes. Raw quote history grows
while the process runs; no automatic historical-data deletion occurs.

## Research checked October 19, 2026

- [Robinhood official API documentation](https://docs.robinhood.com/crypto/trading/):
  Ed25519 request signing, quote schema and timestamps. The adapter uses v1
  read-only quotes, supported alongside v2 per Robinhood's help page.
- [Robinhood API setup](https://robinhood.com/us/en/support/articles/crypto-api/):
  credentials and action-scoped permissions.
- [Current sampled market rules](https://polymarket.com/event/btc-updown-15m-1792445400):
  BTC 5:30–5:45 PM ET uses the Chainlink BTC/USD 60-second TWAP source. Gamma's
  matching metadata explicitly identifies `btc-15m-twap-60`, rate 0.07, exponent 1.
  The bot fetches and validates each interval rather than assuming these persist.
- [Polymarket fees](https://docs.polymarket.com/trading/fees): taker formula and
  precision. [Real-time data](https://docs.polymarket.com/market-data/realtime-data)
  documents streaming options for a later measured latency improvement.
