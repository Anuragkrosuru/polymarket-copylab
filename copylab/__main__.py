import argparse
import hashlib
import json
import math
import re
import sqlite3
import time
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import URLError
from copylab.portfolio import initialize, refresh, summary, display


def get(url, **params):
    request = Request(url + '?' + urlencode(params), headers={'User-Agent': 'CopyLab/0.1'})
    with urlopen(request, timeout=20) as response:
        return json.load(response)


class Ledger:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS seen(id TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS wallets(wallet TEXT PRIMARY KEY, started REAL);
        CREATE TABLE IF NOT EXISTS fills(id TEXT PRIMARY KEY, day TEXT, asset TEXT,
            price REAL, shares REAL, cost REAL, source TEXT);
        ''')
        initialize(self.db)

    def spend(self, where='', args=()):
        return self.db.execute('SELECT COALESCE(SUM(cost),0) FROM fills ' + where, args).fetchone()[0]

    def process(self, trade, book, now):
        key = hashlib.sha256(json.dumps(trade, sort_keys=True).encode()).hexdigest()
        day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
        with self.db:
            if not self.db.execute('INSERT OR IGNORE INTO seen VALUES (?)', (key,)).rowcount:
                return 'duplicate'
            age = now - float(trade['timestamp'])
            if trade['side'] != 'BUY' or not 0 <= age <= 120:
                return 'old trade or non-buy'
            source_price = float(trade['price'])
            if not math.isfinite(source_price) or not 0 < source_price < 1:
                return 'invalid source price'
            asset = str(trade['asset'])
            if self.db.execute('SELECT 1 FROM settlements WHERE asset=?', (asset,)).fetchone():
                return 'already settled'
            budget = min(2 - self.spend('WHERE asset=?', (asset,)),
                         8 - self.spend('WHERE day=?', (day,)), 40 - self.spend())
            if budget < 0.01:
                return 'risk limit'
            levels = sorted((float(x['price']), float(x['size'])) for x in book['asks'])
            # All-in simulated cost includes a conservative 2% fee reserve.
            remaining, shares, gross = budget / 1.02, 0.0, 0.0
            for price, size in levels:
                if not all(math.isfinite(x) for x in (price, size)) or not 0 < price < 1 or size <= 0:
                    continue
                if price > source_price + 0.02:
                    break
                take = min(size, remaining / price)
                shares += take
                gross += take * price
                remaining -= take * price
                if remaining < 1e-9:
                    break
            if shares == 0:
                return 'no acceptable liquidity'
            self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?,?)',
                            (key, day, asset, gross / shares, shares, gross * 1.02,
                             json.dumps(trade, sort_keys=True)))
            return 'paper buy: $%.2f, %.4f shares' % (gross * 1.02, shares)


def scan(ledger, wallet):
    # Legacy v1 routes remain documented; explicit schema checks detect API changes.
    trades = get('https://data-api.polymarket.com/trades', user=wallet, limit=500, takerOnly='false')
    if not isinstance(trades, list):
        raise ValueError('Unexpected trades API schema')
    now = time.time()
    row = ledger.db.execute('SELECT started FROM wallets WHERE wallet=?', (wallet,)).fetchone()
    if row is None:
        with ledger.db:
            ledger.db.execute('INSERT INTO wallets VALUES (?,?)', (wallet, now))
        print(wallet, 'initialized; historical trades skipped', flush=True)
        return
    for trade in sorted(trades, key=lambda t: float(t['timestamp'])):
        if float(trade['timestamp']) <= row[0] or now - float(trade['timestamp']) > 120:
            continue
        if trade.get('proxyWallet', '').lower() != wallet or trade.get('side') != 'BUY':
            continue
        book = get('https://clob.polymarket.com/book', token_id=trade['asset'])
        print(wallet, ledger.process(trade, book, time.time()), flush=True)


def main():
    parser = argparse.ArgumentParser(description='CopyLab — simulated money only')
    parser.add_argument('--db', default='paper.sqlite3')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('leaderboard')
    status = sub.add_parser('status')
    status.add_argument('--offline', action='store_true', help='Use saved valuations, expire after 120 seconds')
    status.add_argument('--json', action='store_true', help='Print machine-readable portfolio')
    watch = sub.add_parser('watch')
    watch.add_argument('--wallet', action='append', required=True)
    watch.add_argument('--poll', type=int, default=30)
    watch.add_argument('--once', action='store_true')
    args = parser.parse_args()
    if args.command == 'leaderboard':
        print(json.dumps(get('https://data-api.polymarket.com/v1/leaderboard',
                            limit=10, timePeriod='MONTH', orderBy='PNL'), indent=2))
        return
    ledger = Ledger(args.db)
    if args.command == 'status':
        if not args.offline:
            refresh(ledger, get)
        report = summary(ledger)
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            display(report)
        return
    if args.poll < 5 or any(not re.fullmatch(r'0x[0-9a-fA-F]{40}', w) for w in args.wallet):
        parser.error('Use valid 0x wallet addresses and --poll of at least 5 seconds')
    print('SIMULATION ONLY. Ctrl+C stops monitoring.', flush=True)
    while True:
        for wallet in dict.fromkeys(w.lower() for w in args.wallet):
            try:
                scan(ledger, wallet)
            except (URLError, ValueError, KeyError, TypeError) as error:
                print('Scan failed; no fill assumed:', str(error), flush=True)
        refresh(ledger, get)
        display(summary(ledger))
        if args.once:
            break
        time.sleep(args.poll)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nMonitoring stopped. Ledger saved.')
    except (URLError, ValueError) as error:
        raise SystemExit('Request failed: ' + str(error))
