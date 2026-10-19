"""Bounded, explicit public-history screens, not a profitability guarantee."""
import csv
import io
import json
import math
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from copylab.report import atomic_write


def number(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('Nonfinite trader history value')
    return result


def evaluate(candidate, trades, positions, policy, now, trade_capped=False, closed_capped=False):
    wallet = candidate['wallet']
    cutoff = now - policy['lookback_days']*86400
    trades = [t for t in trades if t.get('proxyWallet','').lower() == wallet
              and cutoff <= number(t['timestamp']) <= now]
    transactions = set()
    days = set()
    for trade in trades:
        # Count distinct transactions, not each fill within one transaction.
        key = trade.get('transactionHash')
        if key:
            transactions.add(key.lower())
        days.add(datetime.fromtimestamp(number(trade['timestamp']), timezone.utc).date().isoformat())
    # Group outcomes together: opposite sides of one market are not independent wins.
    markets = defaultdict(float)
    seen_positions = set()
    for position in positions:
        if not cutoff <= number(position['timestamp']) <= now:
            continue
        if position.get('proxyWallet','').lower() != wallet:
            raise ValueError('Closed history wallet mismatch')
        key = (position['conditionId'], position['asset'])
        if key in seen_positions:
            continue
        seen_positions.add(key)
        markets[position['conditionId']] += number(position['realizedPnl'])
    profits = list(markets.values())
    positive = sum(p for p in profits if p > 0)
    losses = -sum(p for p in profits if p < 0)
    net = sum(profits)
    largest = max(profits, default=0)
    largest = max(0, largest)
    fraction = largest/positive if positive else None
    factor = positive/losses if losses else None
    newest = max((number(t['timestamp']) for t in trades), default=None)
    reasons = []
    checks = [
        (len(transactions) >= policy['min_transactions'], 'fewer than required distinct trade transactions'),
        (len(markets) >= policy['min_closed_markets'], 'fewer than required closed markets'),
        (len(days) >= policy['min_active_days'], 'too few observed trading days'),
        (net > 0, 'sampled closed-market profit is not positive'),
        (net-largest > 0, 'profit does not remain positive without the largest winner'),
        (fraction is not None and fraction <= policy['max_largest_win_share'], 'profit is concentrated in one market'),
        (positive > 0 and (factor is None or factor >= policy['min_profit_factor']), 'profit factor below threshold'),
        (newest is not None and now-newest <= policy['max_hours_since_trade']*3600, 'no recent source activity')]
    reasons = [reason for passed, reason in checks if not passed]
    classification = 'core' if not reasons else 'observe'
    concentrated = net > 0 and (net-largest <= 0 or (fraction is not None and fraction > policy['max_largest_win_share']))
    if classification != 'core' and concentrated and len(transactions) >= policy['experimental_min_transactions'] and len(markets) >= policy['experimental_min_closed_markets'] and len(days) >= policy['experimental_min_active_days'] and newest is not None and now-newest <= policy['max_hours_since_trade']*3600:
        classification = 'experimental'
    # Sample size and spread of outcomes matter more than raw dollar P/L.
    score = (min(len(transactions),500)/50 + min(len(markets),100)/10 + min(len(days),30)/5
             + min(factor if factor is not None else 3,3) - (fraction or 1)*3
             - int(trade_capped) - int(closed_capped))
    return dict(**candidate, classification=classification, selected=False, reasons=reasons,
                transactions=len(transactions), trade_fills=len(trades), active_days=len(days),
                closed_markets=len(markets), sampled_realized_pnl=net,
                profit_ex_largest_winner=net-largest, largest_winner_share=fraction,
                profit_factor=factor, winning_markets=sum(p>0 for p in profits),
                losing_markets=sum(p<0 for p in profits), last_trade=newest,
                trade_history_capped=trade_capped, closed_history_capped=closed_capped,
                score=score, error=None)


def history(fetch, endpoint, wallet, size, pages, cutoff, **params):
    result, keys = [], set()
    for page in range(pages):
        rows = fetch('https://data-api.polymarket.com/'+endpoint, user=wallet,
                     limit=size, offset=page*size, **params)
        if not isinstance(rows,list):
            raise ValueError('Unexpected history schema')
        new = []
        for row in rows:
            key = json.dumps(row,sort_keys=True)
            if key not in keys:
                keys.add(key)
                new.append(row)
        if rows and not new:
            raise ValueError('History pagination repeated a page')
        result.extend(new)
        if len(rows)<size or (rows and min(number(r['timestamp']) for r in rows)<cutoff):
            return result, False
    return result, True


def discover(fetch, policy, directory, now=None):
    now = time.time() if now is None else now
    directory = Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    limiter = threading.Lock()
    next_request = [0.0]
    def limited(url, **params):
        with limiter:
            delay = next_request[0]-time.monotonic()
            if delay>0:
                time.sleep(delay)
            next_request[0]=time.monotonic()+1/policy['requests_per_second']
        return fetch(url, **params)
    candidates = {}
    coverage = []
    for period in policy['periods']:
        rows_seen = 0
        for offset in range(0,policy['top_per_period'],50):
            rows = limited('https://data-api.polymarket.com/v1/leaderboard',category='OVERALL',
                           timePeriod=period,orderBy='PNL',limit=min(50,policy['top_per_period']-offset),offset=offset)
            if not isinstance(rows,list):
                raise ValueError('Unexpected leaderboard schema')
            for row in rows:
                if number(row['pnl'])<=0:
                    continue
                wallet = row['proxyWallet'].lower()
                entry = candidates.setdefault(wallet,dict(wallet=wallet,name=row.get('userName',wallet),leaderboards={}))
                entry['leaderboards'][period]=dict(rank=row['rank'],pnl=row['pnl'])
            rows_seen += len(rows)
            if len(rows)<min(50,policy['top_per_period']-offset):
                break
        coverage.append(dict(period=period,leaderboard_slots=rows_seen))
    print('Screening %d distinct wallets across %d leaderboard slots' %
          (len(candidates),sum(c['leaderboard_slots'] for c in coverage)),flush=True)
    cutoff=now-policy['lookback_days']*86400
    def screen(candidate):
        try:
            trades,trade_capped=history(limited,'trades',candidate['wallet'],500,policy['trade_pages'],cutoff,takerOnly='false')
            positions,closed_capped=history(limited,'closed-positions',candidate['wallet'],50,policy['closed_pages'],cutoff,sortBy='TIMESTAMP',sortDirection='DESC')
            return evaluate(candidate,trades,positions,policy,now,trade_capped,closed_capped)
        except (OSError, ValueError, KeyError, TypeError) as error:
            return dict(**candidate,classification='data_error',selected=False,error=str(error),score=-1000,reasons=['history could not be assessed'])
    results=[]
    with ThreadPoolExecutor(max_workers=policy['workers']) as pool:
        futures=[pool.submit(screen,c) for c in candidates.values()]
        for future in as_completed(futures):
            results.append(future.result())
            if len(results)%20==0:
                print('Assessed %d/%d wallets' % (len(results),len(candidates)),flush=True)
    chosen = select_traders(results, policy, now)
    report=dict(generated=time.time(),sample_as_of=now,coverage=coverage,policy=policy,
                candidate_count=len(candidates),assessed_count=len(results),
                selected_core=sum(r['classification']=='core' for r in chosen),
                selected_experimental=sum(r['classification']=='experimental' for r in chosen),
                errors=sum(r['classification']=='data_error' for r in results),traders=results,
                caveat='Bounded public history, not verified account returns. Trade fills are not independent bets. Concentration is measured per market. High-frequency wallets may have insufficient observed days because history is capped. Open losses, deposits, hedges and off-platform exposures are not assessed, so this is not a risk-adjusted return measure.')
    publish(report, directory)
    print('Selected %d core and %d experimental wallets; %d data errors' %
          (report['selected_core'],report['selected_experimental'],report['errors']),flush=True)
    return report


def select_traders(results, policy, now):
    for row in results:
        row['selected'] = False
    # Activity is a shortlist preference, never a substitute for passing the screens.
    results.sort(key=lambda r:(-r.get('score',-1000), r['wallet']))
    chosen=[]
    for group,limit in [('core',policy['max_core_wallets']),('experimental',policy['max_experimental_wallets'])]:
        candidates=[r for r in results if r['classification']==group and r.get('last_trade') is not None
                    and 0 <= now-r['last_trade'] <= policy['max_hours_since_trade']*3600]
        candidates.sort(key=lambda r:(now-r['last_trade'] > 3600, -r['score'], -r['last_trade'], r['wallet']))
        for row in candidates[:limit]:
            row['selected']=True
            chosen.append(row)
    return chosen


def publish(report, directory):
    directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    results,policy=report['traders'],report['policy']
    chosen=[r for r in results if r['selected']]
    atomic_write(directory/'trader-screening.json',json.dumps(report,indent=2)+'\n')
    fields=['name','wallet','classification','selected','transactions','trade_fills','active_days','closed_markets',
            'sampled_realized_pnl','profit_ex_largest_winner','largest_winner_share','profit_factor',
            'trade_history_capped','closed_history_capped','last_trade','reasons','error']
    stream=io.StringIO(newline='')
    writer=csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore')
    writer.writeheader()
    for row in results:
        safe=dict(row,reasons='; '.join(row['reasons']))
        for key,value in safe.items():
            if isinstance(value,str) and value.startswith(('=','+','-','@','\t','\r')):
                safe[key]="'"+value
        writer.writerow(safe)
    atomic_write(directory/'trader-screening.csv',stream.getvalue())
    watchlist=dict(wallets=[r['wallet'] for r in chosen],generated=report['generated'],
                   screening_file='trader-screening.json',
                   profiles={r['wallet']:dict(name=r['name'],classification=r['classification'],
                       max_cost=policy['experimental_max_cost'] if r['classification']=='experimental' else 2)
                       for r in chosen})
    atomic_write(directory/'screened-watchlist.json',json.dumps(watchlist,indent=2)+'\n')


def validate_policy(policy):
    bounds={'top_per_period':(1,1000),'trade_pages':(1,20),'closed_pages':(1,20),
            'workers':(1,8),'requests_per_second':(1,10),'lookback_days':(1,365),
            'max_core_wallets':(1,50),'max_experimental_wallets':(0,5),
            'refresh_seconds':(900,86400),'experimental_max_cost':(.01,1),
            'min_transactions':(1,100000),'min_closed_markets':(1,10000),
            'min_active_days':(1,365),'max_largest_win_share':(0,1),
            'min_profit_factor':(1,100),'max_hours_since_trade':(1,720),
            'experimental_min_transactions':(1,100000),
            'experimental_min_closed_markets':(1,10000),'experimental_min_active_days':(1,365)}
    for key,(low,high) in bounds.items():
        if not low<=number(policy[key])<=high:
            raise ValueError('Invalid screening policy '+key)
    if not policy.get('periods') or any(p not in ('DAY','WEEK','MONTH','ALL') for p in policy['periods']):
        raise ValueError('Invalid leaderboard periods')
