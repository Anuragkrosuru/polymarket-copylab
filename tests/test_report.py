import json
import tempfile
import unittest
from pathlib import Path

from copylab.__main__ import Ledger
from copylab.portfolio import refresh
from copylab.report import collect, export


class ReportTests(unittest.TestCase):
    def test_every_event_reconciles_to_cash_and_realized_profit(self):
        ledger = Ledger(':memory:')
        now = 1700000000
        trade = dict(transactionHash='buy-tx', proxyWallet='wallet', timestamp=now,
                     side='BUY', price=.5, size=100, asset='a', conditionId='c', title='Test market', outcome='Yes')
        book = dict(asks=[dict(price=.5,size=100)], bids=[dict(price=.49,size=100)])
        ledger.process(trade,book,now)
        ledger.process(dict(trade, transactionHash='sell-tx',side='SELL',price=.6,timestamp=now+1),dict(book,bids=[dict(price=.6,size=1)]),now+1)
        market = dict(condition_id='c',closed=True,tokens=[dict(token_id='a',winner=True),dict(token_id='b',winner=False)])
        refresh(ledger,lambda *a,**k:market,now+2)
        report=collect(ledger,now+2)
        self.assertEqual(['BUY','SELL','SETTLEMENT'],[t['type'] for t in report['trades']])
        self.assertAlmostEqual(40+sum(t['cash_change'] for t in report['trades']),report['account']['cash'])
        self.assertAlmostEqual(sum(t['realized_pnl'] or 0 for t in report['trades']),report['account']['realized_pnl'])
        self.assertEqual('sell-tx',report['trades'][1]['source_transaction'])
        self.assertGreater(report['fee_reserve_total'],0)
        with tempfile.TemporaryDirectory() as d:
            export(ledger,d,now=now+2)
            text=(Path(d)/'trade-history.csv').read_text()
            self.assertIn('SELL',text)
            self.assertIn('SETTLEMENT',text)
            self.assertEqual(3,len(json.loads((Path(d)/'trade-report.json').read_text())['trades']))
        ledger.db.close()

    def test_empty_report_has_no_invented_trades(self):
        ledger=Ledger(':memory:')
        report=collect(ledger)
        self.assertEqual([],report['trades'])
        self.assertEqual(0,report['fee_reserve_total'])
        self.assertEqual(0,report['account']['total_pnl'])
        ledger.db.close()


if __name__ == '__main__':
    unittest.main()
