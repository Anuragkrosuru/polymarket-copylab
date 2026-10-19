import tempfile
import unittest
from pathlib import Path
from copylab.__main__ import Ledger
from copylab.arbitrage import Account
from copylab.btc15 import Engine,simulate_trend
from copylab.paper_config import progress
from copylab.portfolio import summary


class PaperFundingTests(unittest.TestCase):
    def test_100_cash_goal_and_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path=str(Path(folder)/'copy.sqlite3')
            ledger=Ledger(path,initial_cash=100,goal=1000,compound=True)
            self.assertEqual(summary(ledger)['cash'],100)
            self.assertEqual(summary(ledger)['remaining_to_goal'],900)
            ledger.db.close();ledger=Ledger(path)
            self.assertEqual(summary(ledger)['initial_cash'],100)
            with self.assertRaises(ValueError):Ledger(path,initial_cash=200)

    def test_cannot_rebase_legacy_fills(self):
        with tempfile.TemporaryDirectory() as folder:
            path=str(Path(folder)/'old.sqlite3');ledger=Ledger(path)
            # Mimic an old pre-settings ledger with recorded purchases.
            with ledger.db:
                ledger.db.execute('DROP TABLE paper_settings')
                ledger.db.execute("INSERT INTO fills VALUES ('a','day','token',.5,2,1,'{}')")
            ledger.db.close()
            with self.assertRaises(ValueError):Ledger(path,initial_cash=100)

    def test_compounding_reuses_cash_without_lifetime_purchase_ceiling(self):
        a=Account(':memory:',initial_cash=100,goal=1000,compound=True)
        q=dict(cost=14,shares_each=14.2,expected_payout=14.2,expected_gain=.2,leg_costs=[7,7])
        for i in range(9):
            m=dict(condition_id=str(i),tokens=[dict(token_id='a'),dict(token_id='b')])
            self.assertTrue(a.enter(m,q,now=1700000000+i*86400))
            a.settle(str(i),dict(m,closed=True,tokens=[dict(token_id='a',winner=True),dict(token_id='b',winner=False)]))
        self.assertGreater(a.state()['lifetime_spend'],100)
        self.assertAlmostEqual(a.state()['cash'],101.8)
        self.assertFalse(a.state()['goal_reached'])

    def test_scaled_caps_and_cash_guard(self):
        a=Account(':memory:',initial_cash=100,goal=1000,compound=True)
        q=dict(cost=14,shares_each=14.2,expected_payout=14.2,expected_gain=.2,leg_costs=[7,7])
        for i in range(7):
            self.assertTrue(a.enter(dict(condition_id=str(i)),q,now=1700000000+i*86400))
        self.assertEqual(a.state()['cash'],2)
        self.assertFalse(a.enter(dict(condition_id='cash-guard'),q,now=1700000000+8*86400))
        self.assertEqual(a.state()['cash'],2)

    def test_btc_100_scenarios_start_at_100_and_do_not_assume_goal(self):
        scenarios=simulate_trend(initial_cash=100,goal=1000,compound=True)['scenarios']
        for s in scenarios:
            self.assertEqual(s['initial_cash'],100)
            self.assertEqual(s['goal'],1000)
            self.assertFalse(s['goal_reached'])
        self.assertGreater(scenarios[0]['cash'],100)
        self.assertLess(scenarios[1]['cash'],100)
        self.assertGreater(scenarios[0]['trades'][0]['cost'],3)
        self.assertLessEqual(scenarios[0]['trades'][0]['cost'],7.5)

    def test_goal_unknown_stays_unknown(self):
        p=progress(dict(initial_cash=100,goal=1000,compound=True),None)
        self.assertIsNone(p['goal_progress_percent'])
        self.assertIsNone(p['remaining_to_goal'])
        self.assertFalse(p['goal_reached'])
