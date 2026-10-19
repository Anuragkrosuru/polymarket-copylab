import contextlib
import io
import json
import unittest

from copylab.__main__ import Ledger, retry_exits, scan
from copylab.portfolio import open_lots, summary


class ExecutionQualityTests(unittest.TestCase):
    def setUp(self):
        self.ledger=Ledger(':memory:')
        self.now=1700000000
        self.trade=dict(transactionHash='buy',proxyWallet='0x'+'1'*40,timestamp=self.now,
                        side='BUY',asset='a',conditionId='c',price=.5,size=100,title='Example')
        self.book=dict(asset_id='a',asks=[dict(price=.5,size=100)],bids=[dict(price=.49,size=100)])

    def sell(self,book):
        self.ledger.process(self.trade,self.book,self.now)
        return self.ledger.process(dict(self.trade,transactionHash='sell',side='SELL',price=.6,
                                       timestamp=self.now+1),book,self.now+1)

    def retry(self,book,seconds):
        with contextlib.redirect_stdout(io.StringIO()):
            retry_exits(self.ledger,lambda *a,**k:book,self.now+seconds)

    def test_wide_spread_entry_is_rejected_even_at_source_price(self):
        result=self.ledger.process(self.trade,dict(self.book,bids=[dict(price=.4,size=100)]),self.now)
        self.assertEqual('entry spread and fee drag exceeds 10 percent',result)
        self.assertEqual(0,self.ledger.spend())

    def test_thin_or_empty_bid_book_cannot_be_treated_as_liquid(self):
        for bids,reason in [([], 'entry requires both bid and ask liquidity'),
                            ([dict(price=.49,size=1)], 'insufficient bid depth for copied position')]:
            ledger=Ledger(':memory:')
            result=ledger.process(self.trade,dict(self.book,bids=bids),self.now)
            self.assertEqual(reason,result)
            self.assertEqual(0,ledger.spend())
            ledger.db.close()

    def test_crossed_and_invalid_books_roll_back_without_consuming_signal(self):
        for book in [dict(self.book,bids=[dict(price=.6,size=100)]),dict(self.book,asks=[dict(price='NaN',size=100)])]:
            with self.assertRaises(ValueError):
                self.ledger.process(self.trade,book,self.now)
            self.assertFalse(self.ledger.known(self.trade))

    def test_partial_exit_retries_only_when_bid_depth_changes(self):
        thin=dict(self.book,bids=[dict(price=.6,size=1)])
        self.assertIn('paper sell',self.sell(thin))
        self.assertEqual(1,summary(self.ledger,self.now+1)['pending_exit_requests'])
        initial=self.ledger.db.execute('SELECT SUM(shares) FROM exits').fetchone()[0]
        self.retry(thin,7)
        self.assertEqual(initial,self.ledger.db.execute('SELECT SUM(shares) FROM exits').fetchone()[0])
        self.retry(dict(thin,bids=[dict(price=.61,size=100)]),13)
        self.assertEqual([],open_lots(self.ledger.db))
        report=summary(self.ledger,self.now+13)
        self.assertEqual(0,report['pending_exit_requests'])
        self.assertEqual(1,report['sales'])
        self.assertEqual(1,self.ledger.db.execute('SELECT COUNT(*) FROM exits').fetchone()[0])

    def test_exit_requests_expire_without_inventing_a_fill(self):
        self.sell(dict(self.book,bids=[]))
        self.retry(dict(self.book,bids=[dict(price=.7,size=100)]),122)
        self.assertEqual(0,summary(self.ledger,self.now+122)['sales'])
        self.assertEqual(0,self.ledger.db.execute('SELECT COUNT(*) FROM pending_exits').fetchone()[0])
        self.assertEqual(1,len(open_lots(self.ledger.db)))

    def test_retries_do_not_sell_new_lots_bought_after_the_source_sale(self):
        # A partial-size copied entry leaves room under the original market cap.
        small=dict(self.book,asks=[dict(price=.5,size=1)])
        self.ledger.process(self.trade,small,self.now)
        sell=dict(self.trade,transactionHash='sell',side='SELL',price=.6,timestamp=self.now+1)
        self.ledger.process(sell,dict(self.book,bids=[]),self.now+1)
        new_buy=dict(self.trade,transactionHash='new-buy',timestamp=self.now+2)
        self.ledger.process(new_buy,self.book,self.now+2)
        self.retry(dict(self.book,bids=[dict(price=.6,size=100)]),7)
        lots=open_lots(self.ledger.db)
        self.assertEqual(1,len(lots))
        self.assertEqual('new-buy',json.loads(lots[0][4])['transactionHash'])
        self.assertAlmostEqual(1,self.ledger.db.execute('SELECT SUM(shares) FROM exits').fetchone()[0])

    def test_pending_exit_survives_restart_and_completes_once(self):
        import tempfile
        from pathlib import Path
        self.sell(dict(self.book,bids=[dict(price=.6,size=1)]))
        with tempfile.TemporaryDirectory() as directory:
            path=str(Path(directory)/'paper.sqlite3')
            disk=Ledger(path)
            self.ledger.db.backup(disk.db)
            disk.db.close()
            disk=Ledger(path)
            with contextlib.redirect_stdout(io.StringIO()):
                retry_exits(disk,lambda *a,**k:dict(self.book,bids=[dict(price=.61,size=100)]),self.now+7)
                before=summary(disk,self.now+7)['cash']
                retry_exits(disk,lambda *a,**k:dict(self.book,bids=[dict(price=.62,size=100)]),self.now+13)
            self.assertEqual(before,summary(disk,self.now+13)['cash'])
            self.assertEqual([],open_lots(disk.db))
            disk.db.close()

    def test_newer_source_sale_supersedes_older_request(self):
        self.sell(dict(self.book,bids=[]))
        new=dict(self.trade,transactionHash='new-sell',side='SELL',price=.6,timestamp=self.now+2)
        self.ledger.process(new,dict(self.book,bids=[]),self.now+2)
        self.assertEqual(1,self.ledger.db.execute('SELECT COUNT(DISTINCT id) FROM pending_exits').fetchone()[0])
        self.retry(dict(self.book,bids=[dict(price=.61,size=100)]),8)
        self.assertEqual(1,summary(self.ledger,self.now+8)['sales'])
        self.assertEqual([],open_lots(self.ledger.db))

    def test_audit_records_entry_price_delay_and_policy(self):
        with self.ledger.db:
            self.ledger.db.execute('INSERT INTO wallets VALUES (?,?)',(self.trade['proxyWallet'],self.now-1))
        with contextlib.redirect_stdout(io.StringIO()):
            scan(self.ledger,self.trade['proxyWallet'],lambda *a,**k:self.book,
                 lambda:self.now+10,prefetched=[self.trade])
        row=json.loads(self.ledger.db.execute('SELECT context FROM execution_audits').fetchone()[0])
        self.assertEqual(10,row['source_age_seconds'])
        self.assertAlmostEqual(.5,row['entry_price'])
        self.assertLess(row['entry_drag'],.1)
        self.assertIn('v3',row['policy'])


if __name__=='__main__':
    unittest.main()
