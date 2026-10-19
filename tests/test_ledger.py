import unittest
from copylab.__main__ import Ledger


class RiskTests(unittest.TestCase):
    def setUp(self):
        self.ledger = Ledger(':memory:')
        self.now = 1700000000
        self.trade = dict(timestamp=self.now, side='BUY', price=0.5, asset='a')
        self.book = {'asks': [{'price': '0.51', 'size': '100'}]}

    def test_duplicate_and_market_cap(self):
        self.assertIn('paper buy', self.ledger.process(self.trade, self.book, self.now))
        self.assertEqual('duplicate', self.ledger.process(self.trade, self.book, self.now))
        self.assertEqual('risk limit', self.ledger.process(dict(self.trade, timestamp=self.now+1), self.book, self.now+1))
        self.assertAlmostEqual(2, self.ledger.spend())

    def test_daily_budget(self):
        for asset in range(5):
            self.ledger.process(dict(self.trade, asset=str(asset)), self.book, self.now)
        self.assertAlmostEqual(8, self.ledger.spend())

    def test_stale_and_chasing(self):
        self.assertEqual('old trade or unsupported side', self.ledger.process(self.trade, self.book, self.now+121))
        self.assertEqual('entry price exceeds 5 percent or 2 cent limit', self.ledger.process(dict(self.trade, asset='b'), {'asks':[{'price':'.8','size':'100'}]}, self.now))
        self.assertEqual(0, self.ledger.spend())

    def test_depth_and_fee_reserve(self):
        self.ledger.process(self.trade, {'asks':[{'price':'.5','size':'1'}]}, self.now)
        self.assertAlmostEqual(.51, self.ledger.spend())

    def test_cheap_outcome_cannot_copy_forty_percent_price_increase(self):
        trade=dict(self.trade,price=.033)
        result=self.ledger.process(trade,{'asks':[dict(price=.047,size=100)]},self.now)
        self.assertEqual('entry price exceeds 5 percent or 2 cent limit',result)
        self.assertEqual(0,self.ledger.spend())

    def test_both_relative_and_absolute_entry_limits_apply(self):
        for source,ask,accepted in [(.033,.0346,True),(.033,.0347,False),(.8,.82,True),(.8,.821,False)]:
            with self.subTest(source=source,ask=ask):
                ledger=Ledger(':memory:')
                result=ledger.process(dict(self.trade,price=source),{'asks':[dict(price=ask,size=100)]},self.now)
                self.assertEqual(accepted,result.startswith('paper buy'))
                ledger.db.close()

    def test_several_distinct_markets_can_be_open_at_the_same_time(self):
        from copylab.portfolio import open_positions
        for i in range(5):
            trade=dict(self.trade,asset=str(i),conditionId=str(i))
            self.ledger.process(trade,self.book,self.now)
        self.assertEqual(4,len(open_positions(self.ledger.db)))
        self.assertAlmostEqual(8,self.ledger.spend())

    def test_total_cash_cap(self):
        for day in range(6):
            now = self.now + day * 86400
            for asset in range(5):
                self.ledger.process(dict(self.trade, timestamp=now, asset=f'{day}-{asset}'), self.book, now)
        self.assertAlmostEqual(40, self.ledger.spend())


if __name__ == '__main__':
    unittest.main()
