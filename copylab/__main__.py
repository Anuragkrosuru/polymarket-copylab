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
from copylab import runtime, sizing, paper_config
from copylab.execution import POLICY_VERSION, levels, entry_quality, bid_fingerprint, audit


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
    def __init__(self, path, initial_cash=None, goal=None, compound=None):
        self.db = sqlite3.connect(path, timeout=30)
        self.paper=paper_config.initialize(self.db,initial_cash,goal,compound)
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS seen(id TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS wallets(wallet TEXT PRIMARY KEY, started REAL);
        CREATE TABLE IF NOT EXISTS fills(id TEXT PRIMARY KEY, day TEXT, asset TEXT,
            price REAL, shares REAL, cost REAL, source TEXT);
        ''')
        runtime.initialize(self.db)
        sizing.initialize(self.db)
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
        trade = dict(trade, copy_group=copy_group, execution_policy=POLICY_VERSION)
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
            account=summary(self,now)
            risk_scale=paper_config.scale(self.paper,account['realized_pnl'])
            available=account['cash'] if self.paper['compound'] else self.paper['initial_cash']-self.spend()
            budget = min(min(2*risk_scale, max_cost) - self.spend('WHERE asset=?', (asset,)), available)
            condition = trade.get('conditionId')
            if condition:
                market_spend = sum(cost for cost,source in self.db.execute('SELECT cost,source FROM fills')
                                   if json.loads(source).get('conditionId') == condition)
                budget = min(budget, 2*risk_scale-market_spend)
            if copy_group == 'experimental':
                source_wallet = trade.get('proxyWallet','').lower()
                today = [(cost,json.loads(source)) for cost,source in self.db.execute(
                    'SELECT cost,source FROM fills WHERE day=?',(day,))]
                group_spend = sum(cost for cost,source in today if source.get('copy_group')=='experimental')
                wallet_spend = sum(cost for cost,source in today if source.get('proxyWallet','').lower()==source_wallet)
                budget = min(budget, 2*risk_scale-group_spend, risk_scale-wallet_spend)
            if budget < 0.01:
                return 'risk limit'
            ask_levels = levels(book, 'asks')
            # All-in simulated cost includes a conservative 2% fee reserve.
            remaining, shares, gross = budget / 1.02, 0.0, 0.0
            for price, size in ask_levels:
                if not all(math.isfinite(x) for x in (price, size)) or not 0 < price < 1 or size <= 0:
                    continue
                if price > source_price + min(0.02, source_price * 0.05) + 1e-12:
                    break
                take = min(size, remaining / price)
                shares += take
                gross += take * price
                remaining -= take * price
                if remaining < 1e-9:
                    break
            if shares == 0:
                valid_prices = [price for price,size in ask_levels if math.isfinite(price) and math.isfinite(size) and 0 < price < 1 and size > 0]
                if valid_prices and min(valid_prices) > source_price + min(0.02, source_price*0.05) + 1e-12:
                    return 'entry price exceeds 5 percent or 2 cent limit'
                return 'no acceptable liquidity'
            minimum = float(book.get('min_order_size', 0))
            if not math.isfinite(minimum) or minimum < 0:
                raise ValueError('Invalid minimum order size')
            if shares + 1e-9 < minimum:
                return 'below market minimum order size'
            rejected, _ = entry_quality(book, shares, gross*1.02)
            if rejected:
                return rejected
            self.db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?,?)',
                            (key, day, asset, gross / shares, shares, gross * 1.02,
                             json.dumps(trade, sort_keys=True)))
            self.db.execute('DELETE FROM marks WHERE asset=?', (asset,))
            return 'paper buy: $%.2f, %.4f shares' % (gross * 1.02, shares)

    def sell(self, trade, book, now, key, retry=False):
        wallet=trade.get('proxyWallet','').lower()
        lots=[lot for lot in open_lots(self.db) if lot[1]==str(trade['asset'])
              and json.loads(lot[4]).get('proxyWallet','').lower()==wallet]
        if not retry:
            # A newer source sale replaces older exit requests on these lots.
            for fill_id,asset,available,cost,source in lots:
                self.db.execute('DELETE FROM pending_exits WHERE fill_id=?',(fill_id,))
                self.db.execute('INSERT INTO pending_exits VALUES (?,?,?,?,?,?,?)',
                    (key,fill_id,available,json.dumps(trade),float(trade['timestamp'])+120,now,''))
        requests={r[0]:r[1] for r in self.db.execute(
            'SELECT fill_id,target_shares FROM pending_exits WHERE id=?',(key,))}
        bids=[list(row) for row in levels(book,'bids')]
        fingerprint=bid_fingerprint(book)
        # Validate minimum before touching fills so rejected partial sales remain pending.
        minimum=float(book.get('min_order_size',0))
        if not math.isfinite(minimum) or minimum<0:
            raise ValueError('Invalid minimum order size')
        allocations=[]
        total_shares,total_proceeds=0.0,0.0
        for fill_id,asset,available,cost,source in lots:
            if fill_id not in requests:
                continue
            previous=self.db.execute('SELECT shares FROM exits WHERE id=? AND fill_id=?',(key,fill_id)).fetchone()
            requested=max(0,requests[fill_id]-(previous[0] if previous else 0))
            remaining=min(available,requested)
            target=remaining
            proceeds=0.0
            for level in bids:
                price,size=level
                if price<float(trade['price'])-.02:
                    break
                take=min(size,remaining)
                proceeds+=take*price*.98
                remaining-=take
                level[1]-=take
                if remaining<=1e-9:
                    break
            sold=target-remaining
            if sold>1e-9:
                allocations.append((fill_id,sold,cost*sold/available,proceeds))
                total_shares+=sold
                total_proceeds+=proceeds
        self.db.execute('UPDATE pending_exits SET attempted=?,fingerprint=? WHERE id=?',(now,fingerprint,key))
        if total_shares==0:
            result='no matching holding or acceptable exit liquidity'
        elif total_shares+1e-9<minimum:
            result='below market minimum exit size'
        else:
            for fill_id,sold,cost,proceeds in allocations:
                self.db.execute("""INSERT INTO exits VALUES (?,?,?,?,?,?)
                    ON CONFLICT(id,fill_id) DO UPDATE SET shares=shares+excluded.shares,
                    cost=cost+excluded.cost,proceeds=proceeds+excluded.proceeds,checked=excluded.checked""",
                    (key,fill_id,sold,cost,proceeds,now))
            self.db.execute('DELETE FROM marks WHERE asset=?',(str(trade['asset']),))
            result='paper sell: $%.2f proceeds, %.4f shares'%(total_proceeds,total_shares)
        remaining_lots={lot[0] for lot in open_lots(self.db)}
        for fill_id,target in requests.items():
            sold=self.db.execute('SELECT shares FROM exits WHERE id=? AND fill_id=?',(key,fill_id)).fetchone()
            if fill_id not in remaining_lots or (sold and sold[0]+1e-9>=target):
                self.db.execute('DELETE FROM pending_exits WHERE id=? AND fill_id=?',(key,fill_id))
        return result


def retry_exits(ledger, fetch=None, now=None):
    fetch=get if fetch is None else fetch
    live_clock=now is None
    now=time.time() if now is None else now
    with ledger.db:
        ledger.db.execute('DELETE FROM pending_exits WHERE expires<?',(now,))
        ledger.db.execute('DELETE FROM pending_exits WHERE fill_id IN (SELECT id FROM fills WHERE asset IN (SELECT asset FROM settlements))')
    requests=ledger.db.execute('SELECT id,MIN(source),MAX(attempted),MIN(fingerprint) FROM pending_exits GROUP BY id').fetchall()
    for key,raw,attempted,fingerprint in requests:
        if now-attempted<5:
            continue
        trade=json.loads(raw)
        try:
            book=fetch('https://clob.polymarket.com/book',token_id=trade['asset'])
            if str(book.get('asset_id'))!=str(trade['asset']):
                raise ValueError('Exit book identity mismatch')
            attempt_time=time.time() if live_clock else now
            if attempt_time-float(trade['timestamp'])>120:
                with ledger.db:
                    ledger.db.execute('DELETE FROM pending_exits WHERE id=?',(key,))
                continue
            if bid_fingerprint(book)==fingerprint:
                # Re-reading unchanged depth must not invent replenished liquidity.
                with ledger.db:
                    ledger.db.execute('UPDATE pending_exits SET attempted=? WHERE id=?',(attempt_time,key))
                continue
            with ledger.db:
                ledger.db.execute('BEGIN IMMEDIATE')
                result=ledger.sell(trade,book,attempt_time,key,retry=True)
            audit(ledger,trade,book,'exit retry: '+result,attempt_time)
            print('Exit retry:',result,flush=True)
        except (OSError,ValueError,KeyError,TypeError) as error:
            print('Exit retry failed; request remains bounded by expiry:',str(error),flush=True)


def scan(ledger, wallet, fetch=None, clock=None, allow_buys=True, max_cost=2, copy_group='manual', prefetched=None):
    fetch = get if fetch is None else fetch
    clock = time.time if clock is None else clock
    trades = prefetched if prefetched is not None else fetch('https://data-api.polymarket.com/trades', user=wallet, limit=500, takerOnly='false')
    if not isinstance(trades, list):
        raise ValueError('Unexpected trades API schema')
    now = clock()
    # Validate the complete timestamp set before advancing scan health.
    ordered = sorted(trades, key=lambda t: float(t['timestamp']))
    sizing.observe(ledger, wallet, ordered, now)
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
                if book.get('market') is not None and book['market'] != trade.get('conditionId'):
                    raise ValueError('Order book market mismatch')
                if str(book.get('asset_id')) != str(trade['asset']):
                    raise ValueError('Order book identity mismatch')
                allocation = sizing.target(ledger, trade, max_cost) if trade['side'] == 'BUY' else None
                if allocation:
                    trade = dict(trade, sizing=allocation)
                result = ledger.process(trade, book, clock(), max_cost=allocation['target_cost'] if allocation else max_cost, copy_group=copy_group)
                with ledger.db:
                    ledger.db.execute('INSERT OR REPLACE INTO decisions VALUES (?,?,?)',
                                      (event_id(trade), clock(), result))
                audit(ledger,trade,book,result,clock())
                print(wallet, result, flush=True)
            except (OSError, ValueError, KeyError, TypeError) as error:
                errors.append(str(error))
                print('Trade skipped after error; will retry while fresh:', str(error), flush=True)
    runtime.scan_result(ledger, wallet, trades, '; '.join(errors) or None, now)


def main():
    parser = argparse.ArgumentParser(description='CopyLab — simulated money only')
    parser.add_argument('--db', default=paper_config.active_database('copy'))
    parser.add_argument('--initial-cash',type=float)
    parser.add_argument('--goal',type=float)
    parser.add_argument('--compound',action='store_true',default=None)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('leaderboard')
    discovery = sub.add_parser('discover', help='Screen broad leaderboards and publish a dynamic watchlist')
    discovery.add_argument('--policy', default='screening-policy.json')
    discovery.add_argument('--output-dir', default='reports')
    discovery.add_argument('--defer-first', action='store_true', help='Wait one refresh interval before the first discovery run')
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
    watch.add_argument('--poll', type=int, default=5)
    watch.add_argument('--report-every', type=int, default=30, help='Seconds between saved reports and snapshots')
    watch.add_argument('--once', action='store_true')
    watch.add_argument('--trade-log', help='Write only copied trades and settlements to this file')
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
        if args.defer_first:
            policy = json.loads(Path(args.policy).read_text())
            validate_policy(policy)
            time.sleep(policy['refresh_seconds'])
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
    ledger = Ledger(args.db,args.initial_cash,args.goal,args.compound)
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
    if args.report_every < 5:
        parser.error('--report-every must be at least 5 seconds')
    if args.duration is not None and args.duration <= 0:
        parser.error('--duration must be positive')
    with runtime.watcher_lock(args.db):
        started = time.monotonic()
        print('SIMULATION ONLY. Ctrl+C stops monitoring.', flush=True)
        previous_cycle = None
        last_report = None
        while True:
            cycle_started = time.monotonic()
            observed_interval = cycle_started-previous_cycle if previous_cycle is not None else None
            previous_cycle = cycle_started
            try:
                targets = runtime.watch_targets(ledger, args.wallet, args.wallet_file)
            except (OSError, ValueError, KeyError, TypeError) as error:
                print('Watchlist unavailable; new entries paused:', str(error), flush=True)
                targets = runtime.watch_targets(ledger, [], None)
            with ledger.db:
                ledger.db.execute('INSERT OR REPLACE INTO monitor VALUES (1,?,?)',
                                  (time.time(), json.dumps(targets)))
            for wallet, trades, error in runtime.wallet_feeds(targets, get):
                target = targets[wallet]
                if error is not None:
                    runtime.scan_result(ledger, wallet, error=error)
                    print('Wallet feed failed:', wallet, error, flush=True)
                    continue
                try:
                    scan(ledger, wallet, allow_buys=target['allow_buys'], max_cost=target['max_cost']*paper_config.scale(ledger.paper,summary(ledger)['realized_pnl']), copy_group=target['classification'], prefetched=trades)
                except (OSError, ValueError, KeyError, TypeError) as error:
                    runtime.scan_result(ledger, wallet, error=str(error))
                    print('Wallet scan failed:', str(error), flush=True)
            retry_exits(ledger)
            refresh(ledger, get)
            with ledger.db:
                ledger.db.execute('INSERT OR REPLACE INTO polling VALUES (1,?,?,?,?)',
                                  (time.time(), args.poll, observed_interval, time.monotonic()-cycle_started))
            if args.trade_log:
                from copylab.report import write_trade_log
                try:
                    write_trade_log(ledger,args.trade_log)
                except (OSError,ValueError) as error:
                    print('Trade log update failed:',str(error),flush=True)
            elapsed = time.monotonic() - started
            finished = args.once or (args.duration is not None and elapsed >= args.duration)
            if last_report is None or time.monotonic()-last_report >= args.report_every or finished:
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
                last_report = time.monotonic()
            elapsed = time.monotonic()-started
            if finished or (args.duration is not None and elapsed >= args.duration):
                break
            delay = max(0, args.poll-(time.monotonic()-cycle_started))
            if args.duration is not None:
                delay = min(delay, max(0, args.duration-elapsed))
            time.sleep(delay)




if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nMonitoring stopped. Ledger saved.')
    except (OSError, ValueError) as error:
        raise SystemExit('Request failed: ' + str(error))
