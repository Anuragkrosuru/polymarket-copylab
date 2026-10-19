"""Robinhood lead hypothesis, paper-only BTC 15-minute market experiment."""
import argparse
import base64
import csv
import getpass
import io
import json
import math
import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from copylab.runtime import watcher_lock
from copylab.presentation import without_quantities

CONFIG = '.env.robinhood.json'


def number(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('Nonfinite number')
    return value


def stamp(value):
    if isinstance(value, str) and 'T' in value:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    result = number(value)
    return result / 1000 if result > 1e11 else result


def request(url, headers=None):
    # No retries: an old response must not silently become a current signal.
    with urlopen(Request(url, headers=dict({'User-Agent': 'CopyLab-BTC15/1.0'}, **(headers or {}))), timeout=3) as response:
        return json.load(response)


class Robinhood:
    """Only a fixed GET quote endpoint; no account or order operations."""
    def __init__(self, config):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        self.api_key = config['api_key']
        self.key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(config['private_key_base64'], validate=True))

    def quote(self):
        path = '/api/v1/crypto/marketdata/best_bid_ask/?symbol=BTC-USD'
        timestamp = str(int(time.time()))
        message = (self.api_key+timestamp+path+'GET').encode()
        headers = {'x-api-key': self.api_key, 'x-timestamp': timestamp,
                   'x-signature': base64.b64encode(self.key.sign(message)).decode()}
        data = request('https://trading.robinhood.com'+path, headers)
        rows = [r for r in data['results'] if r.get('symbol') == 'BTC-USD']
        if len(rows) != 1:
            raise ValueError('Robinhood BTC quote missing')
        row = rows[0]
        price = number(row['price'])
        bid, ask = number(row['bid_inclusive_of_sell_spread']), number(row['ask_inclusive_of_buy_spread'])
        if not 0 < bid <= price <= ask:
            raise ValueError('Invalid Robinhood quote')
        return {'price': price, 'timestamp': stamp(row['timestamp']), 'received': time.time()}


def load_robinhood(path):
    if not Path(path).exists():
        return None
    # Never print the configuration or exception contents containing credentials.
    config = json.loads(Path(path).read_text())
    return Robinhood(config) if config.get('api_key') else None


def setup_key(path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    if Path(path).exists():
        raise ValueError('Configuration exists; refusing to overwrite')
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as handle:
        json.dump({'api_key': '', 'private_key_base64': base64.b64encode(private).decode()}, handle)
    print('Public key to register with Read crypto quotes ONLY:', base64.b64encode(public).decode())
    print('Private key saved locally in', path)


def discover(now):
    start = int(now)//900*900
    slug = 'btc-updown-15m-'+str(start)
    rows = request('https://gamma-api.polymarket.com/markets?slug='+slug)
    if len(rows) != 1:
        raise ValueError('Current BTC market not found')
    m = rows[0]
    if (m['slug'] != slug or not m.get('active') or m.get('closed') or not m.get('acceptingOrders')
            or m.get('negRisk') or json.loads(m['outcomes']) != ['Up', 'Down']
            or stamp(m['endDate']) != start+900):
        raise ValueError('Unexpected BTC market structure')
    config = m.get('cryptoMarketConfig', {})
    if not (config.get('asset') == 'btc' and config.get('duration') == '15m'
            and config.get('twapEnabled') is True and config.get('twapLookbackSeconds') == 60
            and m.get('resolutionSource') == 'https://data.chain.link/streams/btc-usd-twap-60s-streams'):
        raise ValueError('Resolution rules changed; review required')
    fee = m.get('feeSchedule', {})
    if not (m.get('feesEnabled') is True and number(fee.get('exponent')) == 1
            and 0 <= number(fee.get('rate')) <= .2):
        raise ValueError('Unknown fee schedule; pause')
    tokens = json.loads(m['clobTokenIds'])
    if len(tokens) != 2 or len(set(tokens)) != 2:
        raise ValueError('Invalid outcome tokens')
    return dict(condition=m['conditionId'], title=m['question'], start=start, end=start+900,
                tokens=tokens, fee_rate=number(fee['rate']), minimum=number(m['orderMinSize']),
                resolution_source=m['resolutionSource'])


def validate_book(book, market, asset, now):
    if book.get('market') != market['condition'] or str(book.get('asset_id')) != str(asset):
        raise ValueError('Book identity mismatch')
    if not -1 <= now-stamp(book['timestamp']) <= 5:
        raise ValueError('Stale book')
    for side in ['asks', 'bids']:
        if not book.get(side):
            raise ValueError('Missing '+side)
        for r in book[side]:
            if not (0 < number(r['price']) < 1 and number(r['size']) > 0):
                raise ValueError('Invalid book level')
    if max(number(r['price']) for r in book['bids']) >= min(number(r['price']) for r in book['asks']):
        raise ValueError('Crossed book')
    minimum = number(book.get('min_order_size', 0))
    if minimum <= 0:
        raise ValueError('Missing book minimum')


def fill(book, side, shares, fee_rate):
    """Depth walk, venue fee formula, plus adverse half-cent/share assumption."""
    remaining, gross, fees = shares, 0., 0.
    for row in sorted(book[side], key=lambda r:number(r['price']), reverse=side == 'bids'):
        p, size = number(row['price']), number(row['size'])
        used = min(remaining, size)
        if not (0 < p < 1 and size > 0):
            raise ValueError('Invalid depth')
        gross += used*p
        fees += round(used*fee_rate*p*(1-p), 5)
        remaining -= used
        if remaining < 1e-8:
            total = gross+fees+shares*.005 if side == 'asks' else max(0, gross-fees-shares*.005)
            return total
    raise ValueError('Insufficient depth')


class Engine:
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=10)
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS samples(id INTEGER PRIMARY KEY, received REAL, condition TEXT, rh REAL, upask REAL, downask REAL, payload TEXT);
        CREATE INDEX IF NOT EXISTS samples_time ON samples(received);
        CREATE TABLE IF NOT EXISTS trades(id INTEGER PRIMARY KEY, condition TEXT UNIQUE, opened REAL, market TEXT, direction INTEGER, shares REAL, cost REAL, closed REAL, proceeds REAL, exit_reason TEXT);
        CREATE TABLE IF NOT EXISTS pending(id INTEGER PRIMARY KEY CHECK(id=1), created REAL, market TEXT, direction INTEGER, ask REAL);
        CREATE TABLE IF NOT EXISTS days(day TEXT PRIMARY KEY, baseline REAL, halted INTEGER);
        ''')

    def positions(self):
        rows = self.db.execute('SELECT id,condition,opened,market,direction,shares,cost,closed,proceeds,exit_reason FROM trades ORDER BY id').fetchall()
        return [dict(id=r[0], condition=r[1], opened=r[2], market=json.loads(r[3]), direction=r[4],
                     shares=r[5], cost=r[6], closed=r[7], proceeds=r[8], exit_reason=r[9]) for r in rows]

    def state(self, books=None):
        positions = self.positions()
        spent = sum(t['cost'] for t in positions)
        cash = 40-spent+sum(t['proceeds'] or 0 for t in positions)
        open_value, unknown = 0., False
        for t in positions:
            if t['closed'] is None:
                try:
                    book = books[t['market']['tokens'][t['direction']]]
                    open_value += fill(book, 'bids', t['shares'], t['market']['fee_rate'])
                except (KeyError, TypeError, ValueError):
                    unknown = True
        return dict(initial_cash=40, cash=cash, lifetime_spend=spent,
                    realized_pnl=sum(t['proceeds']-t['cost'] for t in positions if t['closed'] is not None),
                    equity=None if unknown else cash+open_value,
                    total_pnl=None if unknown else cash+open_value-40,
                    trades=positions)

    def settle(self, market, now):
        condition = market.get('condition_id')
        tokens = market.get('tokens', [])
        if not market.get('closed') or len(tokens) != 2 or sum(t.get('winner') is True for t in tokens) != 1:
            return
        winner = next(str(t['token_id']) for t in tokens if t.get('winner') is True)
        for t in self.positions():
            if t['condition'] == condition and t['closed'] is None:
                if {str(x['token_id']) for x in tokens} != set(t['market']['tokens']):
                    return
                proceeds = t['shares'] if t['market']['tokens'][t['direction']] == winner else 0
                with self.db:
                    self.db.execute('UPDATE trades SET closed=?,proceeds=?,exit_reason=? WHERE id=? AND closed IS NULL',
                                    (now,proceeds,'confirmed settlement',t['id']))

    def service_exits(self, now, books):
        day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
        baseline = self.db.execute('SELECT baseline,halted FROM days WHERE day=?',(day,)).fetchone()
        state = self.state(books)
        breached = bool(baseline and state['equity'] is not None and baseline[0]-state['equity'] >= 4)
        if breached:
            with self.db:
                self.db.execute('UPDATE days SET halted=1 WHERE day=?',(day,))
        for t in state['trades']:
            if t['closed'] is not None:
                continue
            try:
                proceeds = fill(books[t['market']['tokens'][t['direction']]], 'bids', t['shares'], t['market']['fee_rate'])
                reason = ('daily loss pause' if breached or (baseline and baseline[1]) else
                          'ten-second exit' if now-t['opened'] >= 10 else
                          'position loss exit' if proceeds <= t['cost']*.8 else None)
                if reason:
                    with self.db:
                        self.db.execute('UPDATE trades SET closed=?,proceeds=?,exit_reason=? WHERE id=? AND closed IS NULL',
                                        (now,proceeds,reason,t['id']))
            except (KeyError, ValueError):
                pass

    def tick(self, now, market, rh, books):
        # Books passed here must be validated this cycle, never a cached old quote.
        asks = [min(number(r['price']) for r in books[t]['asks']) for t in market['tokens']]
        fresh = rh is not None and -1 <= now-rh['timestamp'] <= 3 and 0 <= now-rh['received'] <= 3
        with self.db:
            self.db.execute('INSERT INTO samples(received,condition,rh,upask,downask,payload) VALUES (?,?,?,?,?,?)',
                            (now,market['condition'], rh['price'] if fresh else None,*asks,
                             json.dumps({'rh':rh,'books':books})))
        state = self.state(books)
        day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
        baseline = self.db.execute('SELECT baseline,halted FROM days WHERE day=?',(day,)).fetchone()
        with self.db:
            if baseline is None and state['equity'] is not None:
                self.db.execute('INSERT INTO days VALUES (?,?,0)',(day,state['equity']))
                baseline=(state['equity'],0)
            if baseline and state['equity'] is not None and baseline[0]-state['equity'] >= 4:
                self.db.execute('UPDATE days SET halted=1 WHERE day=?',(day,))
                baseline=(baseline[0],1)
        halted = not baseline or bool(baseline[1]) or state['equity'] is None
        self.service_exits(now, books)
        pending = self.db.execute('SELECT created,market,direction,ask FROM pending').fetchone()
        if pending:
            created, raw, direction, original_ask = pending
            original = json.loads(raw)
            if now-created < 1:
                return 'awaiting one-second execution delay'
            with self.db:
                self.db.execute('DELETE FROM pending')
            if (now-created > 3 or not fresh or halted or original['condition'] != market['condition']
                    or market['end']-now < 30 or asks[direction] > original_ask+.01):
                return 'pending signal expired or rejected'
            quantity = max(5, market['minimum'], number(books[market['tokens'][direction]]['min_order_size']))
            cost = fill(books[market['tokens'][direction]], 'asks',quantity,market['fee_rate'])
            liquid = fill(books[market['tokens'][direction]], 'bids',quantity,market['fee_rate'])
            state=self.state(books)
            spent_today=sum(t['cost'] for t in state['trades'] if datetime.fromtimestamp(t['opened'],timezone.utc).date().isoformat()==day)
            if (cost > 3 or cost > state['cash'] or spent_today+cost > 12 or state['lifetime_spend']+cost > 40
                    or liquid < .85*cost or any(t['closed'] is None for t in state['trades'])):
                return 'budget, open exposure or entry cost guard'
            with self.db:
                result=self.db.execute('INSERT OR IGNORE INTO trades(condition,opened,market,direction,shares,cost) VALUES (?,?,?,?,?,?)',
                                       (market['condition'],now,json.dumps(market),direction,quantity,cost))
            return 'PAPER BUY' if result.rowcount else 'already traded this interval'
        if halted or not fresh:
            return 'risk pause' if halted else 'waiting for fresh Robinhood quote'
        if not market['start']+12 <= now <= market['end']-30:
            return 'outside entry window'
        if self.db.execute('SELECT 1 FROM trades WHERE condition=?',(market['condition'],)).fetchone():
            return 'already traded this interval'
        prior=self.db.execute('SELECT received,rh,upask,downask FROM samples WHERE condition=? AND received BETWEEN ? AND ? AND rh IS NOT NULL ORDER BY ABS(received-?) LIMIT 1',
                              (market['condition'],now-12,now-8,now-10)).fetchone()
        if not prior:
            return 'collecting ten-second signal history'
        move=rh['price']/prior[1]-1
        if abs(move) < .0003:
            return 'BTC move below three basis points'
        direction=0 if move > 0 else 1
        ask=asks[direction]
        book=books[market['tokens'][direction]]
        bid=max(number(r['price']) for r in book['bids'])
        if not (.10 <= ask <= .65 and ask-bid <= .03 and abs(ask-prior[2+direction]) <= .01+1e-9):
            return 'odds already moved or spread/price rejected'
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO pending VALUES (1,?,?,?,?)',(now,json.dumps(market),direction,ask))
        return 'signal queued; no fill yet'


def atomic(path, text):
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(text)
    temp.replace(path)


def report(engine, folder, health, books=None):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    state=engine.state(books)
    for t in state['trades']:
        t['entry_price_including_costs'] = t['cost']/t['shares']
        t['exit_price_after_costs'] = None if t['proceeds'] is None else t['proceeds']/t['shares']
    state.update(health,updated_utc=datetime.now(timezone.utc).isoformat(),mode='PAPER ONLY',
                 hypothesis='Unverified Robinhood lead; no assumed ten-second advantage',
                 samples=engine.db.execute('SELECT COUNT(*) FROM samples').fetchone()[0])
    atomic(folder/'status.json',json.dumps(without_quantities(state),indent=2)+'\n')
    output=io.StringIO();writer=csv.writer(output)
    writer.writerow(['id','market','outcome','opened_utc','entry_price_including_costs','cost','closed_utc','exit_price_after_costs','proceeds','pnl','exit_reason'])
    lines=[]
    for t in state['trades']:
        opened=datetime.fromtimestamp(t['opened'],timezone.utc).isoformat()
        closed='' if t['closed'] is None else datetime.fromtimestamp(t['closed'],timezone.utc).isoformat()
        writer.writerow([t['id'],t['market']['title'],['Up','Down'][t['direction']],opened,t['entry_price_including_costs'],t['cost'],closed,t['exit_price_after_costs'],t['proceeds'],'' if not closed else t['proceeds']-t['cost'],t['exit_reason']])
        lines.append(f"{opened} PAPER BUY | {t['market']['title']} | {['Up','Down'][t['direction']]} | all-in entry price ${t['entry_price_including_costs']:.4f} | cost ${t['cost']:.4f}")
        if closed:
            lines.append(f"{closed} PAPER EXIT | trade {t['id']} | net exit price ${t['exit_price_after_costs']:.4f} | proceeds ${t['proceeds']:.4f} | P/L ${t['proceeds']-t['cost']:.4f} | {t['exit_reason']}")
    atomic(folder/'trades.csv',output.getvalue())
    atomic(folder/'trades.log','\n'.join(lines)+ ('\n' if lines else ''))


def live(args):
    with watcher_lock(args.database):
        engine=Engine(args.database)
        market=None
        with ThreadPoolExecutor(max_workers=4) as pool:
            while True:
                started=time.monotonic();books={};health={'feed':'not connected','decision':'waiting','errors':[]}
                try:
                    try:
                        rh_client=load_robinhood(args.config)
                    except Exception as error:
                        rh_client=None
                        health['errors'].append('Robinhood configuration: '+type(error).__name__)
                    health['feed']='Robinhood authenticated quote API' if rh_client else 'NOT CONNECTED — entries disabled'
                    now=time.time()
                    if market is None or now >= market['end']:
                        market=discover(now)
                    targets={t:market for t in market['tokens']}
                    for t in engine.positions():
                        if t['closed'] is None:
                            targets.update({token:t['market'] for token in t['market']['tokens']})
                    rh_future=pool.submit(rh_client.quote) if rh_client else None
                    futures={token:pool.submit(request,'https://clob.polymarket.com/book?token_id='+token) for token in targets}
                    for token,future in futures.items():
                        try:
                            b=future.result();validate_book(b,targets[token],token,time.time());books[token]=b
                        except Exception as error:
                            health['errors'].append('book '+token+': '+type(error).__name__)
                    rh=None
                    if rh_future:
                        try: rh=rh_future.result()
                        except Exception as error: health['errors'].append('Robinhood quote: '+type(error).__name__)
                    now=time.time()
                    # Revalidate after all requests; a fast response cannot age while another stalls.
                    for token in list(books):
                        try: validate_book(books[token],targets[token],token,now)
                        except ValueError: del books[token]
                    if time.monotonic()-started > 3:
                        rh=None
                        health['errors'].append('Cycle exceeded three-second signal window')
                    engine.service_exits(now, books)
                    if all(t in books for t in market['tokens']):
                        health['decision']=engine.tick(now,market,rh,books)
                    else:
                        health['decision']='missing fresh current-market books; no new fills'
                    # Resolution checks only after interval ends, at most once per 30s.
                    if int(now)//30 != getattr(engine,'settlement_bucket',None):
                        engine.settlement_bucket=int(now)//30
                        for t in engine.positions():
                            if t['closed'] is None and now >= t['market']['end']:
                                try: engine.settle(request('https://clob.polymarket.com/markets/'+t['condition']),time.time())
                                except Exception as error: health['errors'].append('settlement: '+type(error).__name__)
                    health['market']=market['title']
                except Exception as error:
                    # Never log request headers, credentials, or arbitrary exception text.
                    health['decision']='cycle failed; entries disabled'
                    health['errors'].append(type(error).__name__)
                health['cycle_seconds']=time.monotonic()-started
                report(engine,args.report_dir,health,books)
                print(json.dumps(health),flush=True)
                if args.once:
                    break
                time.sleep(max(0,args.poll-(time.monotonic()-started)))


def fake_market():
    return dict(condition='synthetic',title='INVENTED BTC 15m interval',start=0,end=900,tokens=['up','down'],fee_rate=.07,minimum=5)


def fake_books(now,up=.40,down=.60):
    return {token:dict(asset_id=token,market='synthetic',timestamp=str(now*1000),min_order_size='5',
                        asks=[dict(price=str(price),size='100')],bids=[dict(price=str(price-.01),size='100')])
            for token,price in [('up',up),('down',down)]}


def simulate():
    scenarios=[]
    for name,exit_ask,signal_ask in [('lag_then_favorable',.47,.40),('lag_then_adverse',.36,.40),('odds_already_repriced',.47,.45)]:
        e=Engine(':memory:');m=fake_market();decisions=[]
        for now in range(12,36):
            price=100000 if now < 22 else 100050
            up=.40 if now < 22 else signal_ask if now < 33 else exit_ask
            b=fake_books(now,up=up,down=1-up)
            decisions.append(e.tick(now,m,dict(price=price,timestamp=now,received=now),b))
        state=e.state(b)
        scenarios.append(dict(name=name,**state,decisions=decisions))
    return {'label':'SYNTHETIC ONLY: invented quotes, not backtest/live returns. Each scenario starts at $40.',
            'scenarios':scenarios}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database',default='btc15.sqlite3')
    p.add_argument('--report-dir',default='reports/btc15')
    p.add_argument('--config',default=CONFIG)
    p.add_argument('--poll',type=float,default=1)
    p.add_argument('--once',action='store_true')
    p.add_argument('--simulate',action='store_true')
    p.add_argument('--create-key',action='store_true')
    p.add_argument('--set-api-key',action='store_true')
    p.add_argument('--connect',action='store_true')
    a=p.parse_args()
    if a.create_key:
        setup_key(a.config)
    elif a.set_api_key or a.connect:
        if not Path(a.config).exists():
            setup_key(a.config)
        data=json.loads(Path(a.config).read_text())
        if a.connect:
            from cryptography.hazmat.primitives import serialization
            public = Robinhood(dict(data, api_key='')).key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            print('Open https://robinhood.com/account/crypto and select Add key.')
            print('Enable Read crypto quotes ONLY. Register this public key:')
            print(base64.b64encode(public).decode())
            print('Then enter the API key Robinhood returns below. It will not be displayed.')
        data['api_key']=getpass.getpass('Robinhood read-quotes-only API key: ').strip()
        descriptor=os.open(a.config,os.O_WRONLY|os.O_TRUNC)
        os.fchmod(descriptor,0o600)
        with os.fdopen(descriptor,'w') as handle: json.dump(data,handle)
        print('Saved locally. Background collector reloads configuration each cycle.')
    elif a.simulate:
        print(json.dumps(simulate(),indent=2))
    else:
        if not math.isfinite(a.poll) or a.poll < 1: p.error('poll must be at least one second')
        live(a)


if __name__ == '__main__':
    main()
