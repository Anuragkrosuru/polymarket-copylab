import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from copylab.__main__ import Ledger
from copylab.portfolio import bid_value, refresh, settlement_price, summary


class PortfolioTests(unittest.TestCase):
    def setUp(self):
        self.ledger = Ledger(':memory:')
        self.now = 1700000000
        trade = dict(timestamp=self.now, side='BUY', price=.5, asset='a',
                     conditionId='condition', title='Example', outcome='Yes')
        self.ledger.process(trade, {'asks': [{'price': '.5', 'size': '100'}]}, self.now)
        self.market = dict(condition_id='condition', closed=False, tokens=[
            dict(token_id='a', winner=False), dict(token_id='b', winner=False)])
        self.book = dict(asset_id='a', bids=[dict(price='.6', size='100')])

    def fetch(self, url, **params):
        return self.market if '/markets/' in url else self.book

    def test_unrealized_pnl_includes_entry_and_exit_reserves(self):
        refresh(self.ledger, self.fetch, self.now)
        report = summary(self.ledger, self.now)
        shares = (2/1.02)/.5
        self.assertAlmostEqual(38, report['cash'])
        self.assertAlmostEqual(shares*.6*.98-2, report['unrealized_pnl'])
        self.assertAlmostEqual(report['equity']-40, report['total_pnl'])
        self.assertEqual(0, report['realized_pnl'])

    def test_win_and_loss_settle_once_and_survive_restart(self):
        for winner in (True, False):
            with self.subTest(winner=winner), tempfile.TemporaryDirectory() as directory:
                ledger = Ledger(str(Path(directory)/'test.sqlite3'))
                self.ledger.db.backup(ledger.db)
                self.market['closed'] = True
                self.market['tokens'][0]['winner'] = winner
                self.market['tokens'][1]['winner'] = not winner
                refresh(ledger, self.fetch, self.now)
                first = summary(ledger, self.now)
                ledger.db.close()
                ledger = Ledger(str(Path(directory)/'test.sqlite3'))
                refresh(ledger, self.fetch, self.now+1)
                second = summary(ledger, self.now+1)
                payout = (2/1.02)/.5 if winner else 0
                self.assertAlmostEqual(38+payout, second['cash'])
                self.assertAlmostEqual(payout-2, second['realized_pnl'])
                self.assertEqual(first['cash'], second['cash'])
                self.assertEqual(1, second['settled_positions'])
                self.assertEqual([], second['positions'])
                late_trade = dict(timestamp=self.now+2, side='BUY', price=.5, asset='a')
                self.assertEqual('already settled', ledger.process(late_trade, {'asks': []}, self.now+2))
                ledger.db.close()

    def test_closed_without_winner_is_not_settled(self):
        self.market['closed'] = True
        refresh(self.ledger, self.fetch, self.now)
        self.assertEqual(0, summary(self.ledger, self.now)['settled_positions'])
        self.assertIsNone(settlement_price(self.market, 'a'))

    def test_missing_and_stale_quotes_never_become_profit(self):
        refresh(self.ledger, self.fetch, self.now)
        self.assertIsNone(summary(self.ledger, self.now+121)['total_pnl'])
        def failing(url, **params):
            raise OSError('Network unavailable')
        refresh(self.ledger, failing, self.now+1)
        report = summary(self.ledger, self.now+1)
        self.assertIsNone(report['equity'])
        self.assertEqual(1, report['unpriced_positions'])
        self.assertAlmostEqual(38, report['cash'])

    def test_thin_and_empty_bids(self):
        value, note = bid_value({'bids': [dict(price='.6', size='1')]}, 4)
        self.assertAlmostEqual(.6*.98, value)
        self.assertIn('unfilled', note)
        self.assertEqual(0, bid_value({'bids': []}, 4)[0])

    def test_wrong_token_never_values_position(self):
        self.book['asset_id'] = 'wrong'
        refresh(self.ledger, self.fetch, self.now)
        self.assertIsNone(summary(self.ledger, self.now)['total_pnl'])

    def test_existing_ledger_migrates_without_losing_fills(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory)/'legacy.sqlite3')
            db = sqlite3.connect(path)
            db.execute('CREATE TABLE fills(id TEXT PRIMARY KEY, day TEXT, asset TEXT, price REAL, shares REAL, cost REAL, source TEXT)')
            db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?,?)',
                       ('old', '2026-10-19', 'a', .5, 2, 1.02, json.dumps(dict(conditionId='condition'))))
            db.commit()
            db.close()
            ledger = Ledger(path)
            refresh(ledger, self.fetch, self.now)
            self.assertEqual(1, summary(ledger, self.now)['fills'])
            self.assertAlmostEqual(38.98, summary(ledger, self.now)['cash'])
            ledger.db.close()


if __name__ == '__main__':
    unittest.main()
