"""Read-only aggregation of the three independent live paper accounts."""
import json
import math
import sqlite3
from datetime import datetime
from pathlib import Path
from urllib.parse import quote


def finite(value):
    return isinstance(value,(int,float)) and math.isfinite(value)


def status(path, now):
    try:
        data=json.loads(path.read_text())
        checked=datetime.fromisoformat(data['updated_utc'].replace('Z','+00:00')).timestamp()
        return data,checked,-3<=now-checked<=120
    except (OSError,ValueError,KeyError,TypeError):
        return {},None,False


def rows(path, table):
    uri='file:'+quote(str(path.resolve()))+'?mode=ro'
    connection=sqlite3.connect(uri,uri=True,timeout=5)
    connection.row_factory=sqlite3.Row
    try:
        return [dict(r) for r in connection.execute('SELECT * FROM '+table)]
    finally:
        connection.close()


def event(bot, key, kind, at, market, outcome, cost, cash, price=None, realized=None, **extra):
    return dict(bot=bot,event_id=key,type=kind,timestamp=at,market=market,outcome=outcome,
                cost_basis=cost,cash_change=cash,price=price,realized_pnl=realized,
                time_basis='paper event time' if at is not None else 'exact settlement time not recorded',**extra)


def unavailable(name,error):
    return dict(name=name,available=False,initial_cash=40,cash=None,equity=None,total_pnl=None,
                realized_pnl=None,unrealized_pnl=None,positions=[],entries=0,exits=0,
                status='Data unavailable: '+error,source_updated=None,stale=True),[]


def btc(root, directory, now):
    name='BTC trend bot'
    try:
        trades=rows(root/'btc15.sqlite3','trades')
        snapshot,checked,fresh=status(directory/'btc15'/'status.json',now)
        spent=sum(t['cost'] for t in trades)
        cash=40-spent+sum(t['proceeds'] or 0 for t in trades)
        realized=sum(t['proceeds']-t['cost'] for t in trades if t['closed'] is not None)
        opened=[t for t in trades if t['closed'] is None]
        snapshot_ids={t['id'] for t in snapshot.get('trades',[]) if t['closed'] is None}
        matched=snapshot_ids=={t['id'] for t in opened} and finite(snapshot.get('cash')) and abs(snapshot['cash']-cash)<1e-7
        equity=cash if not opened else snapshot.get('equity') if fresh and matched else None
        if not finite(equity):equity=None
        positions=[];events=[]
        for t in trades:
            market=json.loads(t['market']);title=market['title'];outcome=['Up','Down'][t['direction']]
            price=t['cost']/t['shares']
            events.append(event(name,'btc-'+str(t['id'])+'-buy','BUY',t['opened'],title,outcome,t['cost'],-t['cost'],price,
                                price_basis='includes modeled entry costs',strategy=t.get('strategy','lead')))
            if t['closed'] is not None:
                events.append(event(name,'btc-'+str(t['id'])+'-exit','SETTLEMENT' if t['exit_reason']=='confirmed settlement' else 'SELL',t['closed'],title,outcome,t['cost'],t['proceeds'],
                                    t['proceeds']/t['shares'],t['proceeds']-t['cost'],price_basis='net of modeled exit costs',
                                    exit_reason=t['exit_reason'],net_return_percent=100*(t['proceeds']/t['cost']-1)))
            else:
                value=equity-cash if equity is not None and len(opened)==1 else None
                positions.append(dict(title=title,outcome=outcome,price=price,cost=t['cost'],value=value,
                                      pnl=None if value is None else value-t['cost'],
                                      target_proceeds=t['cost']*1.05 if t.get('strategy')=='trend' else None))
        return dict(name=name,available=True,initial_cash=40,cash=cash,equity=equity,
                    total_pnl=None if equity is None else equity-40,realized_pnl=realized,
                    unrealized_pnl=None if equity is None else equity-cash-sum(t['cost'] for t in opened),
                    positions=positions,entries=len(trades),exits=len(trades)-len(opened),
                    status=snapshot.get('decision','No status available'),source_updated=checked,stale=not fresh),events
    except (OSError,sqlite3.Error,ValueError,KeyError,TypeError) as error:
        return unavailable(name,type(error).__name__)


def arbitrage(root, directory, now):
    name='Arbitrage bot'
    try:
        trades=rows(root/'arbitrage.sqlite3','pairs')
        snapshot,checked,fresh=status(directory/'arbitrage'/'status.json',now)
        cash=40;realized=0;expected=0;positions=[];events=[]
        for t in trades:
            market=json.loads(t['market']);q=json.loads(t['quote']);cost=q['cost'];cash-=cost
            prices=q.get('entry_prices') or [c/1.1/q['shares_each'] for c in q['leg_costs']]
            labels=[x['outcome'] if 'outcome' in x else 'Outcome '+str(i+1) for i,x in enumerate(market['tokens'])]
            events.append(event(name,'arb-'+t['condition']+'-buy','PAIR BUY',t['opened'],market['question'],
                                ' + '.join(labels),cost,-cost,prices[0],second_price=prices[1],
                                price_basis='outcome prices before modeled costs',expected_gain=q['expected_gain']))
            if t['payout'] is not None:
                cash+=t['payout'];realized+=t['payout']-cost
                events.append(event(name,'arb-'+t['condition']+'-settle','SETTLEMENT',t.get('settled'),market['question'],
                                    'Complete pair',cost,t['payout'],realized=t['payout']-cost))
            else:
                expected+=q['expected_gain']
                positions.append(dict(title=market['question'],outcome='Complete pair',cost=cost,value=None,pnl=None,
                                      expected_payout=q['expected_payout'],expected_gain=q['expected_gain']))
        equity=None if positions else cash
        return dict(name=name,available=True,initial_cash=40,cash=cash,equity=equity,
                    total_pnl=None if equity is None else equity-40,realized_pnl=realized,
                    unrealized_pnl=None if positions else 0,expected_open_gain=expected,
                    positions=positions,entries=len(trades),exits=sum(t['payout'] is not None for t in trades),
                    status='%s/%s markets checked in latest sweep; %s errors' % (snapshot.get('checked','?'),snapshot.get('universe','?'),snapshot.get('errors','?')),
                    source_updated=checked,stale=not fresh),events
    except (OSError,sqlite3.Error,ValueError,KeyError,TypeError) as error:
        return unavailable(name,type(error).__name__)


def combine(report, root, directory, now):
    from copylab.report import timestamp
    copy=report['account']
    bot=dict(name='Copy bot',available=True,initial_cash=40,positions=copy['positions'],
             entries=copy['fills'],exits=copy['sales']+copy['settled_positions'],
             status='Copies screened traders with relative commitment sizing',source_updated=copy.get('last_refresh'),
             stale=bool(copy['unpriced_positions']))
    bot.update({key:copy[key] for key in ('cash','equity','total_pnl','realized_pnl','unrealized_pnl')})
    bots=[bot];events=[dict(t,bot='Copy bot',price_basis='before modeled fee reserve') for t in report['trades']]
    for collector in (arbitrage,btc):
        account,history=collector(Path(root),Path(directory),now)
        bots.append(account);events.extend(history)
    def total(key):
        values=[b.get(key) for b in bots]
        return sum(values) if all(finite(v) for v in values) else None
    report['combined']={key:total(key) for key in ('initial_cash','cash','equity','total_pnl','realized_pnl','unrealized_pnl')}
    report['combined']['expected_arbitrage_gain']=bots[1].get('expected_open_gain')
    report['bots']=bots
    report['scope']='Three independent live paper accounts starting at $40 each ($120 combined). Synthetic scenarios excluded.'
    report['explanation']=('Balances are independent and collected at different times. Open values are bid estimates, not realized gains. '
                           'Arbitrage expected payouts are not current equity. Unknown valuations make combined equity/PnL unknown. '
                           'Copy prices exclude fixed reserves; BTC prices include modeled entry costs or subtract exit costs. No real orders.')
    events.sort(key=lambda t:(t['timestamp'] is None,t['timestamp'] or 0,t['type']!='BUY',t['bot'],t['event_id']))
    for i,t in enumerate(events,1):
        t['number']=i;t['time']=timestamp(t['timestamp'])
    report['trades']=events
    return report


def price_text(event):
    first=event.get('price')
    text='Not recorded' if first is None else '$%.4f'%first
    if event.get('second_price') is not None:text+=' / $%.4f'%event['second_price']
    return text


def markdown(report):
    from copylab.report import dollars,timestamp
    c=report['combined']
    lines=['# All three paper bots trade and profit report','','Updated '+report['generated'],'',report['scope'],'',
           'Combined cash: '+dollars(c['cash']),'Combined equity: '+dollars(c['equity']),
           'Combined total P/L: '+dollars(c['total_pnl']),'Combined realized P/L: '+dollars(c['realized_pnl']),
           'Combined estimated open P/L: '+dollars(c['unrealized_pnl']),'',report['explanation'],'']
    for b in report['bots']:
        lines += ['## '+b['name'],'','Cash '+dollars(b['cash'])+' | Equity '+dollars(b['equity'])+' | Total P/L '+dollars(b['total_pnl']),
                  'Realized P/L '+dollars(b['realized_pnl'])+' | Estimated open P/L '+dollars(b['unrealized_pnl']),
                  '%d entries | %d exits/settlements'%(b['entries'],b['exits']),
                  'Last source update '+timestamp(b['source_updated'])+(' | STALE OR UNAVAILABLE' if b['stale'] else ''),b['status'],'']
        for p in b['positions']:
            lines+=['Open: '+p['title']+' | '+p['outcome'],'Cost '+dollars(p['cost'])+' | Estimated value '+dollars(p['value'])+' | P/L '+dollars(p['pnl'])]
            if 'expected_payout' in p:lines+=['Expected payout '+dollars(p['expected_payout'])+'; not realized profit or current equity.']
        if not b['entries']:lines+=['No recorded entries.']
        lines+=['']
    lines+=['## Complete trade history','']
    if not report['trades']:lines+=['No recorded trades.','']
    for t in report['trades']:
        lines+=['### %d %s %s'%(t['number'],t['bot'],t['type']),'',t['time']+' | '+t['time_basis'],
                t['market']+' | '+t['outcome'],'Price '+price_text(t)+' | '+t.get('price_basis',''),
                'Cash change '+dollars(t['cash_change'])+' | Realized P/L '+('On exit' if t['realized_pnl'] is None else dollars(t['realized_pnl']))]
        if t.get('exit_reason'):lines += ['Exit reason: '+t['exit_reason']]
        lines+=['']
    return '\n'.join(lines)
