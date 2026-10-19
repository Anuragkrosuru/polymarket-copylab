# User supplied sources reviewed October 19 2026

## Exact recovered references

- Polymarket profile: https://polymarket.com/@automatedaitradingbot
- Setup article: https://www.quantvps.com/blog/setup-polymarket-trading-bot
- Exact Reddit post, resolved from the supplied short link: https://www.reddit.com/r/SideProject/comments/1qz11e5/polymarket_trading_bot_exploiting_market/
- Author linked repository, read only: https://github.com/jaredzwick/polymarket-trading-bot

The other Reddit search results about AI and fifteen minute bots were not the supplied post and were not treated as that source.

## Shared wallet assessment

Verified the exact profile against Polymarket's public profile API: automatedAItradingbot is 0xd8f8c13644ea84d62e1ec88c5d1215e436eb0f11. A public search initially returned automatedAItradingbot2, which is a different wallet and was excluded.

The profile displays about $88.9K total PnL and 3433 markets traded. This is not proof of a currently copyable strategy. The same bounded 30 day screening used by CopyLab found zero recent trade transactions, 19 closed markets and sampled closed-market realized PnL of -$473.27. The latest trade returned by the public history endpoint was July 14, 2026 UTC. These are endpoint observations, not a complete account audit. Raw assessment is in the local ignored reports/reference-trader-screen.json.

Decision: observation only. Do not bypass the activity, profit, concentration, price or size rules because a profile has a large historical headline profit. No automatic copying of this wallet was enabled.

## Setup article

The setup article concerns public data, paper ledgers, executable depth, failure recovery and deployment. It is useful implementation context, but does not establish a profitable strategy. No hosting purchase or third-party bot installation was made. Technical API behavior should continue to be checked against current Polymarket documentation, not copied verbatim from third-party setup examples.

## Arbitrage idea

The exact Reddit post proposes buying a complete outcome set below its payout. Its author explicitly reports that their tested opportunities had not covered the spread. The linked repository was inspected, not downloaded or executed.

A future scanner must compare executable asks for matched quantities across an exhaustive, mutually exclusive outcome set with the actual settlement payoff, fees, depth and minimum sizes. Displayed probabilities summing below one are insufficient. Separate leg executions can leave directional exposure. Any experiment should use its own paper ledger; its results must not be mixed into copy trading.

Primary venue references: https://docs.polymarket.com/concepts/positions-tokens and https://docs.polymarket.com/trading/fees

## Profit targets

Do not infer a profitable exit target from these sources or the first few CopyLab trades. A prospective paper comparison could test source-following exits against predeclared net profit targets, using executable bid depth and exit costs rather than the displayed last price. Partial exits must respect the market minimum share size. Existing positions and source exit behavior were not changed during this source review.
