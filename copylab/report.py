from copylab.presentation import without_quantities
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
    return 'Unknown' if value is None else ('-$%.2f' % abs(value) if value < 0 else '$%.2f' % value)


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
            audit_row=ledger.db.execute('SELECT context FROM execution_audits WHERE id=?',(event_id,)).fetchone()
            execution=json.loads(audit_row[0]) if audit_row else {}
            copied = ledger.db.execute('SELECT checked FROM decisions WHERE id=?', (event_id,)).fetchone()
            events.append(dict(event_id=event_id, type='BUY',
                timestamp=copied[0] if copied else trade.get('timestamp'),
                time_basis='copied time' if copied else 'source trade time; copied time unavailable',
                execution_policy=trade.get('execution_policy','legacy'),source_price=trade.get('price'),
                relative_price_change=price/float(trade['price'])-1 if trade.get('price') else None,
                source_age_seconds=execution.get('source_age_seconds'),entry_drag=execution.get('entry_drag'),
                sizing=trade.get('sizing'),sizing_target_cost=trade.get('sizing',{}).get('target_cost'),
                sizing_relative_commitment=trade.get('sizing',{}).get('relative_commitment'),
                sizing_reason=trade.get('sizing',{}).get('reason'),
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
    screen_path = directory/'trader-screening.json'
    if screen_path.exists():
        try:
            screen = json.loads(screen_path.read_text())
            report['screening'] = {key:screen[key] for key in ('generated','sample_as_of','candidate_count','assessed_count','selected_core','selected_experimental','errors','policy','coverage','caveat')}
            report['screening']['stale'] = report['generated_timestamp']-screen['generated'] > 7200
            report['screening']['selected'] = [r for r in screen['traders'] if r['selected']]
        except (OSError, ValueError, KeyError, TypeError) as error:
            report['screening_error'] = str(error)
    from copylab.combined_report import combine,markdown
    database=ledger.db.execute('PRAGMA database_list').fetchone()[2]
    root=Path(database).resolve().parent if database else directory.resolve().parent
    report=combine(report,root,directory,report['generated_timestamp'])
    atomic_write(directory/'trade-report.json', json.dumps(without_quantities(report), indent=2)+'\n')
    fields = ['number','bot','type','time','time_basis','market','outcome','wallet','price','second_price','price_basis','exit_reason','net_return_percent',
              'gross','fee_reserve','cost_basis','cash_change','realized_pnl','execution_policy','source_price','relative_price_change','source_age_seconds','entry_drag','sizing_target_cost','sizing_relative_commitment','sizing_reason','asset','source_transaction','event_id']
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
    writer.writeheader()
    writer.writerows({k: ("'"+v if isinstance(v,str) and v.startswith(('=','+','-','@','\t','\r')) else v)
                     for k,v in event.items()} for event in report['trades'])
    atomic_write(directory/'trade-history.csv', stream.getvalue())
    audit_fields=['checked','policy','side','wallet','market','outcome','asset','source_price','entry_price',
                  'relative_price_change','source_age_seconds','entry_drag','best_ask','best_bid','sizing_policy','sizing_target_cost','sizing_source_commitment','sizing_typical_commitment','sizing_relative_commitment','sizing_history_markets','sizing_reason','copied_exit_proceeds_total','result']
    audit_stream=io.StringIO(newline='')
    audit_writer=csv.DictWriter(audit_stream,fieldnames=audit_fields,extrasaction='ignore')
    audit_writer.writeheader()
    for context, in ledger.db.execute('SELECT context FROM execution_attempts ORDER BY attempt'):
        row=json.loads(context)
        audit_writer.writerow({k:("'"+v if isinstance(v,str) and v.startswith(('=','+','-','@','\t','\r')) else v) for k,v in row.items()})
    atomic_write(directory/'execution-quality.csv',audit_stream.getvalue())
    atomic_write(directory/'trade-report.md',markdown(report))
    if docx_python:
        subprocess.run([docx_python, '-m', 'copylab.report_docx',
                        str(directory/'trade-report.json'), str(directory/'trade-report.docx')],
                       check=True, timeout=45, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return report


def write_trade_log(ledger, path):
    """A readable trade-only view; diagnostics and mark updates stay elsewhere."""
    report=collect(ledger)
    lines=[]
    for event in report['trades']:
        parts=[event['time'],'PAPER '+event['type'],event['market'],event['outcome'],
               'Price $%.4f'%event['price']]
        if event['type']=='BUY':
            parts.append('Spent '+dollars(-event['cash_change']))
        else:
            parts.append('Received '+dollars(event['cash_change']))
            parts.append('Realized P/L '+dollars(event['realized_pnl']))
        lines.append(' | '.join(part for part in parts if part))
    atomic_write(path,'\n'.join(lines)+ ('\n' if lines else ''))
