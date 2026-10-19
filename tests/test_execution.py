import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from copylab.__main__ import Ledger, get, scan
from copylab.portfolio import refresh, summary
from copylab.runtime import watcher_lock
from copylab.simulation import scenario


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.ledger = Ledger(':memory:')
        self.now = 1700000000
        self.wallet = '0x' + '1'*40
        self.trade = dict(transactionHash='tx', timestamp=self.now, side='BUY',
                          price=.5, size=100, asset='a', conditionId='c', proxyWallet=self.wallet)
        self.book = dict(asset_id='a', asks=[dict(price=.5, size=100)], bids=[dict(price=.6, size=100)])

    def test_market_minimum_prevents_impossible_small_order(self):
        book = dict(self.book, min_order_size='5')
        self.assertEqual('below market minimum order size', self.ledger.process(self.trade, book, self.now))
        self.assertEqual(0, summary(self.ledger, self.now)['fills'])

    def test_rejected_small_sale_leaves_holdings_unchanged(self):
        self.ledger.process(self.trade, self.book, self.now)
        sell = dict(self.trade, transactionHash='sell', side='SELL', price=.6)
        thin = dict(self.book, min_order_size=2, bids=[dict(price=.6, size=1)])
        self.assertEqual('below market minimum exit size', self.ledger.process(sell, thin, self.now))
        self.assertEqual(0, summary(self.ledger, self.now)['sales'])

    def test_invalid_book_rolls_back_seen_so_retry_can_succeed(self):
        sell = dict(self.trade, transactionHash='sell', side='SELL', price=.6)
        self.ledger.process(self.trade, self.book, self.now)
        with self.assertRaises(ValueError):
            self.ledger.process(sell, dict(self.book, bids=[dict(price='NaN', size=1)]), self.now)
        self.assertFalse(self.ledger.known(sell))
        self.assertIn('paper sell', self.ledger.process(sell, self.book, self.now))

    def test_changed_display_metadata_is_not_another_trade(self):
        self.ledger.process(self.trade, self.book, self.now)
        self.assertEqual('duplicate', self.ledger.process(dict(self.trade, title='Renamed', profileImage='new'), self.book, self.now))
        self.assertEqual(1, summary(self.ledger, self.now)['fills'])

    def test_sell_only_exits_matching_source_and_settles_remaining(self):
        self.ledger.process(self.trade, self.book, self.now)
        other = dict(self.trade, transactionHash='sell-other', side='SELL', proxyWallet='other')
        self.assertIn('no matching', self.ledger.process(other, self.book, self.now))
        sell = dict(self.trade, transactionHash='sell', side='SELL', price=.6)
        thin = dict(self.book, bids=[dict(price=.6, size=1)])
        self.assertIn('paper sell', self.ledger.process(sell, thin, self.now))
        market = dict(condition_id='c', closed=True, tokens=[dict(token_id='a', winner=True), dict(token_id='b', winner=False)])
        refresh(self.ledger, lambda *a, **k: market, self.now)
        r = summary(self.ledger, self.now)
        shares = (2/1.02)/.5
        self.assertAlmostEqual(38 + .6*.98 + shares-1, r['cash'])
        self.assertAlmostEqual(r['total_pnl'], r['realized_pnl'])
        self.assertEqual([], r['positions'])
        self.assertEqual('duplicate', self.ledger.process(sell, thin, self.now))

    def test_full_exit_and_sale_replay_after_restart(self):
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d)/'paper.sqlite3')
            ledger = Ledger(path)
            ledger.process(self.trade, self.book, self.now)
            sell = dict(self.trade, transactionHash='sell', side='SELL', price=.6)
            ledger.process(sell, self.book, self.now)
            report = summary(ledger, self.now)
            self.assertEqual([], report['positions'])
            self.assertAlmostEqual((2/1.02)/.5*.6*.98-2, report['sale_pnl'])
            ledger.db.close()
            ledger = Ledger(path)
            self.assertEqual('duplicate', ledger.process(sell, self.book, self.now))
            self.assertAlmostEqual(report['cash'], summary(ledger, self.now)['cash'])
            ledger.db.close()

    def test_source_sell_does_not_chase_a_low_bid(self):
        self.ledger.process(self.trade, self.book, self.now)
        sell = dict(self.trade, transactionHash='sell', side='SELL', price=.9)
        self.assertIn('no matching holding or acceptable', self.ledger.process(sell, self.book, self.now))
        self.assertEqual(0, summary(self.ledger, self.now)['sales'])

    def test_one_bad_trade_does_not_block_other_trades(self):
        with self.ledger.db:
            self.ledger.db.execute('INSERT INTO wallets VALUES (?,?)', (self.wallet, self.now-1))
        trades = [dict(self.trade, asset='bad', transactionHash='bad'), self.trade]
        def fetch(url, **params):
            if url.endswith('/trades'):
                return trades
            if params['token_id']=='bad':
                raise OSError('temporary failure')
            return self.book
        with contextlib.redirect_stdout(io.StringIO()):
            scan(self.ledger, self.wallet, fetch, lambda: self.now)
            scan(self.ledger, self.wallet, fetch, lambda: self.now)
        report = summary(self.ledger, self.now)
        self.assertEqual(1, report['fills'])
        self.assertIn('temporary failure', report['scans'][0]['error'])

    def test_startup_and_stale_history_are_skipped(self):
        with contextlib.redirect_stdout(io.StringIO()):
            scan(self.ledger, self.wallet, lambda *a, **k: [self.trade], lambda: self.now)
            scan(self.ledger, self.wallet, lambda *a, **k: [self.trade], lambda: self.now+130)
        self.assertEqual(0, summary(self.ledger, self.now)['fills'])

    def test_watch_lock_excludes_second_process(self):
        with tempfile.TemporaryDirectory() as d:
            with watcher_lock(str(Path(d)/'paper.sqlite3')):
                with self.assertRaisesRegex(ValueError, 'already running'):
                    with watcher_lock(str(Path(d)/'paper.sqlite3')):
                        self.fail('Second watcher acquired lock')

    def test_http_retries_transient_but_not_permanent_errors(self):
        temporary = HTTPError('https://example.com', 503, 'unavailable', {}, None)
        permanent = HTTPError('https://example.com', 404, 'not found', {}, None)
        with patch('copylab.__main__.urlopen', side_effect=temporary) as request, patch('copylab.__main__.time.sleep'):
            with self.assertRaises(HTTPError):
                get('https://example.com')
            self.assertEqual(3, request.call_count)
        with patch('copylab.__main__.urlopen', side_effect=permanent) as request:
            with self.assertRaises(HTTPError):
                get('https://example.com')
            self.assertEqual(1, request.call_count)

    def test_synthetic_scenarios_report_both_gains_and_losses(self):
        gain = scenario('gain', (.65, .6), (True, True))
        loss = scenario('loss', (.3, .25), (False, False))
        self.assertGreater(gain['total_pnl'], 0)
        self.assertLess(loss['total_pnl'], 0)
        self.assertTrue(gain['synthetic'])


if __name__ == '__main__':
    unittest.main()
