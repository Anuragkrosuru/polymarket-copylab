import json
import sqlite3
import tempfile
import unittest
from datetime import datetime,timezone
from pathlib import Path
from copylab.__main__ import Ledger
from copylab.report import collect
from copylab.combined_report import combine


class CombinedReportTests(unittest.TestCase):
    def setup_report(self,folder,open_btc=False,open_pair=False,stale=False):
        root=Path(folder);reports=root/'reports';reports.mkdir()
        for name in ['btc15','arbitrage']:(reports/name).mkdir()
        now=1700000000
        ledger=Ledger(str(root/'paper.sqlite3'))
        c=sqlite3.connect(str(root/'btc15.sqlite3'))
        c.execute('CREATE TABLE trades(id INTEGER PRIMARY KEY,opened REAL,market TEXT,direction INTEGER,shares REAL,cost REAL,closed REAL,proceeds REAL,exit_reason TEXT,strategy TEXT)')
        m=json.dumps(dict(title='BTC interval'))
        c.execute('INSERT INTO trades VALUES (1,?,?,?,?,?,?,?,?,?)',(now-10,m,1,5,2,None if open_btc else now-1,None if open_btc else 2.2,'target','trend'));c.commit();c.close()
        c=sqlite3.connect(str(root/'arbitrage.sqlite3'))
        c.execute('CREATE TABLE pairs(condition TEXT,opened REAL,market TEXT,quote TEXT,payout REAL)')
        q=dict(cost=4.8,shares_each=5,entry_prices=[.4,.55],leg_costs=[2,2.8],expected_payout=5,expected_gain=.2)
        m=dict(question='Pair market',tokens=[dict(outcome='Yes'),dict(outcome='No')])
        c.execute('INSERT INTO pairs VALUES (?,?,?,?,?)',('pair',now-5,json.dumps(m),json.dumps(q),None if open_pair else 5));c.commit();c.close()
        checked=datetime.fromtimestamp(now-(121 if stale else 0),timezone.utc).isoformat()
        (reports/'btc15'/'status.json').write_text(json.dumps(dict(updated_utc=checked,cash=38 if open_btc else 40.2,
            equity=40.1 if open_btc else 40.2,trades=[dict(id=1,closed=None if open_btc else now-1)],decision='test')))
        (reports/'arbitrage'/'status.json').write_text(json.dumps(dict(updated_utc=checked,checked=100,universe=100,errors=0)))
        report=combine(collect(ledger,now),root,reports,now)
        ledger.db.close()
        return report

    def test_totals_and_each_trade_reconcile(self):
        with tempfile.TemporaryDirectory() as folder:
            r=self.setup_report(folder)
            self.assertAlmostEqual(r['combined']['cash'],120.4)
            self.assertAlmostEqual(r['combined']['realized_pnl'],.4)
            self.assertAlmostEqual(120+sum(t['cash_change'] for t in r['trades']),r['combined']['cash'])
            self.assertEqual(len(r['trades']),4)
            self.assertEqual(len({t['event_id'] for t in r['trades']}),4)
            self.assertEqual(r['trades'][-1]['timestamp'],None)

    def test_open_pair_expected_gain_is_not_realized_or_equity(self):
        with tempfile.TemporaryDirectory() as folder:
            r=self.setup_report(folder,open_pair=True)
            self.assertIsNone(r['combined']['equity'])
            self.assertAlmostEqual(r['combined']['realized_pnl'],.2)
            self.assertAlmostEqual(r['combined']['expected_arbitrage_gain'],.2)

    def test_stale_btc_open_quote_makes_combined_equity_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            r=self.setup_report(folder,open_btc=True,stale=True)
            self.assertIsNone(r['combined']['equity'])
            self.assertIsNone(r['combined']['unrealized_pnl'])
            self.assertTrue(r['bots'][2]['stale'])

    def test_fresh_btc_open_value_is_separate_from_realized(self):
        with tempfile.TemporaryDirectory() as folder:
            r=self.setup_report(folder,open_btc=True)
            self.assertAlmostEqual(r['combined']['equity'],120.3)
            self.assertAlmostEqual(r['bots'][2]['unrealized_pnl'],.1)
            self.assertEqual(r['bots'][2]['realized_pnl'],0)

    def test_missing_bot_is_unknown_not_fake_zero_profit(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger=Ledger(':memory:')
            r=combine(collect(ledger),Path(folder),Path(folder),1700000000)
            self.assertIsNone(r['combined']['equity'])
            self.assertFalse(r['bots'][1]['available'])
            self.assertEqual(r['trades'],[])
            self.assertFalse((Path(folder)/'arbitrage.sqlite3').exists())
