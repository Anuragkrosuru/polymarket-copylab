import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from copylab.__main__ import Ledger, scan
from copylab.sizing import observe, target


class SizingTests(unittest.TestCase):
    def setUp(self):
        self.ledger=Ledger(':memory:')
        self.now=1700000000
        self.wallet='0xabc'
        self.trade=dict(proxyWallet=self.wallet,conditionId='current',asset='a',side='BUY',
                        timestamp=self.now,price=.5,size=200,transactionHash='current')

    def seed(self):
        history=[dict(self.trade,conditionId='m'+str(i),asset='a'+str(i),timestamp=self.now-1000,
                      transactionHash='h'+str(i),size=200) for i in range(20)]
        observe(self.ledger,self.wallet,history,self.now)
        return history

    def allocation(self,size,cap=2):
        t=dict(self.trade,size=size)
        observe(self.ledger,self.wallet,[t],self.now)
        return target(self.ledger,t,cap)

    def test_relative_allocations(self):
        for size,expected in [(100,.5),(200,1),(400,2),(1000,2)]:
            self.ledger=Ledger(':memory:');self.seed()
            result=self.allocation(size)
            self.assertAlmostEqual(expected,result['target_cost'])
            self.assertEqual(20,result['history_markets'])
            self.assertEqual(100,result['typical_commitment'])

    def test_split_fills_and_duplicate_feeds(self):
        self.seed()
        first=dict(self.trade,size=100,transactionHash='split1')
        second=dict(first,transactionHash='split2')
        observe(self.ledger,self.wallet,[first,second,first,second],self.now)
        result=target(self.ledger,second,2)
        self.assertEqual(100,result['source_commitment'])
        self.assertEqual(1,result['target_cost'])

    def test_future_fills_do_not_inflate_earlier_sizing(self):
        self.seed()
        future=dict(self.trade,timestamp=self.now+1,size=10000,transactionHash='future')
        observe(self.ledger,self.wallet,[self.trade,future],self.now+1)
        self.assertEqual(1,target(self.ledger,self.trade,2)['target_cost'])

    def test_limited_history_and_experimental_cap(self):
        self.assertEqual(.5,self.allocation(200)['target_cost'])
        self.assertEqual(.25,target(self.ledger,self.trade,1)['target_cost'])
        self.seed()
        self.assertEqual(.5,target(self.ledger,self.trade,1)['target_cost'])

    def test_invalid_or_wrong_wallet_history_is_ignored(self):
        bad=[dict(self.trade,price='nan'),dict(self.trade,size=-1),dict(self.trade,proxyWallet='other')]
        observe(self.ledger,self.wallet,bad,self.now)
        self.assertEqual(0,self.ledger.db.execute('SELECT COUNT(*) FROM source_commitments').fetchone()[0])

    def test_history_survives_restart(self):
        with tempfile.TemporaryDirectory() as d:
            self.seed();other=Ledger(str(Path(d)/'paper.sqlite3'))
            self.ledger.db.backup(other.db);other.db.close()
            other=Ledger(str(Path(d)/'paper.sqlite3'))
            observe(other,self.wallet,[self.trade],self.now)
            self.assertEqual(1,target(other,self.trade,2)['target_cost'])

    def test_scan_applies_size_and_audits_it(self):
        self.seed()
        with self.ledger.db:self.ledger.db.execute('INSERT INTO wallets VALUES (?,?)',(self.wallet,self.now-1))
        book=dict(asset_id='a',market='current',asks=[dict(price=.5,size=100)],bids=[dict(price=.49,size=100)])
        with contextlib.redirect_stdout(io.StringIO()):
            scan(self.ledger,self.wallet,lambda *a,**k:book,lambda:self.now,prefetched=[self.trade])
        self.assertAlmostEqual(1,self.ledger.spend())
        context=json.loads(self.ledger.db.execute('SELECT context FROM execution_audits').fetchone()[0])
        self.assertEqual(1,context['sizing_target_cost'])
        self.assertEqual(100,context['sizing_source_commitment'])

    def test_minimum_shares_are_not_bypassed_for_small_allocation(self):
        with self.ledger.db:self.ledger.db.execute('INSERT INTO wallets VALUES (?,?)',(self.wallet,self.now-1))
        book=dict(asset_id='a',market='current',min_order_size='5',asks=[dict(price=.5,size=100)],bids=[dict(price=.49,size=100)])
        with contextlib.redirect_stdout(io.StringIO()):
            scan(self.ledger,self.wallet,lambda *a,**k:book,lambda:self.now,prefetched=[self.trade])
        self.assertEqual(0,self.ledger.spend())

    def test_later_fills_top_up_to_target_instead_of_adding_target(self):
        self.seed()
        with self.ledger.db:self.ledger.db.execute('INSERT INTO wallets VALUES (?,?)',(self.wallet,self.now-1))
        book=dict(asset_id='a',market='current',asks=[dict(price=.5,size=100)],bids=[dict(price=.49,size=100)])
        first=dict(self.trade,size=100,transactionHash='first')
        second=dict(first,timestamp=self.now+1,transactionHash='second')
        with contextlib.redirect_stdout(io.StringIO()):
            scan(self.ledger,self.wallet,lambda *a,**k:book,lambda:self.now,prefetched=[first])
            self.assertAlmostEqual(.5,self.ledger.spend())
            scan(self.ledger,self.wallet,lambda *a,**k:book,lambda:self.now+1,prefetched=[first,second])
        self.assertAlmostEqual(1,self.ledger.spend())

    def test_twenty_outcomes_in_one_market_do_not_count_as_twenty_markets(self):
        history=[dict(self.trade,conditionId='same-market',asset='outcome'+str(i),timestamp=self.now-1000,
                      transactionHash='h'+str(i)) for i in range(20)]
        observe(self.ledger,self.wallet,history,self.now)
        self.assertEqual(.5,self.allocation(200)['target_cost'])
        self.assertEqual(1,self.allocation(200)['history_markets'])
