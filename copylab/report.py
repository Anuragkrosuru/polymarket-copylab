"""Export the live paper ledger without mixing in synthetic scenarios."""
import csv
import io
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from copylab.portfolio import summary


def timestamp(value):
    if value is None:
        return 'Not recorded'
    try:
        zone = ZoneInfo('America/Detroit')
    except ZoneInfoNotFoundError:
        zone = timezone.utc
    return datetime.fromtimestamp(value, zone).strftime('%Y-%m-%d %I:%M:%S %p %Z')


def dollars(value):
    return 'Unknown' if value is None else '$%.2f' % value


def collect(ledger, now=None):
    now = time.time() if now is None else now
    # A read transaction keeps the balances and trade list on the same snapshot.
    ledger.db.execute('BEGIN')
    try:
        account = summary(ledger, now)
        fills = ledger.db.execute('SELECT id,day,asset,price,shares,cost,source FROM fills ORDER BY rowid').fetchall()
        events = []
        sources = {}
        for event_id, day, asset, price, shares, cost, raw in fills:
            trade = json.loads(raw)
            sources[asset] = trade
            copied = ledger.db.execute('SELECT checked FROM decisions WHERE id=?', (event_id,)).fetchone()
            events.append(dict(event_id=event_id, type='BUY',
                timestamp=copied[0] if copied else trade.get('timestamp'),
                time_basis='copied time' if copied else 'source trade time; copied time unavailable',
                asset=asset, market=trade.get('title', asset), outcome=trade.get('outcome', ''),
                wallet=trade.get('proxyWallet', ''), source_transaction=trade.get('transactionHash', ''),
                shares=shares, price=price, gross=price*shares, fee_reserve=cost-price*shares,
                cost_basis=cost, cash_change=-cost, realized_pnl=None))
        sales = ledger.db.execute('''SELECT e.id,f.asset,SUM(e.shares),SUM(e.cost),SUM(e.proceeds),
            MAX(e.checked),MIN(f.source) FROM exits e JOIN fills f ON f.id=e.fill_id
            GROUP BY e.id,f.asset ORDER BY MAX(e.checked)''').fetchall()
        for event_id, asset, shares, cost, proceeds, checked, raw in sales:
            trade = json.loads(raw)
            event = ledger.db.execute('SELECT source FROM events WHERE id=?', (event_id,)).fetchone()
            sale_source = json.loads(event[0]) if event else {}
            gross = proceeds/.98
            events.append(dict(event_id=event_id, type='SELL', timestamp=checked, time_basis='copied time',
                asset=asset, market=trade.get('title', asset), outcome=trade.get('outcome', ''),
                wallet=trade.get('proxyWallet', ''), source_transaction=sale_source.get('transactionHash', ''),
                shares=shares, price=gross/shares, gross=gross, fee_reserve=gross-proceeds,
                cost_basis=cost, cash_change=proceeds, realized_pnl=proceeds-cost))
        for asset, shares, cost, payout, checked in ledger.db.execute('SELECT * FROM settlements ORDER BY checked'):
            trade = sources.get(asset, {})
            events.append(dict(event_id='settlement-'+asset, type='SETTLEMENT', timestamp=checked,
                time_basis='paper settlement check time', asset=asset, market=trade.get('title', asset),
                outcome=trade.get('outcome', ''), wallet='See source buys', source_transaction='',
                shares=shares, price=payout/shares if shares else 0, gross=payout, fee_reserve=0,
                cost_basis=cost, cash_change=payout, realized_pnl=payout-cost))
        events.sort(key=lambda e: (e['timestamp'] or 0, e['type'] != 'BUY'))
        for number, event in enumerate(events, 1):
            event['number'] = number
            event['time'] = timestamp(event['timestamp'])
        return dict(generated=timestamp(now), generated_timestamp=now,
                    scope='Live paper account only; synthetic integration scenarios are excluded.',
                    account=account, trades=events,
                    fee_reserve_total=sum(e['fee_reserve'] for e in events),
                    explanation='Fees are fixed simulation reserves, not verified exchange charges. Open gains are bid estimates. Realized gains come from copied sales and confirmed paper settlements.')
    finally:
        ledger.db.commit()


def atomic_write(path, content):
    path = Path(path)
    temporary = path.with_name(path.name+'.tmp')
    temporary.write_text(content, encoding='utf-8')
    os.replace(temporary, path)


def export(ledger, directory, docx_python=None, now=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    report = collect(ledger, now)
    atomic_write(directory/'trade-report.json', json.dumps(report, indent=2)+'\n')
    fields = ['number','type','time','time_basis','market','outcome','wallet','shares','price',
              'gross','fee_reserve','cost_basis','cash_change','realized_pnl','asset','source_transaction','event_id']
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
    writer.writeheader()
    writer.writerows({k: ("'"+v if isinstance(v,str) and v.startswith(('=','+','-','@','\t','\r')) else v)
                     for k,v in event.items()} for event in report['trades'])
    atomic_write(directory/'trade-history.csv', stream.getvalue())
    account = report['account']
    lines = ['# Paper trading trade and profit report', '', 'Updated: '+report['generated'], '', report['scope'], '',
             'Cash: '+dollars(account['cash']), 'Equity: '+dollars(account['equity']),
             'Total profit or loss: '+dollars(account['total_pnl']),
             'Realized profit or loss: '+dollars(account['realized_pnl']),
             'Estimated open profit or loss: '+dollars(account['unrealized_pnl']),
             'Total entry and exit fee reserves: '+dollars(report['fee_reserve_total']), '', report['explanation'], '']
    if not report['trades']:
        lines += ['No qualifying copied trades have been recorded yet. Profit is $0 because the account has not traded.', '']
    for event in report['trades']:
        lines += ['## %d %s %s' % (event['number'], event['type'], event['market']), '',
                  event['time']+' ('+event['time_basis']+')', 'Outcome: '+event['outcome'],
                  'Shares: %.6f | Price: $%.4f' % (event['shares'],event['price']),
                  'Cash change: '+dollars(event['cash_change'])+' | Fee reserve: '+dollars(event['fee_reserve']),
                  'Realized profit or loss: '+('Recorded on exit' if event['realized_pnl'] is None else dollars(event['realized_pnl'])),
                  'Source wallet: '+event['wallet'], '']
    atomic_write(directory/'trade-report.md', '\n'.join(lines))
    if docx_python:
        subprocess.run([docx_python, '-m', 'copylab.report_docx',
                        str(directory/'trade-report.json'), str(directory/'trade-report.docx')],
                       check=True, timeout=45, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return report
