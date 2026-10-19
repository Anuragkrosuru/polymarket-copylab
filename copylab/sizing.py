"""Relative source buy commitment: a bounded sizing proxy, not known confidence."""
import math
import statistics

LOOKBACK = 30 * 86400
MIN_MARKETS = 20
VERSION = 'relative-commitment-v1'


def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS source_commitments(id TEXT PRIMARY KEY, wallet TEXT,
        condition TEXT, asset TEXT, timestamp REAL, notional REAL);
    CREATE INDEX IF NOT EXISTS commitments_wallet_time ON source_commitments(wallet,timestamp);
    ''')


def observe(ledger, wallet, trades, now):
    from copylab.__main__ import event_id
    rows=[]
    for t in trades:
        try:
            timestamp,price,size=float(t['timestamp']),float(t['price']),float(t['size'])
            if (t.get('side') != 'BUY' or t.get('proxyWallet','').lower() != wallet.lower()
                    or not t.get('conditionId') or not t.get('asset')
                    or not all(math.isfinite(x) for x in (timestamp,price,size,price*size))
                    or not 0 < price < 1 or size <= 0 or not now-LOOKBACK <= timestamp <= now):
                continue
            rows.append((event_id(t),wallet.lower(),str(t['conditionId']),str(t['asset']),timestamp,price*size))
        except (KeyError,TypeError,ValueError,OverflowError):
            continue
    with ledger.db:
        ledger.db.executemany('INSERT OR IGNORE INTO source_commitments VALUES (?,?,?,?,?,?)',rows)


def target(ledger, trade, cap):
    """Only observations available by source-event time enter its sizing decision."""
    at=float(trade['timestamp'])
    wallet=trade.get('proxyWallet','').lower()
    condition=str(trade.get('conditionId',''))
    asset=str(trade['asset'])
    groups=ledger.db.execute('''SELECT condition,asset,SUM(notional),MAX(timestamp)
        FROM source_commitments WHERE wallet=? AND timestamp BETWEEN ? AND ?
        GROUP BY condition,asset''',(wallet,at-LOOKBACK,at)).fetchall()
    current=sum(total for c,a,total,last in groups if c == condition and a == asset)
    history=[(c,total) for c,a,total,last in groups if c != condition and last <= at-120]
    market_count=len({c for c,total in history})
    median=statistics.median(total for _,total in history) if market_count >= MIN_MARKETS else None
    ratio=current/median if median and current > 0 else None
    amount=min(cap,max(.25,cap*ratio/2)) if ratio is not None else cap*.25
    return dict(policy=VERSION,target_cost=round(amount,6),source_commitment=current,
                typical_commitment=median,relative_commitment=ratio,history_markets=market_count,
                reason='relative gross buy commitment' if ratio is not None else 'limited history: conservative fallback',
                caveat='Observed 30-day gross buys by market/outcome; incomplete history, hedges and current holdings are unknown.')
