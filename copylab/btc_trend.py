"""Experimental trend and pullback signals; not a fair-value or profit forecast."""
import statistics

VERSION = 'btc-trend-v1'
TAKE_PROFIT = .05
STOP_LOSS = .20
MAX_HOLD = 120
COOLDOWN = 20
MAX_INTERVAL_ENTRIES = 4


def signal(db, condition, now):
    rows=db.execute('SELECT received,rh FROM samples WHERE condition=? AND received BETWEEN ? AND ? AND rh IS NOT NULL ORDER BY received',
                    (condition,now-60,now)).fetchall()
    if len(rows)<30 or rows[-1][0]-rows[0][0]<45:
        return dict(direction=None,reason='collecting 45 seconds of trend history')
    if any(b[0]-a[0]>5 for a,b in zip(rows,rows[1:])) or now-rows[-1][0]>3:
        return dict(direction=None,reason='trend history has a data gap')
    price=rows[-1][1]
    def prior(seconds):
        return min(rows,key=lambda row:abs(row[0]-(now-seconds)))[1]
    fast=statistics.mean(p for t,p in rows if t>=now-10)
    slow=statistics.mean(p for t,p in rows if t>=now-45)
    move30=(price/prior(30)-1)*10000
    move10=(price/prior(10)-1)*10000
    direction=0 if move30>0 else 1
    sign=1 if direction==0 else -1
    recent=[p for t,p in rows if t>=now-10]
    changes=[b-a for a,b in zip(recent,recent[1:]) if abs(b-a)>1e-9]
    agreement=sum(change*sign>0 for change in changes)/len(changes) if changes else 0
    confirmed=(move30*sign>=1 and move10*sign>=.3 and (fast-slow)*sign>0
               and (price-fast)*sign>0 and len(changes)>=4 and agreement>=.55)
    return dict(direction=direction if confirmed else None,reason='confirmed trend' if confirmed else 'no confirmed trend',
                move30_bps=move30,move10_bps=move10,fast_average=fast,slow_average=slow,tick_agreement=agreement)


def reentry(db, market, direction, now):
    condition=market['condition']
    trades=db.execute('SELECT closed,direction FROM trades WHERE condition=? ORDER BY opened DESC',(condition,)).fetchall()
    if len(trades)>=MAX_INTERVAL_ENTRIES:
        return False,'four-entry interval cap'
    latest=db.execute('SELECT MAX(closed) FROM trades').fetchone()[0]
    if latest is not None and now-latest<COOLDOWN:
        return False,'twenty-second exit cooldown'
    if not trades or trades[0][1]!=direction:
        return True,'new trend direction'
    closed=trades[0][0]
    if closed is None:
        return False,'position already open'
    rows=db.execute('SELECT received,rh FROM samples WHERE condition=? AND received BETWEEN ? AND ? AND rh IS NOT NULL ORDER BY received',
                    (condition,closed,now)).fetchall()
    if len(rows)<3:
        return False,'waiting for post-exit pullback'
    # Convert Down to the same rising coordinate used for Up.
    sign=1 if direction==0 else -1
    anchor=rows[0][1]*sign
    trough=None
    for _,price in rows[1:]:
        value=price*sign
        anchor=max(anchor,value)
        if trough is None and anchor-value>=price*.00005:
            trough=value
        elif trough is not None:
            trough=min(trough,value)
    if trough is None:
        return False,'waiting for post-exit pullback'
    if rows[-1][1]*sign-trough<rows[-1][1]*.000025:
        return False,'waiting for pullback recovery'
    return True,'pullback and trend recovery'
