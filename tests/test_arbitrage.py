import unittest
from copylab.arbitrage import Account, quote, scenarios


def market():
    return dict(condition_id='c', question='Example', active=True, closed=False, accepting_orders=True,
                tokens=[{'token_id':'a'},{'token_id':'b'}], minimum_order_size=5)


def books(prices=(0.42,0.43)):
    return [dict(asset_id=t,market='c',asks=[dict(price=p,size=10)],bids=[dict(price=p-.03,size=10)]) for t,p in zip(('a','b'),prices)]


class ArbitrageTests(unittest.TestCase):
    def test_edge_and_normal_spread(self):
        self.assertAlmostEqual(quote(market(), books())['expected_gain'], .325)
        with self.assertRaises(ValueError): quote(market(), books((.50,.51)))

    def test_depth_identity_and_crossed(self):
        for mutate in (lambda b:b[0].update(asset_id='wrong'),
                       lambda b:b[0]['asks'][0].update(size=1),
                       lambda b:b[0]['bids'][0].update(price=.9)):
            b=books(); mutate(b)
            with self.assertRaises(ValueError): quote(market(), b)

    def test_budget_and_negative_risk(self):
        m=market(); m['minimum_order_size']=20
        with self.assertRaises(ValueError): quote(m,books())
        m=market(); m['neg_risk']=True
        with self.assertRaises(ValueError): quote(m,books())

    def test_duplicate_and_settlement(self):
        a=Account(':memory:'); m=market(); q=quote(m,books())
        self.assertTrue(a.enter(m,q)); self.assertFalse(a.enter(m,q))
        self.assertEqual(a.state()['realized_pnl'],0)
        resolved=dict(m,closed=True,tokens=[dict(token_id='a',winner=True),dict(token_id='b',winner=False)])
        a.settle('c',resolved); a.settle('c',resolved)
        self.assertAlmostEqual(a.state()['cash'],40.325)
        self.assertAlmostEqual(a.state()['realized_pnl'],.325)

    def test_daily_limit(self):
        a=Account(':memory:'); q=quote(market(),books())
        for n in range(3):
            m=dict(market(),condition_id=str(n))
            self.assertEqual(a.enter(m,q,100),n<2)

    def test_failed_leg_is_loss(self):
        self.assertLess(scenarios()['second_leg_failure_stress']['loss'],0)

    def test_published_fee_and_buffer(self):
        m=dict(market(),paper_fee_rate=.07)
        q=quote(m,books())
        self.assertAlmostEqual(q['cost'],4.44605)
        self.assertEqual(q['fee_model'],'published schedule plus buffer')
        self.assertAlmostEqual(q['entry_prices'][0],.42)
        self.assertAlmostEqual(q['entry_prices'][1],.43)

    def test_raw_discount_can_still_lose_after_fees(self):
        m=dict(market(),paper_fee_rate=.07)
        with self.assertRaises(ValueError):quote(m,books((.49,.49)))
        candidate=quote(m,books((.49,.49)),require_edge=False)
        self.assertLess(candidate['expected_gain'],0)
        self.assertGreater(quote(dict(m,paper_fee_rate=0),books((.49,.49)))['expected_gain'],0)

    def test_live_scan_requires_known_fees_and_reports_closest(self):
        from unittest.mock import patch
        from copylab.arbitrage import scan
        with patch('copylab.arbitrage.get',return_value=market()),patch('copylab.arbitrage.FEE_SCHEDULES',{}):
            health=scan(Account(':memory:'),['c'])
            self.assertEqual(health['entered'],0)
            self.assertEqual(health['reasons'],{'Unknown published fees; skip':1})
        with patch('copylab.arbitrage.get',return_value=market()),patch('copylab.arbitrage.FEE_SCHEDULES',{'c':.07}),patch('copylab.arbitrage.books_for',return_value=books((.5,.51))):
            health=scan(Account(':memory:'),['c'])
            self.assertEqual(health['entered'],0)
            self.assertLess(health['closest_candidate']['expected_gain'],0)

    def test_stale_exchange_snapshot_is_rejected(self):
        from unittest.mock import patch
        from copylab.arbitrage import books_for
        with patch('copylab.arbitrage.get',return_value=dict(timestamp=1000)),patch('copylab.arbitrage.time.time',return_value=100):
            with self.assertRaisesRegex(ValueError,'Stale exchange'):books_for(market())
