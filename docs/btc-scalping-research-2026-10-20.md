# BTC scalping research and paper risk revision

The previous BTC strategy lost $6.79921 on eight closed paper trades. Replaying
all frozen recorded quotes reproduces those eight entries and their realized
profit or loss exactly. That verifies the replay against this run; it does not
verify executable returns or predict future performance.

## Research and diagnosis

[Polymarket's official fee documentation](https://docs.polymarket.com/trading/fees)
specifies the price-dependent taker formula. At an outcome price of $0.50, the
crypto coefficient 0.07 corresponds to approximately 3.5% of purchase notional per
side, before spread and the model's adverse execution allowance. It is not a flat
7% charge on all trades. Small price movements often cannot pay for a round trip.
The previous guard allowed up to 15% immediate liquidation drag against a 5% net
profit target. Its 20% loss trigger and polling gaps permitted large losses.

[Cont Kukanov and Stoikov](https://arxiv.org/abs/1011.6402) and
[Gould and Bonart](https://arxiv.org/abs/1512.03492) study order-flow/queue imbalance
and very short price moves in equity order books. These motivate measuring visible
book support instead of relying solely on BTC moving averages. They do **not**
establish an edge in these prediction markets. A snapshot queue can be canceled,
spoofed or unrelated to subsequent fills; it is not full order-flow imbalance.

[Novy-Marx's research on multiple trading signals](https://www.nber.org/papers/w21329)
explains why adding indicators and selecting attractive backtests can overfit.
This revision is one explicitly specified candidate, without a parameter grid
search. Temporal validation is small and was inspected during development. It is
not an independent forward trial, and no claim of positive expectancy is made.

Current BTC 15-minute markets resolve using Chainlink's 60-second TWAP, validated
by the collector for every interval. A spot trend does not guarantee the final
winning outcome. This strategy aims for a short repricing trade, not a settlement
prediction. The collector uses only read-only Robinhood quotes and public books.

## Revision btc scalp v2

- Keep the existing BTC trend confirmation and one-second delayed entry recheck.
- Require immediate estimated liquidation drag at most 7%, including fees and
  adverse costs. Cheap outcomes with expensive round trips are rejected.
- Require near-touch bid depth imbalance at least 0.10. Current outcome bid must
  not fall over the previous approximately five seconds or jump over two cents.
  These guards are checked when a signal is queued and again before its fill.
- Reduce maximum position cost from 7.5% to 5% of starting cash plus realized PnL.
  Available cash, full depth, minimum venue size and one open position still apply.
- Retain the 5% **net** profit target. Reduce the position loss trigger from 20%
  to 10%, and maximum hold from two minutes to one minute. Exit when BTC moves one
  basis point against the entry signal, or the confirmed trend reverses.
- Once a trade has shown at least 3% net profit, protect it when proceeds fall two
  percentage points from their peak or below a 0.5% profit floor. Peaks persist
  across restarts. Prices can gap past any trigger, including below break-even.
- Allow at most two entries per interval. Wait two minutes after a loss and
  fifteen minutes after three consecutive v2 losses. Existing pullback checks and
  the twenty-second post-profit cooldown remain.
- Daily purchase turnover may reach 100% of starting capital plus realized PnL,
  reusing available cash. This replaces the previous 30% turnover cap, which could
  exhaust the day's buying allowance in four trades. It does not permit leverage
  or simultaneous exposure beyond the smaller position cap. The fixed $10 daily
  loss pause on this $100 experiment is unchanged. Existing day spending and
  losses are retained, not reset for this policy change.

The limits are modeled controls, not maximum possible losses or guaranteed exits.
Unknown/stale quotes block entries; exits still run when Robinhood is unavailable.
Each new trade stores its entry policy and diagnostics. Old v1 trades keep their
original exit rules and remain in the account's cumulative results.

## Recorded quote validation

The frozen dataset contains 9,223 observations in 33 market intervals, covering
October 19 through October 20, 2026. Historical metadata was verified against
Gamma using explicit closed/active filters. Only instrument identity, interval,
minimum size, resolution source and fee fields were used; future outcome prices
and winners were not used for signals or fills.

The first 22 intervals are the development segment and the last 11 the temporal
validation segment. Each strategy/segment starts from an independent $100 account.
The replay uses the same engine and fee model, validates original book timestamps,
waits for a later recorded quote, and never interpolates missing quotes. Gaps,
market impact, queue competition and real order failures remain limitations.
Unknown final equity stays unknown; drawdown only measures priced observations.

The prior strategy lost $5.78 on six development trades. V2 lost $0.69 on three.
On the later segment, the prior strategy lost $4.70 on four trades; v2 entered
none. **Avoiding those trades is reduced exposure, not proof of a profitable
strategy.** V2 remains experimental paper trading. The dataset is short and
contains sparse periods; these counts cannot establish a reliable win rate.

Machine-readable results are in `btc-scalp-replay-results.json`. Reproduce against
the retained live quote history, capped at the frozen observation ID:

```sh
python3 -m copylab.btc_replay \
  --database experiments/100-to-1000/btc15.sqlite3 \
  --metadata reports/btc15/research-markets-2026-10-20.json \
  --end-id 9223 \
  --output btc-scalp-replay-results.json
```

No account is reset or credited with replay profits. The live $100 experiment
keeps its actual recorded loss and $1,000 tracked target. Synthetic examples are
separate and clearly labeled. No real orders or paid AI APIs are used.
