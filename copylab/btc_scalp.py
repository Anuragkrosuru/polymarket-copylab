"""Experimental cost-aware scalping guards, not a proven return forecast."""
VERSION='btc-scalp-v2'
MAX_ENTRY_DRAG=.07
STOP_LOSS=.10
TAKE_PROFIT=.05
MAX_HOLD=60
DAILY_TURNOVER_BASE=40.0  # Reused cash turnover, not simultaneous exposure.
POSITION_BASE=2.0  # 5% of starting cash plus realized PnL.


def policy():
    return dict(version=VERSION,max_entry_drag=MAX_ENTRY_DRAG,stop_loss=STOP_LOSS,
                take_profit=TAKE_PROFIT,max_hold=MAX_HOLD,position_fraction=.05,daily_turnover_fraction=1.0)


def entry_guard(db,market,direction,now,book):
    import json
    bids=sorted([(float(r['price']),float(r['size'])) for r in book['bids']],reverse=True)
    asks=sorted([(float(r['price']),float(r['size'])) for r in book['asks']])
    bid,ask=bids[0][0],asks[0][0]
    bid_size=sum(size for price,size in bids if price>=bid-.01-1e-9)
    ask_size=sum(size for price,size in asks if price<=ask+.01+1e-9)
    imbalance=(bid_size-ask_size)/(bid_size+ask_size)
    diagnostics=dict(near_touch_imbalance=imbalance,best_bid=bid,best_ask=ask)
    if imbalance<.10:
        return False,'scalp: insufficient visible bid support',diagnostics
    prior=db.execute('SELECT payload FROM samples WHERE condition=? AND received BETWEEN ? AND ? ORDER BY ABS(received-?) LIMIT 1',
                     (market['condition'],now-7,now-3,now-5)).fetchone()
    if not prior:return False,'scalp: collecting book confirmation',diagnostics
    previous=json.loads(prior[0])['books'].get(market['tokens'][direction])
    if not previous or not previous.get('bids'):
        return False,'scalp: missing previous bid',diagnostics
    old_bid=max(float(r['price']) for r in previous['bids'])
    change=bid-old_bid;diagnostics['five_second_bid_change']=change
    if change < -1e-9:return False,'scalp: outcome bid is falling',diagnostics
    if change > .02+1e-9:return False,'scalp: avoid chasing a price jump',diagnostics
    return True,'scalp: visible book confirms trend',diagnostics


def reentry(db,market,direction,now):
    from copylab.btc_trend import reentry as original
    rows=db.execute('SELECT closed,cost,proceeds FROM trades WHERE closed IS NOT NULL ORDER BY closed DESC LIMIT 1').fetchall()
    if rows and rows[0][2]<rows[0][1] and now-rows[0][0]<120:
        return False,'scalp: two-minute cooldown after loss'
    recent=db.execute("SELECT closed,cost,proceeds FROM trades WHERE strategy='scalp' AND closed IS NOT NULL ORDER BY closed DESC LIMIT 3").fetchall()
    if len(recent)==3 and all(r[2]<r[1] for r in recent) and now-recent[0][0]<900:
        return False,'scalp: fifteen-minute pause after three losses'
    count=db.execute('SELECT COUNT(*) FROM trades WHERE condition=?',(market['condition'],)).fetchone()[0]
    if count>=2:return False,'scalp: two-entry interval cap'
    return original(db,market,direction,now)
