"""Quote validation and entry quality checks for the paper execution model."""
import hashlib
import json
import math

from copylab.portfolio import bid_value

POLICY_VERSION = 'copy-v3-entry-depth-exit-retry-sizing-v1'
MAX_ENTRY_DRAG = 0.10


def levels(book, side):
    rows=[]
    for row in book[side]:
        price,size=float(row['price']),float(row['size'])
        if not math.isfinite(price) or not math.isfinite(size) or not 0<price<1 or size<=0:
            raise ValueError('Invalid '+side+' order book level')
        rows.append((price,size))
    return sorted(rows,reverse=side=='bids')


def entry_quality(book, shares, cost):
    bids=levels(book,'bids')
    asks=levels(book,'asks')
    if not bids or not asks:
        return 'entry requires both bid and ask liquidity',None
    if bids[0][0] > asks[0][0]+1e-12:
        raise ValueError('Crossed order book; entry rejected')
    if sum(size for _,size in bids)+1e-9<shares:
        return 'insufficient bid depth for copied position',None
    value,_=bid_value(book,shares)
    drag=1-value/cost
    if drag>MAX_ENTRY_DRAG+1e-12:
        return 'entry spread and fee drag exceeds 10 percent',drag
    return None,drag


def bid_fingerprint(book):
    return hashlib.sha256(json.dumps(levels(book,'bids')).encode()).hexdigest()


def audit(ledger,trade,book,result,checked):
    from copylab.__main__ import event_id
    key=event_id(trade)
    quote={}
    for side in ('asks','bids'):
        try:
            values=levels(book,side)
            quote['best_'+side[:-1]]=values[0][0] if values else None
        except (ValueError,KeyError,TypeError):
            quote['best_'+side[:-1]]=None
    fill=ledger.db.execute('SELECT price,shares,cost FROM fills WHERE id=?',(key,)).fetchone()
    source=float(trade['price'])
    if not math.isfinite(source):
        source=None
    context=dict(policy=POLICY_VERSION,market=trade.get('title',''),outcome=trade.get('outcome',''),side=trade['side'],wallet=trade.get('proxyWallet'),asset=trade['asset'],
                 source_price=source,source_timestamp=trade['timestamp'],checked=checked,
                 source_age_seconds=checked-float(trade['timestamp']),result=result,**quote)
    if trade.get('sizing'):
        context.update({'sizing_'+key:value for key,value in trade['sizing'].items()})
    if fill:
        price,shares,cost=fill
        _,drag=entry_quality(book,shares,cost)
        context.update(entry_price=price,relative_price_change=price/source-1 if source and source>0 else None,entry_drag=drag)
    if trade['side']=='SELL':
        sold,proceeds=ledger.db.execute('SELECT COALESCE(SUM(shares),0),COALESCE(SUM(proceeds),0) FROM exits WHERE id=?',(key,)).fetchone()
        context.update(copied_exit_shares_total=sold,copied_exit_proceeds_total=proceeds)
    with ledger.db:
        ledger.db.execute('INSERT INTO execution_attempts(id,checked,context) VALUES (?,?,?)',(key,checked,json.dumps(context)))
        ledger.db.execute('INSERT OR REPLACE INTO execution_audits VALUES (?,?,?)',
                          (key,checked,json.dumps(context)))
