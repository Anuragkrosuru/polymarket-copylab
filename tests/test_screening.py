import json
import unittest
from pathlib import Path

from copylab.__main__ import Ledger
from copylab.screening import evaluate, history, validate_policy


class ScreeningTests(unittest.TestCase):
    def setUp(self):
        self.policy=json.loads(Path('screening-policy.json').read_text())
        self.now=1792440000
        self.wallet='0x'+'1'*40
        self.candidate=dict(wallet=self.wallet,name='Test trader',leaderboards={})
        self.trades=[dict(transactionHash=str(i),proxyWallet=self.wallet,timestamp=self.now-(i%7)*86400,
                          price=.5,side='BUY',asset='a') for i in range(70)]
        self.positions=[dict(proxyWallet=self.wallet,timestamp=self.now,asset=str(i),conditionId=str(i),
                             realizedPnl=10 if i<20 else -5) for i in range(30)]

    def evaluate(self,trades=None,positions=None):
        return evaluate(self.candidate,self.trades if trades is None else trades,
                        self.positions if positions is None else positions,self.policy,self.now)

    def test_repeat_performance_passes_core(self):
        row=self.evaluate()
        self.assertEqual('core',row['classification'])
        self.assertEqual(70,row['transactions'])
        self.assertEqual(30,row['closed_markets'])
        self.assertGreater(row['profit_ex_largest_winner'],0)

    def test_many_fills_in_one_transaction_do_not_meet_minimum(self):
        row=self.evaluate([dict(t,transactionHash='one') for t in self.trades])
        self.assertEqual(1,row['transactions'])
        self.assertEqual('observe',row['classification'])

    def test_one_big_win_goes_to_experimental_group(self):
        positions=[dict(p,realizedPnl=1000 if i==0 else -10) for i,p in enumerate(self.positions)]
        row=self.evaluate(positions=positions)
        self.assertEqual('experimental',row['classification'])
        self.assertLess(row['profit_ex_largest_winner'],0)
        self.assertEqual(1,row['largest_winner_share'])

    def test_insufficient_history_and_negative_results_are_not_core(self):
        self.assertEqual('observe',self.evaluate(self.trades[:1])['classification'])
        self.assertEqual('observe',self.evaluate(positions=[dict(p,realizedPnl=-1) for p in self.positions])['classification'])

    def test_opposite_outcomes_and_duplicate_positions_do_not_inflate_market_count(self):
        positions=[dict(p,conditionId='same') for p in self.positions]
        row=self.evaluate(positions=positions+positions)
        self.assertEqual(1,row['closed_markets'])
        self.assertEqual('observe',row['classification'])

    def test_history_caps_and_wrong_wallet_are_visible(self):
        row=evaluate(self.candidate,self.trades,self.positions,self.policy,self.now,True,True)
        self.assertTrue(row['trade_history_capped'])
        with self.assertRaisesRegex(ValueError,'wallet mismatch'):
            self.evaluate(positions=[dict(self.positions[0],proxyWallet='other')])

    def test_stale_activity_cannot_qualify(self):
        row=self.evaluate(trades=[dict(t,timestamp=t['timestamp']-2*86400) for t in self.trades])
        self.assertEqual('observe',row['classification'])

    def test_repeated_api_page_is_not_a_larger_history(self):
        with self.assertRaisesRegex(ValueError,'repeated'):
            history(lambda *a,**k:[{'timestamp':self.now}], 'trades',self.wallet,1,2,self.now-86400)

    def test_experimental_budget_cannot_increase_default_cap(self):
        ledger=Ledger(':memory:')
        trade=dict(self.trades[0],transactionHash='copy',size=100,conditionId='c')
        ledger.process(trade,{'asks':[dict(price=.5,size=100)]},self.now,max_cost=1)
        self.assertAlmostEqual(1,ledger.spend())
        ledger.db.close()

    def test_dynamic_watchlist_reloads_and_expired_screen_stops_entries(self):
        import tempfile
        from copylab.runtime import watch_targets
        ledger=Ledger(':memory:')
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'watchlist.json'
            config=dict(wallets=[self.wallet],generated=self.now,profiles={self.wallet:dict(max_cost=1,classification='experimental')})
            path.write_text(json.dumps(config))
            targets=watch_targets(ledger,[],path,self.now)
            self.assertEqual(1,targets[self.wallet]['max_cost'])
            self.assertEqual({},watch_targets(ledger,[],path,self.now+7201))
            trade=dict(self.trades[0],timestamp=self.now,transactionHash='holding',size=100,conditionId='c')
            ledger.process(trade,{'asks':[dict(price=.5,size=100)]},self.now)
            path.write_text(json.dumps(dict(wallets=[],generated=self.now)))
            targets=watch_targets(ledger,[],path,self.now)
            self.assertFalse(targets[self.wallet]['allow_buys'])
        ledger.db.close()

    def test_watch_command_uses_dynamic_targets_without_startup_replay(self):
        import contextlib
        import io
        import tempfile
        from unittest.mock import patch
        from copylab.__main__ import main
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'watchlist.json'
            path.write_text(json.dumps(dict(wallets=[self.wallet])))
            argv=['copylab','--db',str(Path(directory)/'paper.sqlite3'),'watch','--wallet-file',str(path),'--once']
            with patch('sys.argv',argv), patch('copylab.__main__.get',return_value=[]), contextlib.redirect_stdout(io.StringIO()):
                main()
            ledger=Ledger(str(Path(directory)/'paper.sqlite3'))
            self.assertEqual(1,ledger.db.execute('SELECT COUNT(*) FROM scans').fetchone()[0])
            self.assertEqual(0,ledger.spend())
            ledger.db.close()

    def test_discovery_deduplicates_boards_and_publishes_all_assessed_wallets(self):
        import contextlib
        import io
        import tempfile
        from copylab.screening import discover
        policy=dict(self.policy,periods=['WEEK','MONTH'],top_per_period=1,trade_pages=1,closed_pages=1)
        def fetch(url,**params):
            if url.endswith('/leaderboard'):
                return [dict(proxyWallet=self.wallet,userName='Repeated trader',rank='1',pnl=100)]
            return self.trades if url.endswith('/trades') else self.positions
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            result=discover(fetch,policy,directory,self.now)
            watchlist=json.loads((Path(directory)/'screened-watchlist.json').read_text())
            self.assertEqual(1,result['candidate_count'])
            self.assertEqual(2,len(result['coverage']))
            self.assertEqual([self.wallet],watchlist['wallets'])
            self.assertEqual(1,result['selected_core'])
            self.assertTrue((Path(directory)/'trader-screening.csv').exists())

    def test_experimental_group_has_two_dollar_daily_cap(self):
        ledger=Ledger(':memory:')
        for i in range(4):
            trade=dict(self.trades[0],timestamp=self.now,transactionHash='copy-'+str(i),
                       size=100,conditionId='c'+str(i),asset=str(i),proxyWallet='0x'+str(i+1)*40)
            ledger.process(trade,{'asks':[dict(price=.5,size=100)]},self.now,max_cost=1,copy_group='experimental')
        self.assertAlmostEqual(2,ledger.spend())
        ledger.db.close()

    def test_multiple_wallets_cannot_double_market_exposure(self):
        ledger=Ledger(':memory:')
        for i in range(2):
            trade=dict(self.trades[0],timestamp=self.now,transactionHash='market-'+str(i),
                       size=100,conditionId='same-market',asset=str(i),proxyWallet='0x'+str(i+1)*40)
            ledger.process(trade,{'asks':[dict(price=.5,size=100)]},self.now)
        self.assertAlmostEqual(2,ledger.spend())
        ledger.db.close()

    def test_recent_activity_prioritizes_qualified_wallets_only(self):
        from copylab.screening import select_traders
        import copy
        base=self.evaluate()
        idle=dict(base,wallet='idle',last_trade=self.now-7200,score=100)
        active=dict(base,wallet='active',last_trade=self.now-60,score=1)
        rejected=dict(base,wallet='rejected',classification='observe',last_trade=self.now,score=1000)
        stale=dict(base,wallet='stale',last_trade=self.now-90000,score=200)
        chosen=select_traders([idle,active,rejected,stale],dict(self.policy,max_core_wallets=1),self.now)
        self.assertEqual(['active'],[row['wallet'] for row in chosen])

    def test_policy_requires_bounded_experiment_budget(self):
        validate_policy(self.policy)
        with self.assertRaises(ValueError):
            validate_policy(dict(self.policy,experimental_max_cost=5))


if __name__=='__main__':
    unittest.main()
