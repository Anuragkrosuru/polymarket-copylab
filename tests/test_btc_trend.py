import json
import sqlite3
import tempfile
import unittest
from copylab.btc15 import Engine,fake_market,fake_books,simulate_trend


def tick(e,now,price=None,up=.40,rh=True,market=None):
    price=100000+now if price is None else price
    return e.tick(now,market or fake_market(),dict(price=price,timestamp=now,received=now) if rh else None,
                  fake_books(now,up=up,down=1-up))


def opened(path=':memory:'):
    e=Engine(path,strategy='trend')
    for now in range(47):tick(e,now)
    assert len(e.positions())==1
    return e


class TrendTests(unittest.TestCase):
    def test_net_profit_target_not_raw_price_increase(self):
        e=opened()
        tick(e,49,up=.42)
        self.assertIsNone(e.positions()[0]['closed'])
        tick(e,50,up=.48)
        t=e.positions()[0]
        self.assertEqual(t['exit_reason'],'five-percent net profit target')
        self.assertGreaterEqual(t['proceeds'],t['cost']*1.05)

    def test_no_forced_ten_second_exit_in_trend_mode(self):
        e=opened();tick(e,60)
        self.assertIsNone(e.positions()[0]['closed'])
        e.service_exits(166,fake_books(166))
        self.assertEqual(e.positions()[0]['exit_reason'],'two-minute holding limit')

    def test_profit_reentry_and_adverse_scenarios(self):
        scenarios=simulate_trend()['scenarios']
        winning=scenarios[0]['trades']
        self.assertEqual(len(winning),2)
        self.assertEqual(winning[0]['condition'],winning[1]['condition'])
        self.assertGreaterEqual(winning[1]['opened']-winning[0]['closed'],20)
        self.assertTrue(all(t['exit_reason']=='five-percent net profit target' for t in winning))
        self.assertLess(scenarios[1]['realized_pnl'],0)
        self.assertEqual(scenarios[1]['trades'][0]['exit_reason'],'position loss exit')
        self.assertEqual(scenarios[2]['trades'],[])

    def test_no_reentry_chasing_without_pullback(self):
        e=opened();tick(e,50,up=.48)
        for now in range(51,100):tick(e,now)
        self.assertEqual(len(e.positions()),1)

    def test_feed_failure_still_services_profit_and_time_exit(self):
        e=opened();tick(e,50,up=.48,rh=False)
        self.assertEqual(e.positions()[0]['exit_reason'],'five-percent net profit target')
        e=opened();e.service_exits(166,{'up':fake_books(166)['up']})
        self.assertEqual(e.positions()[0]['closed'],166)

    def test_restart_preserves_trade_ids_and_exit_rules(self):
        with tempfile.TemporaryDirectory() as folder:
            path=folder+'/test.sqlite3';e=opened(path)
            e.db.close();e=Engine(path,strategy='trend')
            e.service_exits(50,fake_books(50,up=.48))
            self.assertEqual(e.positions()[0]['id'],1)
            self.assertEqual(e.positions()[0]['exit_reason'],'five-percent net profit target')

    def test_migration_preserves_legacy_trade(self):
        with tempfile.TemporaryDirectory() as folder:
            path=folder+'/old.sqlite3';c=sqlite3.connect(path)
            c.execute('CREATE TABLE trades(id INTEGER PRIMARY KEY,condition TEXT UNIQUE,opened REAL,market TEXT,direction INTEGER,shares REAL,cost REAL,closed REAL,proceeds REAL,exit_reason TEXT)')
            c.execute('INSERT INTO trades VALUES (7,?,?,?,?,?,?,NULL,NULL,NULL)',('synthetic',1,json.dumps(fake_market()),0,5,2.109));c.commit();c.close()
            e=Engine(path,strategy='trend')
            self.assertEqual(e.positions()[0]['id'],7)
            self.assertEqual(e.positions()[0]['strategy'],'lead')
            e.service_exits(12,fake_books(12))
            self.assertEqual(e.positions()[0]['exit_reason'],'ten-second exit')

    def test_pending_fill_rechecks_trend(self):
        e=Engine(':memory:',strategy='trend')
        for now in range(46):tick(e,now)
        tick(e,46,price=99900)
        self.assertEqual(e.positions(),[])

    def test_interval_expiry_exit(self):
        e=opened()
        with e.db:
            m=fake_market();m['end']=120
            e.db.execute('UPDATE trades SET market=?',(json.dumps(m),))
        e.service_exits(100,fake_books(100))
        self.assertEqual(e.positions()[0]['exit_reason'],'interval ending exit')

    def test_four_entry_cap(self):
        from copylab.btc_trend import reentry
        e=Engine(':memory:',strategy='trend')
        with e.db:
            for i in range(4):
                e.db.execute('INSERT INTO trades(condition,opened,market,direction,shares,cost,closed,proceeds,strategy) VALUES (?,?,?,?,?,?,?,?,?)',
                             ('synthetic',i*30,json.dumps(fake_market()),0,5,2,i*30+1,2.1,'trend'))
        self.assertEqual(reentry(e.db,fake_market(),0,150),(False,'four-entry interval cap'))
