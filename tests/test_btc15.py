import json
import tempfile
import unittest
from copylab.btc15 import Engine, fake_market, fake_books, fill, validate_book, simulate


def tick(e, now, price=100000, up=.40, rh=True, market=None):
    return e.tick(now, market or fake_market(), dict(price=price,timestamp=now,received=now) if rh else None,
                  fake_books(now,up=up,down=1-up))


def pending_engine():
    e=Engine(':memory:',strategy='lead')
    for now in range(12,22): tick(e,now)
    assert tick(e,22,100050)=='signal queued; no fill yet'
    return e


class BTC15Tests(unittest.TestCase):
    def test_examples_use_same_engine_and_show_loss(self):
        scenarios=simulate(strategy='lead')['scenarios']
        self.assertAlmostEqual(scenarios[0]['cash'],40.07906)
        self.assertAlmostEqual(scenarios[1]['cash'],39.53637)
        self.assertEqual(scenarios[2]['trades'],[])

    def test_signal_delayed_not_immediate_fill(self):
        e=pending_engine()
        self.assertEqual(e.positions(),[])
        tick(e,22.5,100050)
        self.assertEqual(e.positions(),[])
        self.assertEqual(tick(e,23,100050),'PAPER BUY')
        self.assertEqual(len(e.positions()),1)

    def test_reprice_or_stale_signal_cancels(self):
        for now,up in [(23,.43),(26,.40)]:
            e=pending_engine();tick(e,now,100050,up)
            self.assertEqual(e.positions(),[])

    def test_feed_loss_disables_entry_and_does_not_block_exit(self):
        e=pending_engine();tick(e,23,100050,rh=False)
        self.assertEqual(e.positions(),[])
        e=pending_engine();tick(e,23,100050)
        tick(e,34,rh=False)
        self.assertIsNotNone(e.positions()[0]['closed'])

    def test_exit_with_only_held_book(self):
        e=pending_engine();tick(e,23,100050)
        e.service_exits(34,{'up':fake_books(34)['up']})
        self.assertEqual(e.positions()[0]['closed'],34)

    def test_stale_wrong_or_invalid_books(self):
        b=fake_books(100)['up'];b['timestamp']='100';m=fake_market()
        validate_book(b,m,'up',100)
        with self.assertRaises(ValueError):validate_book(b,m,'down',100)
        with self.assertRaises(ValueError):validate_book(b,m,'up',106)
        b['asks'][0]['size']='nan'
        with self.assertRaises(ValueError):validate_book(b,m,'up',100)

    def test_depth_and_fee_accounting(self):
        b=fake_books(100)['up']
        self.assertAlmostEqual(fill(b,'asks',5,.07),2.109)
        b['asks'][0]['size']='2'
        with self.assertRaises(ValueError):fill(b,'asks',5,.07)

    def test_loss_pause_persists_across_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            p=folder+'/db.sqlite3';e=Engine(p,strategy='lead')
            tick(e,12)
            with e.db:e.db.execute('UPDATE days SET baseline=44')
            self.assertEqual(tick(e,13),'risk pause')
            e.db.close();e=Engine(p,strategy='lead')
            with e.db:e.db.execute('UPDATE days SET baseline=40')
            self.assertEqual(tick(e,14),'risk pause')

    def test_settlement_identity_and_idempotence(self):
        e=pending_engine();tick(e,23,100050)
        closed=dict(condition_id='synthetic',closed=True,tokens=[dict(token_id='up',winner=True),dict(token_id='down',winner=False)])
        e.settle(dict(closed,condition_id='wrong'),901)
        self.assertIsNone(e.positions()[0]['closed'])
        e.settle(closed,901);e.settle(closed,902)
        self.assertEqual(e.positions()[0]['proceeds'],5)
        self.assertEqual(e.positions()[0]['closed'],901)

    def test_cash_cap_and_one_trade_per_interval(self):
        e=pending_engine();tick(e,23,100050)
        tick(e,34,100050)
        for now in range(35,50):tick(e,now,100100)
        self.assertEqual(len(e.positions()),1)
        self.assertLessEqual(e.positions()[0]['cost'],3)

    def test_minimum_size_budget(self):
        e=pending_engine();m=fake_market();m['minimum']=20
        self.assertIn('budget',tick(e,23,100050,market=m))
        self.assertEqual(e.positions(),[])

    def test_daily_purchase_and_lifetime_caps(self):
        for count,opened in [(4,1),(16,-90000)]:
            e=pending_engine()
            with e.db:
                for n in range(count):
                    e.db.execute('INSERT INTO trades(condition,opened,market,direction,shares,cost,closed,proceeds) VALUES (?,?,?,?,?,?,?,?)',
                                 ('old-'+str(n),opened,json.dumps(fake_market()),0,5,2.5,opened+1,2.5))
            self.assertIn('budget',tick(e,23,100050))
            self.assertEqual(len(e.positions()),count)


try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    CRYPTO_AVAILABLE=True
except ImportError:
    CRYPTO_AVAILABLE=False


@unittest.skipUnless(CRYPTO_AVAILABLE,'optional Robinhood signing dependency')
class SigningTests(unittest.TestCase):
    def test_adapter_signs_only_fixed_get_quote_request(self):
        import base64
        import time
        from unittest.mock import patch
        from cryptography.hazmat.primitives import serialization
        from copylab.btc15 import Robinhood
        key=Ed25519PrivateKey.generate()
        seed=key.private_bytes(serialization.Encoding.Raw,serialization.PrivateFormat.Raw,serialization.NoEncryption())
        client=Robinhood(dict(api_key='test-key',private_key_base64=base64.b64encode(seed).decode()))
        def response(url,headers):
            path='/api/v1/crypto/marketdata/best_bid_ask/?symbol=BTC-USD'
            self.assertEqual(url,'https://trading.robinhood.com'+path)
            message=('test-key'+headers['x-timestamp']+path+'GET').encode()
            key.public_key().verify(base64.b64decode(headers['x-signature']),message)
            return {'results':[dict(symbol='BTC-USD',price=100,bid_inclusive_of_sell_spread=99,
                                     ask_inclusive_of_buy_spread=101,timestamp=time.time())]}
        with patch('copylab.btc15.request',side_effect=response):
            self.assertEqual(client.quote()['price'],100)


class DiagnosticsTests(unittest.TestCase):
    def test_lower_trigger_counts_do_not_create_trades(self):
        from copylab.btc15 import signal_diagnostics
        e=Engine(':memory:',strategy='lead')
        for now in range(12,22):tick(e,now)
        # A two-basis-point move passes a shadow threshold but not the live three.
        tick(e,22,100020)
        counts=signal_diagnostics(e)['thresholds_bps']
        self.assertEqual(counts['1']['unchanged_odds_pass'],1)
        self.assertEqual(counts['3']['move_pass'],0)
        self.assertEqual(e.positions(),[])
        self.assertEqual(e.db.execute('SELECT COUNT(*) FROM pending').fetchone()[0],0)
