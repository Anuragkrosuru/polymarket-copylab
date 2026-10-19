"""Independent complete-set paper scanner. Never submits orders or signs transactions."""
import argparse
import csv
import io
import json
import math
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError

from copylab.__main__ import get
from copylab.runtime import watcher_lock
from copylab.presentation import without_quantities
from copylab import paper_config

# Legacy synthetic scenarios use this reserve; live scans require published fees.
FEE_RESERVE = 0.10
FEE_SCHEDULES = {}


def walk(book, side, quantity):
    levels = []
    for row in book.get(side, []):
        p, s = float(row['price']), float(row['size'])
        if not (math.isfinite(p) and math.isfinite(s) and 0 < p < 1 and s > 0):
            raise ValueError('Invalid depth')
        levels.append((p, s))
    levels.sort(reverse=side == 'bids')
    remaining, value = quantity, 0.0
    for price, size in levels:
        used = min(remaining, size)
        value += used * price
        remaining -= used
        if remaining < 1e-8:
            return value
    raise ValueError('Insufficient depth')


def quote(market, books, budget=6, require_edge=True):
    tokens = market.get('tokens', [])
    if (not market.get('active') or market.get('closed') or
            not market.get('accepting_orders') or market.get('neg_risk') or
            len(tokens) != 2 or len({t['token_id'] for t in tokens}) != 2):
        raise ValueError('Not an eligible ordinary binary market')
    quantity = max(5.0, float(market.get('minimum_order_size', 5)),math.floor(budget*.95))
    if not math.isfinite(quantity) or quantity <= 0:
        raise ValueError('Invalid minimum size')
    costs = []
    if len(books) != 2:
        raise ValueError('Missing books')
    for token, book in zip(tokens, books):
        if str(book.get('asset_id')) != str(token['token_id']) or book.get('market') != market['condition_id']:
            raise ValueError('Book identity mismatch')
        quantity = max(quantity, float(book.get('min_order_size', 0)))
    if len(books) != 2 or not math.isfinite(quantity):
        raise ValueError('Missing books')
    prices = []
    for book in books:
        if not book.get('asks') or not book.get('bids'):
            raise ValueError('Missing bid or ask depth')
        ask = min(float(x['price']) for x in book['asks'])
        bid = max(float(x['price']) for x in book['bids'])
        if bid >= ask:
            raise ValueError('Crossed book')
        gross = walk(book, 'asks', quantity)
        prices.append(gross/quantity)
        if 'paper_fee_rate' in market:
            rate = float(market['paper_fee_rate'])
            if not math.isfinite(rate) or not 0 <= rate <= .2:
                raise ValueError('Invalid market fee rate')
            remaining, fees = quantity, 0.0
            for row in sorted(book['asks'], key=lambda r:float(r['price'])):
                p, size = float(row['price']), float(row['size'])
                used = min(remaining, size)
                fees += round(used*rate*p*(1-p), 5)
                remaining -= used
                if remaining < 1e-8:
                    break
            # Half a cent per complete set reserved for adverse execution.
            costs.append(gross + fees + .0025*quantity)
        else:
            costs.append(gross * (1 + FEE_RESERVE))
    cost = sum(costs)
    if cost > budget + 1e-9:
        raise ValueError('Pair exceeds budget')
    if require_edge and quantity - cost < max(0.02, cost * 0.01):
        raise ValueError('No edge after reserve')
    return {'shares_each': quantity, 'cost': cost, 'expected_payout': quantity,
            'expected_gain': quantity - cost, 'leg_costs': costs, 'entry_prices': prices,
            'fee_rate': market.get('paper_fee_rate'), 'fee_model': 'published schedule plus buffer' if 'paper_fee_rate' in market else 'legacy reserve'}


class Account:
    def __init__(self, path, initial_cash=None, goal=None, compound=None):
        self.db = sqlite3.connect(path)
        self.paper=paper_config.initialize(self.db,initial_cash,goal,compound)
        self.db.execute('CREATE TABLE IF NOT EXISTS pairs(condition TEXT PRIMARY KEY, opened REAL, market TEXT, quote TEXT, payout REAL)')

    def state(self):
        rows = self.db.execute('SELECT condition,opened,market,quote,payout FROM pairs ORDER BY opened').fetchall()
        trades = [dict(condition=c, opened=t, market=json.loads(m), **json.loads(q), payout=p) for c,t,m,q,p in rows]
        spent = sum(t['cost'] for t in trades)
        paid = sum(t['payout'] or 0 for t in trades)
        cash=self.paper['initial_cash']-spent+paid
        return {**paper_config.progress(self.paper,None if any(t['payout'] is None for t in trades) else cash),
                'initial_cash': self.paper['initial_cash'], 'cash': cash, 'lifetime_spend': spent,
                'realized_pnl': sum(t['payout']-t['cost'] for t in trades if t['payout'] is not None),
                'open_expected_gain': sum(t['expected_gain'] for t in trades if t['payout'] is None),
                'trades': trades}

    def enter(self, market, proposed, now=None):
        now = time.time() if now is None else now
        state = self.state()
        day = datetime.fromtimestamp(now, timezone.utc).date()
        daily = sum(t['cost'] for t in state['trades'] if datetime.fromtimestamp(t['opened'], timezone.utc).date() == day)
        risk_scale=paper_config.scale(self.paper,state['realized_pnl'])
        if (proposed['cost'] > 6*risk_scale or proposed['cost'] > state['cash'] or
                (not self.paper['compound'] and state['lifetime_spend']+proposed['cost'] > self.paper['initial_cash'])
                or daily+proposed['cost'] > 12*risk_scale):
            return False
        with self.db:
            result = self.db.execute('INSERT OR IGNORE INTO pairs VALUES (?,?,?,?,NULL)',
                                    (market['condition_id'], now, json.dumps(market), json.dumps(proposed)))
        return result.rowcount == 1

    def settle(self, condition, market):
        tokens = market.get('tokens', [])
        if (market.get('condition_id') != condition or not market.get('closed') or len(tokens) != 2
                or sum(t.get('winner') is True for t in tokens) != 1):
            return
        row = self.db.execute('SELECT market,quote FROM pairs WHERE condition=? AND payout IS NULL', (condition,)).fetchone()
        if not row or {t['token_id'] for t in json.loads(row[0])['tokens']} != {t['token_id'] for t in tokens}:
            return
        with self.db:
            self.db.execute('UPDATE pairs SET payout=? WHERE condition=? AND payout IS NULL',
                            (json.loads(row[1])['shares_each'], condition))


def books_for(market):
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=2) as pool:
        books = list(pool.map(lambda t: get('https://clob.polymarket.com/book', token_id=t['token_id']), market['tokens']))
    if time.monotonic()-started > 2:
        raise ValueError('Book fetch exceeded two-second freshness window')
    now = time.time()
    for book in books:
        timestamp = float(book.get('timestamp',0))/1000
        if not math.isfinite(timestamp) or not -1 <= now-timestamp <= 5:
            raise ValueError('Stale exchange book timestamp')
    return books


def discover(limit):
    markets = []
    # Bound discovery to 500 most active markets; skip neg-risk before fetching books.
    for offset in range(0, 500, 100):
        page = get('https://gamma-api.polymarket.com/markets', active='true', closed='false',
                   limit=100, offset=offset, order='volume24hr', ascending='false')
        for m in page:
            if m.get('acceptingOrders') and not m.get('negRisk') and m.get('conditionId'):
                fee = m.get('feeSchedule', {})
                if m.get('feesEnabled') is False:
                    rate = 0.0
                elif (m.get('feesEnabled') is True and fee.get('exponent') == 1
                      and isinstance(fee.get('rate'), (int,float)) and math.isfinite(fee['rate'])
                      and 0 <= fee['rate'] <= .2):
                    rate = fee['rate']
                else:
                    continue
                FEE_SCHEDULES[m['conditionId']] = rate
                markets.append(m['conditionId'])
                if len(markets) >= limit:
                    return markets
        if len(page) < 100:
            break
    return markets


def write_reports(account, directory, health):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    state = account.state()
    for t in state['trades']:
        t['entry_prices'] = t.get('entry_prices') or [cost/(1+FEE_RESERVE)/t['shares_each'] for cost in t['leg_costs']]
    state.update(health)
    state['updated_utc'] = datetime.now(timezone.utc).isoformat()
    state['model'] = ('PAPER ONLY: conditional full-pair fills at twice-checked asks, published market fees plus half-cent per complete-set execution buffer; '
                      'no atomic execution, latency, queue or settlement-delay guarantee. Expected gains are not realized PnL.')
    for name, data in [('status.json', json.dumps(without_quantities(state), indent=2))]:
        temporary = directory / (name+'.tmp')
        temporary.write_text(data+'\n')
        temporary.replace(directory/name)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['opened_utc','market','first_price','second_price','cost','expected_payout','expected_gain','actual_payout','realized_pnl'])
    lines = []
    for t in state['trades']:
        stamp = datetime.fromtimestamp(t['opened'], timezone.utc).isoformat()
        writer.writerow([stamp,t['market']['question'],*t['entry_prices'],t['cost'],t['expected_payout'],t['expected_gain'],t['payout'],'' if t['payout'] is None else t['payout']-t['cost']])
        lines.append(f"{stamp} PAPER PAIR | {t['market']['question']} | entry prices ${t['entry_prices'][0]:.4f} / ${t['entry_prices'][1]:.4f} | cost ${t['cost']:.4f} | expected gain ${t['expected_gain']:.4f} | payout {t['payout']}")
    (directory/'trades.csv').write_text(output.getvalue())
    Path('arbitrage.log').write_text('\n'.join(lines)+ ('\n' if lines else ''))


def scan(account, conditions, progress=None):
    health = dict(checked_utc=datetime.now(timezone.utc).isoformat(), universe=len(conditions), checked=0, rejected=0, errors=0, entered=0, reasons={})
    for t in account.state()['trades']:
        if t['payout'] is None:
            try:
                account.settle(t['condition'], get('https://clob.polymarket.com/markets/'+t['condition']))
            except Exception:
                health['errors'] += 1
    state=account.state()
    pair_budget=min(state['cash'],6*paper_config.scale(account.paper,state['realized_pnl']))
    def inspect(condition):
        market = get('https://clob.polymarket.com/markets/'+condition)
        if market.get('condition_id') != condition:
            raise ValueError('Market identity mismatch')
        if condition not in FEE_SCHEDULES:
            raise ValueError('Unknown published fees; skip')
        market['paper_fee_rate'] = FEE_SCHEDULES[condition]
        first = quote(market, books_for(market),budget=pair_budget,require_edge=False)
        return market, first

    pending = [c for c in conditions if not account.db.execute('SELECT 1 FROM pairs WHERE condition=?',(c,)).fetchone()]
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(inspect,c):c for c in pending}
        for future in as_completed(futures):
            condition = futures[future]
            try:
                market, first = future.result()
                closest = health.get('closest_candidate')
                if closest is None or first['expected_gain'] > closest['expected_gain']:
                    health['closest_candidate'] = dict(market=market['question'],condition=condition,
                        entry_prices=first['entry_prices'],cost=first['cost'],expected_payout=first['expected_payout'],
                        expected_gain=first['expected_gain'],fee_rate=market['paper_fee_rate'])
                if first['expected_gain'] < max(.02,first['cost']*.01):
                    raise ValueError('No edge after published fees and buffer')
                # Refresh BOTH legs again on the main thread immediately before entry.
                second = quote(market, books_for(market),budget=pair_budget)
                if second['cost'] > first['cost'] + .001:
                    raise ValueError('Quote deteriorated on recheck')
                health['entered'] += int(account.enter(market, second))
            except (ValueError, KeyError, TypeError, HTTPError) as error:
                if isinstance(error, HTTPError) and error.code != 404:
                    health['errors'] += 1
                    reason = 'HTTP '+str(error.code)
                else:
                    health['rejected'] += 1
                    reason = 'Unavailable market/book (404)' if isinstance(error,HTTPError) else str(error)
                health['reasons'][reason] = health['reasons'].get(reason,0)+1
            except Exception as error:
                health['errors'] += 1
                print(f'Market {condition}: {type(error).__name__}: {error}',flush=True)
            finally:
                health['checked'] += 1
                if progress:
                    progress(dict(health,cycle_in_progress=True))
    return health


def scenarios():
    # Invented examples demonstrate accounting, not observed opportunities or returns.
    q = 5
    cost = q*(0.42+0.43)*(1+FEE_RESERVE)
    first = q*0.42*(1+FEE_RESERVE)
    unwind = q*0.38*(1-FEE_RESERVE)
    return {'label': 'SYNTHETIC ONLY — not live performance',
            'profitable_pair': {'cost': cost, 'settlement': q, 'profit': q-cost},
            'normal_spread': {'cost': q*(0.50+0.51)*(1+FEE_RESERVE), 'action': 'reject'},
            'second_leg_failure_stress': {'first_leg_cost': first, 'unwind_proceeds': unwind,
                                          'loss': unwind-first, 'note': 'If bids vanish, unwind may fail and exposure remains.'}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', default=paper_config.active_database('arbitrage'))
    parser.add_argument('--initial-cash',type=float)
    parser.add_argument('--goal',type=float)
    parser.add_argument('--compound',action='store_true',default=None)
    parser.add_argument('--report-dir', default='reports/arbitrage')
    parser.add_argument('--limit', type=int, default=100)
    parser.add_argument('--poll', type=float, default=10)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--simulate', action='store_true')
    args = parser.parse_args()
    if args.simulate:
        print(json.dumps(scenarios(), indent=2))
        return
    if not 1 <= args.limit <= 100 or args.poll < 5:
        parser.error('limit must be 1–100; poll at least 5 seconds')
    with watcher_lock(args.database):
        account = Account(args.database,args.initial_cash,args.goal,args.compound)
        conditions, discovered = [], 0
        while True:
            started = time.monotonic()
            try:
                if time.time()-discovered > 600:
                    conditions = discover(args.limit)
                    discovered = time.time()
                health = scan(account, conditions, lambda h: write_reports(account, args.report_dir, h))
                health['cycle_in_progress'] = False
                health['cycle_seconds'] = time.monotonic()-started
                write_reports(account, args.report_dir, health)
                print(json.dumps({k:v for k,v in health.items()}), flush=True)
            except Exception as error:
                print(f'Scan failed: {type(error).__name__}: {error}', flush=True)
            if args.once:
                break
            time.sleep(max(args.poll-(time.monotonic()-started), 1))


if __name__ == '__main__':
    main()
