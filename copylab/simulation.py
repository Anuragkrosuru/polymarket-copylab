"""Deterministic integration scenarios, deliberately not a historical backtest."""
import json
import tempfile
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

from copylab.portfolio import refresh, summary

WALLET = '0x' + '1' * 40
BASE = 1792425600


def scenario(name, sell_prices, winners):
    from copylab.__main__ import Ledger, scan
    with tempfile.TemporaryDirectory() as directory:
        ledger = Ledger(str(Path(directory) / 'scenario.sqlite3'))
        now = [BASE]
        trades = []
        books = {str(i): dict(asset_id=str(i), asks=[dict(price='.5', size='100')],
                             bids=[dict(price='.49', size='100')]) for i in range(5)}
        markets = {f'condition-{i}': dict(condition_id=f'condition-{i}', closed=False,
                   tokens=[dict(token_id=str(i), winner=False),
                           dict(token_id=f'opposite-{i}', winner=False)]) for i in range(5)}

        def fetch(url, **params):
            if url.endswith('/trades'):
                return trades
            if url.endswith('/book'):
                return books[params['token_id']]
            return markets[url.split('/')[-1]]

        def report(stage):
            refresh(ledger, fetch, now[0])
            return dict(stage=stage, timestamp=now[0], **summary(ledger, now[0]))

        with redirect_stdout(StringIO()):
            scan(ledger, WALLET, fetch, lambda: now[0])
            now[0] += 1
            for i in range(5):
                trades.append(dict(transactionHash=f'buy-{i}', proxyWallet=WALLET,
                    timestamp=now[0], side='BUY', price=.5, size=100, asset=str(i),
                    conditionId=f'condition-{i}', title=f'Synthetic market {i+1}', outcome='Yes'))
            scan(ledger, WALLET, fetch, lambda: now[0])
            snapshots = [report('4 buys; fifth blocked by daily spending cap')]
            # Repeating the same feed must not add fills.
            scan(ledger, WALLET, fetch, lambda: now[0])
            assert summary(ledger, now[0])['fills'] == 4
            now[0] += 60
            trades.clear()
            for i, price in enumerate(sell_prices):
                books[str(i)]['bids'] = [dict(price=price, size=100)]
                trades.append(dict(transactionHash=f'sell-{i}', proxyWallet=WALLET,
                    timestamp=now[0], side='SELL', price=price, size=100, asset=str(i),
                    conditionId=f'condition-{i}'))
            scan(ledger, WALLET, fetch, lambda: now[0])
            snapshots.append(report('source sells copied'))
            now[0] += 3600
            for i, winner in zip((2, 3), winners):
                markets[f'condition-{i}']['closed'] = True
                markets[f'condition-{i}']['tokens'][0]['winner'] = winner
                markets[f'condition-{i}']['tokens'][1]['winner'] = not winner
            snapshots.append(report('remaining positions settled'))
            final = snapshots[-1]
            assert final['open_cost'] == 0
            assert final['sales'] == 2 and final['settled_positions'] == 2
            assert abs(final['equity'] - 40 - final['realized_pnl']) < 1e-8
            assert abs(report('repeat settlement')['cash'] - final['cash']) < 1e-8
        ledger.db.close()
        return dict(name=name, synthetic=True, final_equity=final['equity'],
                    total_pnl=final['total_pnl'], snapshots=snapshots)


def run(output):
    scenarios = [scenario('Favorable constructed outcomes', (.65, .60), (True, True)),
                 scenario('Mixed constructed outcomes', (.51, .49), (True, False)),
                 scenario('Adverse constructed outcomes', (.30, .25), (False, False))]
    result = dict(kind='SYNTHETIC_INTEGRATION_TEST',
                  generated=datetime.now(timezone.utc).isoformat(),
                  disclaimer='Invented prices and outcomes. Tests software behavior; does not measure or predict trading profitability.',
                  assumptions=dict(initial_cash=40, buy_cost=2, daily_spend_cap=8,
                                   entry_reserve=.02, exit_reserve=.02, source_entry_price=.5,
                                   policy='Source SELL requests full exit of matching copied holdings.'),
                  scenarios=scenarios)
    Path(output).write_text(json.dumps(result, indent=2) + '\n')
    print('SYNTHETIC scenarios — invented prices, not a profitability backtest:')
    for item in scenarios:
        print('%s: equity $%.2f; P/L $%+.2f' % (item['name'], item['final_equity'], item['total_pnl']))
    print('Saved:', str(Path(output).resolve()))
    return result
