import json
import tempfile
import unittest
from copylab.btc15 import Engine,fake_market,fake_books
from copylab import btc_scalp


def books(now,up=.70,support=True):
    b=fake_books(now,up=up,down=1-up)
    for v in b.values():v['bids'][0]['size']='300' if support else '50'
    return b


def tick(e,now,up=.7,price=None,support=True,rh=True):
    return e.tick(now,fake_market(),dict(price=100000+now if price is None else price,timestamp=now,received=now) if rh else None,
                  books(now,up,support))


def opened(path=':memory:'):
    e=Engine(path,strategy='scalp',initial_cash=100,goal=1000,compound=True)
    for now in range(47):tick(e,now)
    assert len(e.positions())==1
    return e


class ScalpTests(unittest.TestCase):
    def test_rejects_expensive_roundtrip_even_with_trend(self):
        e=Engine(':memory:',strategy='scalp',initial_cash=100,compound=True)
        for now in range(55):tick(e,now,up=.4)
        self.assertEqual(e.positions(),[])

    def test_supported_entry_has_lower_size_and_stored_policy(self):
        e=opened();t=e.positions()[0]
        self.assertLessEqual(t['cost'],5)
        self.assertEqual(t['market']['scalp_policy']['version'],btc_scalp.VERSION)
        self.assertEqual(t['market']['scalp_policy']['stop_loss'],.10)
        self.assertGreater(t['market']['entry_diagnostics']['near_touch_imbalance'],.1)

    def test_profit_target_is_net_and_early(self):
        e=opened();tick(e,50,up=.79)
        t=e.positions()[0]
        self.assertEqual(t['exit_reason'],'five-percent net profit target')
        self.assertGreaterEqual(t['proceeds'],1.05*t['cost'])

    def test_stop_is_ten_percent_not_twenty_and_can_gap(self):
        e=opened();tick(e,48,up=.66)
        t=e.positions()[0]
        self.assertEqual(t['exit_reason'],'position loss exit')
        self.assertLess(t['proceeds'],.90*t['cost'])

    def test_profit_protection_survives_restart(self):
        with tempfile.TemporaryDirectory() as d:
            path=d+'/db.sqlite3';e=opened(path);tick(e,49,up=.77)
            self.assertIsNone(e.positions()[0]['closed'])
            e.db.close();e=Engine(path,strategy='scalp')
            e.service_exits(50,books(50,.75))
            t=e.positions()[0]
            self.assertEqual(t['exit_reason'],'scalp profit protection')
            self.assertGreater(t['proceeds'],t['cost'])

    def test_btc_adverse_move_invalidates_entry(self):
        e=opened();tick(e,48,price=100020)
        self.assertEqual(e.positions()[0]['exit_reason'],'scalp BTC signal invalidated')

    def test_one_minute_limit_works_without_feed(self):
        e=opened();e.service_exits(106,books(106))
        self.assertEqual(e.positions()[0]['exit_reason'],'one-minute scalp holding limit')

    def test_book_support_required(self):
        e=Engine(':memory:',strategy='scalp',initial_cash=100,compound=True)
        for now in range(55):tick(e,now,support=False)
        self.assertEqual(e.positions(),[])

    def test_loss_cooldown_and_streak_pause(self):
        e=opened();tick(e,48,up=.66)
        self.assertEqual(btc_scalp.reentry(e.db,fake_market(),0,60),(False,'scalp: two-minute cooldown after loss'))
        with e.db:
            for i in [2,3]:
                e.db.execute('INSERT INTO trades(condition,opened,market,direction,shares,cost,closed,proceeds,strategy) VALUES (?,?,?,?,?,?,?,?,?)',
                             (str(i),i*60,json.dumps(fake_market()),0,5,3,i*60+1,2.7,'scalp'))
        self.assertEqual(btc_scalp.reentry(e.db,fake_market(),0,400),(False,'scalp: fifteen-minute pause after three losses'))

    def test_synthetic_scalp_cases_include_profit_loss_and_no_trade(self):
        from copylab.btc15 import simulate_scalp
        s=simulate_scalp(initial_cash=100,goal=1000,compound=True)['scenarios']
        self.assertGreater(s[0]['realized_pnl'],0)
        self.assertLess(s[1]['realized_pnl'],0)
        self.assertEqual(s[2]['trades'],[])

    def test_replay_does_not_fill_before_execution_delay_or_chase_future_ask(self):
        from copylab.btc_replay import replay
        base=1700000100;m=dict(fake_market(),start=base-20,end=base+880)
        records=[]
        for second in range(47):
            at=base+second;b=books(at,up=.4 if second<46 else .8)
            raw=json.dumps(dict(books=b,tokens=m['tokens'],rh=dict(price=100000+second,timestamp=at,received=at)))
            records.append((at,'synthetic',raw))
        r=replay(records,{'synthetic':m},'trend')
        self.assertEqual(r['entries'],0)

    def test_replay_rejects_stale_books(self):
        from copylab.btc_replay import replay
        base=1700000100;m=dict(fake_market(),start=base-20,end=base+880)
        b=books(base)
        for book in b.values():book['timestamp']='1000'
        r=replay([(base,'synthetic',json.dumps(dict(books=b,rh=None)))],{'synthetic':m},'scalp')
        self.assertEqual(r['entries'],0)
        self.assertEqual(r['invalid_book_observations'],2)
