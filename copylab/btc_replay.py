"""Chronological recorded-quote replay. No interpolation or fabricated fills."""
import argparse
import collections
import json
import sqlite3
from pathlib import Path
from urllib.parse import quote
from copylab.btc15 import Engine,validate_book
from copylab.presentation import without_quantities


def replay(records,markets,strategy):
    e=Engine(':memory:',strategy=strategy,initial_cash=100,goal=1000,compound=True)
    reasons=collections.Counter();invalid=0;last_books={};peak=100.;drawdown=0.;unknown=0
    for row in records:
        at,condition,raw=row
        market=markets[condition];payload=json.loads(raw);books={}
        for token,book in payload['books'].items():
            corresponding=markets.get(book.get('market'))
            if not corresponding:continue
            try:validate_book(book,corresponding,token,at);books[token]=book
            except (ValueError,KeyError,TypeError):invalid+=1
        e.service_exits(at,books)
        if all(token in books for token in market['tokens']):
            reasons[e.tick(at,market,payload.get('rh'),books)]+=1
        else:reasons['missing valid books']+=1
        state=e.state(books)
        if state['equity'] is not None:
            peak=max(peak,state['equity']);drawdown=max(drawdown,peak-state['equity'])
        else:unknown+=1
        last_books=books
    state=e.state(last_books)
    trades=state.pop('trades')
    return dict(**state,entries=len(trades),closed=sum(t['closed'] is not None for t in trades),
                unclosed=sum(t['closed'] is None for t in trades),
                winning_exits=sum(t['closed'] is not None and t['proceeds']>t['cost'] for t in trades),
                max_observed_drawdown=drawdown,unknown_equity_observations=unknown,
                invalid_book_observations=invalid,decisions=dict(reasons),trades=without_quantities(trades))


def run(database,metadata,output,end_id=None):
    source=sqlite3.connect('file:'+quote(str(Path(database).resolve()))+'?mode=ro',uri=True)
    limit=end_id or source.execute('SELECT MAX(id) FROM samples').fetchone()[0]
    rows=source.execute('SELECT received,condition,payload FROM samples WHERE id<=? ORDER BY received,id',(limit,)).fetchall()
    actual=source.execute("SELECT COUNT(*),COALESCE(SUM(proceeds-cost),0) FROM trades WHERE strategy='trend' AND closed<=?",(rows[-1][0],)).fetchone()
    source.close()
    markets=json.loads(Path(metadata).read_text())
    missing={r[1] for r in rows}-set(markets)
    if missing:raise ValueError('Missing verified market metadata')
    conditions=list(dict.fromkeys(row[1] for row in rows));cut=len(conditions)*2//3
    partitions={'development':conditions[:cut],'held_out':conditions[cut:]}
    result=dict(label='Recorded quote replay, not executable returns or proof of profit.',
                frozen_end_id=limit,observations=len(rows),intervals=len(conditions),
                start_timestamp=rows[0][0],end_timestamp=rows[-1][0],
                policy='Fixed v2 rules compared with v1. No parameter grid search. Last third of intervals used for temporal validation; inspected during development, not an independent future trial.',
                limitations='HTTP samples have gaps; no quote interpolation, atomic fills, queue priority or real order rejection model. Unknown final equity stays unknown. Drawdown is measured only at priced observations. Each partition starts with fresh $100.',partitions={})
    for name,selected in partitions.items():
        records=[r for r in rows if r[1] in selected]
        result['partitions'][name]=dict(intervals=len(selected),observations=len(records),
                                        v1=replay(records,markets,'trend'),v2=replay(records,markets,'scalp'))
    reproduced=replay(rows,markets,'trend')
    result['baseline_reproduction']=dict(entries=reproduced['entries'],realized_pnl=reproduced['realized_pnl'],
        actual_closed_trades=actual[0],actual_realized_pnl=actual[1],
        matches=actual[0]==reproduced['closed'] and abs(actual[1]-reproduced['realized_pnl'])<1e-7)
    Path(output).write_text(json.dumps(result,indent=2)+'\n')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database',required=True);p.add_argument('--metadata',required=True)
    p.add_argument('--output',default='btc-scalp-replay-results.json');p.add_argument('--end-id',type=int)
    a=p.parse_args();r=run(a.database,a.metadata,a.output,a.end_id)
    for name,part in r['partitions'].items():
        for version in ['v1','v2']:
            s=part[version];print(name,version,'entries',s['entries'],'realized P/L',round(s['realized_pnl'],2),'equity',s['equity'],'unclosed',s['unclosed'])


if __name__=='__main__':main()
