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
from urllib.error import URLError, HTTPError
from copylab.portfolio import initialize, refresh, summary, display, open_lots
from copylab import runtime


def get(url, **params):
    request = Request(url + '?' + urlencode(params), headers={'User-Agent': 'CopyLab/0.2'})
    for attempt in range(3):
        try:
            with urlopen(request, timeout=15) as response:
                return json.load(response)
        except HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
        except OSError:
            if attempt == 2:
                raise
        time.sleep(2 ** attempt)


def event_id(trade):
    # Display names, avatars and titles may change after a fill is published.
    fields = ('transactionHash', 'proxyWallet', 'asset', 'side', 'timestamp', 'price', 'size')
    stable = {k: trade.get(k) for k in fields}
    for field in ('transactionHash', 'proxyWallet'):
        if isinstance(stable[field], str):
            stable[field] = stable[field].lower()
    return hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()


def legacy_id(trade):
    return hashlib.sha256(json.dumps(trade, sort_keys=True).encode()).hexdigest()


class Ledger:
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=30)
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS seen(id TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS wallets(wallet TEXT PRIMARY KEY, started REAL);
        CREATE TABLE IF NOT EXISTS fills(id TEXT PRIMARY KEY, day TEXT, asset TEXT,
            price REAL, shares REAL, cost REAL, source TEXT);
        ''')
        runtime.initialize(self.db)
        initialize(self.db)
        # Migrate previously copied fills to stable identities without replaying them.
        with self.db:
            for source, in self.db.execute('SELECT source FROM fills'):
                self.db.execute('INSERT OR IGNORE INTO seen VALUES (?)', (event_id(json.loads(source)),))

    def spend(self, where='', args=()):
        return self.db.execute('SELECT COALESCE(SUM(cost),0) FROM fills ' + where, args).fetchone()[0]

    def known(self, trade):
        return self.db.execute('SELECT 1 FROM seen WHERE id IN (?,?)',
                               (event_id(trade), legacy_id(trade))).fetchone() is not None

    def process(self, trade, book, now, max_cost=2, copy_group='manual'):
        if self.known(trade):
            return 'duplicate'
        key = event_id(trade)
        trade = dict(trade, copy_group=copy_group)
        day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
        with self.db:
            if not self.db.execute('INSERT OR IGNORE INTO seen VALUES (?)', (key,)).rowcount:
                return 'duplicate'
            self.db.execute('INSERT OR IGNORE INTO events VALUES (?,?)', (key, json.dumps(trade, sort_keys=True)))
            age = now - float(trade['timestamp'])
            if trade['side'] not in ('BUY', 'SELL') or not 0 <= age <= 120:
                return 'old trade or unsupported side'
            source_price = float(trade['price'])
            if not math.isfinite(source_price) or not 0 < source_price < 1:
                return 'invalid source price'
            asset = str(trade['asset'])
            if self.db.execute('SELECT 1 FROM settlements WHERE asset=?', (asset,)).fetchone():
                return 'already settled'
            if trade['side'] == 'SELL':
                return self.sell(trade, book, now, key)
            budget = min(min(2, max_cost) - self.spend('WHERE asset=?', (asset,)),
                         8 - self.spend('WHERE day=?', (day,)), 40 - self.spend())
            condition = trade.get('conditionId')
            if condition:
                market_spend = sum(cost for cost,source in self.db.execute('SELECT cost,source FROM fills')
                                   if json.loads(source).get('conditionId') == condition)
                budget = min(budget, 2-market_spend)
            if copy_group == 'experimental':
                source_wallet = trade.get('proxyWallet','').lower()
                today = [(cost,json.loads(source)) for cost,source in self.db.execute(
                    'SELECT cost,source FROM fills WHERE day=?',(day,))]
                group_spend = sum(cost for cost,source in today if source.get('copy_group')=='experimental')
                wallet_spend = sum(cost for cost,source in today if source.get('proxyWallet','').lower()==source_wallet)
                budget = min(budget, 2-group_spend, 1-wallet_spend)
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
            minimum = float(book.get('min_order_size', 0))
            if not math.isfinite(minimum) or minimum < 0:
                raise ValueError('Invalid minimum order size')
            if shares + 1e-9 < minimum:
                return 'below market minimum order size'
            self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?,?)',
                            (key, day, asset, gross / shares, shares, gross * 1.02,
                             json.dumps(trade, sort_keys=True)))
            self.db.execute('DELETE FROM marks WHERE asset=?', (asset,))
            return 'paper buy: $%.2f, %.4f shares' % (gross * 1.02, shares)

    def sell(self, trade, book, now, key):
        # A source SELL requests a full exit of this source's copied token holdings.
        wallet = trade.get('proxyWallet', '').lower()
        lots = [lot for lot in open_lots(self.db) if lot[1] == str(trade['asset'])
                and json.loads(lot[4]).get('proxyWallet', '').lower() == wallet]
        levels = sorted(([float(x['price']), float(x['size'])] for x in book['bids']), reverse=True)
        if any(not all(math.isfinite(x) for x in level) or not 0 <= level[0] <= 1 or level[1] <= 0
               for level in levels):
            raise ValueError('Invalid sell order book')
        total_shares, total_proceeds = 0.0, 0.0
        for fill_id, asset, available, cost, source in lots:
            remaining, proceeds = available, 0.0
            for level in levels:
                price, size = level
                if price < float(trade['price']) - 0.02:
                    break
                take = min(size, remaining)
                proceeds += take * price * .98
                remaining -= take
                level[1] -= take
                if remaining <= 1e-9:
                    break
            sold = available - remaining
            if sold > 1e-9:
                self.db.execute('INSERT INTO exits VALUES (?,?,?,?,?,?)',
                                (key, fill_id, sold, cost * sold / available, proceeds, now))
                total_shares += sold
                total_proceeds += proceeds
        if total_shares == 0:
            return 'no matching holding or acceptable exit liquidity'
        minimum = float(book.get('min_order_size', 0))
        if not math.isfinite(minimum) or minimum < 0:
            raise ValueError('Invalid minimum order size')
        if total_shares + 1e-9 < minimum:
            self.db.execute('DELETE FROM exits WHERE id=?', (key,))
            return 'below market minimum exit size'
        self.db.execute('DELETE FROM marks WHERE asset=?', (str(trade['asset']),))
        return 'paper sell: $%.2f proceeds, %.4f shares' % (total_proceeds, total_shares)


def scan(ledger, wallet, fetch=None, clock=None, allow_buys=True, max_cost=2, copy_group='manual'):
    fetch = get if fetch is None else fetch
    clock = time.time if clock is None else clock
    trades = fetch('https://data-api.polymarket.com/trades', user=wallet, limit=500, takerOnly='false')
    if not isinstance(trades, list):
        raise ValueError('Unexpected trades API schema')
    now = clock()
    # Validate the complete timestamp set before advancing scan health.
    ordered = sorted(trades, key=lambda t: float(t['timestamp']))
    errors = []
    row = ledger.db.execute('SELECT started FROM wallets WHERE wallet=?', (wallet,)).fetchone()
    if row is None:
        with ledger.db:
            ledger.db.execute('INSERT INTO wallets VALUES (?,?)', (wallet, now))
        print(wallet, 'initialized; historical trades skipped', flush=True)
    else:
        if len(ordered) >= 500 and float(ordered[0]['timestamp']) > max(row[0], now-120):
            errors.append('Trade feed reached 500 records inside the copy window; activity may be missing')
        for trade in ordered:
            try:
                if not row[0] < float(trade['timestamp']) <= now or now - float(trade['timestamp']) > 120:
                    continue
                if trade.get('proxyWallet', '').lower() != wallet or trade.get('side') not in ('BUY', 'SELL'):
                    continue
                if trade['side'] == 'BUY' and not allow_buys:
                    continue
                if ledger.known(trade):
                    continue
                book = fetch('https://clob.polymarket.com/book', token_id=trade['asset'])
                if str(book.get('asset_id')) != str(trade['asset']):
                    raise ValueError('Order book identity mismatch')
                result = ledger.process(trade, book, clock(), max_cost=max_cost, copy_group=copy_group)
                with ledger.db:
                    ledger.db.execute('INSERT OR REPLACE INTO decisions VALUES (?,?,?)',
                                      (event_id(trade), clock(), result))
                print(wallet, result, flush=True)
            except (OSError, ValueError, KeyError, TypeError) as error:
                errors.append(str(error))
                print('Trade skipped after error; will retry while fresh:', str(error), flush=True)
    runtime.scan_result(ledger, wallet, trades, '; '.join(errors) or None, now)


def main():
    parser = argparse.ArgumentParser(description='CopyLab — simulated money only')
    parser.add_argument('--db', default='paper.sqlite3')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('leaderboard')
    discovery = sub.add_parser('discover', help='Screen broad leaderboards and publish a dynamic watchlist')
    discovery.add_argument('--policy', default='screening-policy.json')
    discovery.add_argument('--output-dir', default='reports')
    discovery.add_argument('--repeat', action='store_true', help='Refresh hourly using the policy interval')
    simulation = sub.add_parser('simulate', help='Offline synthetic scenarios; not a profitability backtest')
    simulation.add_argument('--output', default='simulation-results.json')
    report_parser = sub.add_parser('report', help='Export every paper trade and current profit')
    report_parser.add_argument('--output-dir', default='reports')
    report_parser.add_argument('--docx-python', help='Python executable with python-docx installed')
    history = sub.add_parser('history', help='Saved live-paper polling snapshots')
    history.add_argument('--limit', type=int, default=20)
    status = sub.add_parser('status')
    status.add_argument('--offline', action='store_true', help='Use saved valuations, expire after 120 seconds')
    status.add_argument('--json', action='store_true', help='Print machine-readable portfolio')
    watch = sub.add_parser('watch')
    watch.add_argument('--wallet', action='append', default=[])
    watch.add_argument('--wallet-file', help='JSON file containing a wallets list')
    watch.add_argument('--poll', type=int, default=30)
    watch.add_argument('--once', action='store_true')
    watch.add_argument('--report-dir', help='Refresh trade report after every poll')
    watch.add_argument('--docx-python', help='Python executable with python-docx for Word reports')
    watch.add_argument('--duration', type=int, help='Stop after this many seconds; omit to keep running')
    args = parser.parse_args()
    if args.command == 'leaderboard':
        print(json.dumps(get('https://data-api.polymarket.com/v1/leaderboard',
                            limit=10, timePeriod='MONTH', orderBy='PNL'), indent=2))
        return
    if args.command == 'simulate':
        from copylab.simulation import run
        run(args.output)
        return
    if args.command == 'discover':
        from copylab.screening import discover, validate_policy
        from pathlib import Path
        while True:
            policy = json.loads(Path(args.policy).read_text())
            validate_policy(policy)
            try:
                discover(get, policy, args.output_dir)
            except (OSError, ValueError, KeyError, TypeError) as error:
                if not args.repeat:
                    raise
                print('Discovery failed; prior watchlist retained:', str(error), flush=True)
            if not args.repeat:
                break
            time.sleep(policy['refresh_seconds'])
        return
    ledger = Ledger(args.db)
    if args.command == 'report':
        from copylab.report import export
        export(ledger, args.output_dir, args.docx_python)
        print('Paper trade report saved to', args.output_dir)
        return
    if args.command == 'history':
        if not 1 <= args.limit <= 1000:
            parser.error('--limit must be between 1 and 1000')
        rows = ledger.db.execute('SELECT checked, report FROM snapshots ORDER BY id DESC LIMIT ?',
                                 (args.limit,)).fetchall()
        print(json.dumps([dict(checked=r[0], **json.loads(r[1])) for r in reversed(rows)], indent=2))
        return
    if args.command == 'status':
        if not args.offline:
            refresh(ledger, get)
        report = summary(ledger)
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            display(report)
        return
    profiles = {}
    if args.wallet_file:
        from pathlib import Path
        config = json.loads(Path(args.wallet_file).read_text())
        if not isinstance(config, dict) or not isinstance(config.get('wallets'), list):
            parser.error('--wallet-file must contain a wallets list')
        profiles = config.get('profiles', {})
        configured_wallets = config['wallets']
    else:
        configured_wallets = []
    if not args.wallet and not args.wallet_file:
        parser.error('Specify --wallet or --wallet-file')
    if args.poll < 5 or any(not isinstance(w, str) or not re.fullmatch(r'0x[0-9a-fA-F]{40}', w) for w in args.wallet+configured_wallets):
        parser.error('Use valid 0x wallet addresses and --poll of at least 5 seconds')
    if args.duration is not None and args.duration <= 0:
        parser.error('--duration must be positive')
    with runtime.watcher_lock(args.db):
        started = time.monotonic()
        print('SIMULATION ONLY. Ctrl+C stops monitoring.', flush=True)
        while True:
            try:
                targets = runtime.watch_targets(ledger, args.wallet, args.wallet_file)
            except (OSError, ValueError, KeyError, TypeError) as error:
                print('Watchlist unavailable; new entries paused:', str(error), flush=True)
                targets = runtime.watch_targets(ledger, [], None)
            with ledger.db:
                ledger.db.execute('INSERT OR REPLACE INTO monitor VALUES (1,?,?)',
                                  (time.time(), json.dumps(targets)))
            for wallet, target in sorted(targets.items()):
                try:
                    scan(ledger, wallet, allow_buys=target['allow_buys'], max_cost=target['max_cost'], copy_group=target['classification'])
                except (OSError, ValueError, KeyError, TypeError) as error:
                    runtime.scan_result(ledger, wallet, error=str(error))
                    print('Wallet scan failed:', str(error), flush=True)
            refresh(ledger, get)
            report = summary(ledger)
            runtime.save_snapshot(ledger, report)
            display(report)
            if args.report_dir:
                from copylab.report import export
                import subprocess
                try:
                    export(ledger, args.report_dir, args.docx_python)
                except (OSError, ValueError, subprocess.SubprocessError) as error:
                    print('Report export failed; watcher continues:', str(error), flush=True)
            elapsed = time.monotonic() - started
            if args.once or (args.duration is not None and elapsed >= args.duration):
                break
            delay = args.poll if args.duration is None else min(args.poll, args.duration-elapsed)
            time.sleep(max(0, delay))



if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nMonitoring stopped. Ledger saved.')
    except (OSError, ValueError) as error:
        raise SystemExit('Request failed: ' + str(error))
