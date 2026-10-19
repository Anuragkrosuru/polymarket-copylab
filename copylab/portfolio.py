"""Conservative paper portfolio valuation and idempotent settlement accounting."""
import json
import math
import time
from datetime import datetime, timezone


EXIT_RESERVE = 0.02
MAX_MARK_AGE = 120


def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS settlements (
        asset TEXT PRIMARY KEY, shares REAL NOT NULL, cost REAL NOT NULL,
        payout REAL NOT NULL, checked REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS marks (
        asset TEXT PRIMARY KEY, value REAL, checked REAL NOT NULL,
        note TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS health (
        id INTEGER PRIMARY KEY CHECK (id=1), checked REAL NOT NULL);
    ''')
    columns = {row[1] for row in db.execute('PRAGMA table_info(marks)')}
    for column in ('shares', 'cost'):
        if column not in columns:
            db.execute('ALTER TABLE marks ADD COLUMN ' + column + ' REAL')
    db.commit()


def open_lots(db):
    return db.execute("""SELECT f.id, f.asset, f.shares-COALESCE(e.shares,0),
        f.cost-COALESCE(e.cost,0), f.source FROM fills f
        LEFT JOIN (SELECT fill_id, SUM(shares) shares, SUM(cost) cost
                   FROM exits GROUP BY fill_id) e ON e.fill_id=f.id
        WHERE f.asset NOT IN (SELECT asset FROM settlements)
        AND f.shares-COALESCE(e.shares,0)>0.000000001 ORDER BY f.rowid""").fetchall()


def open_positions(db):
    grouped = {}
    for _, asset, shares, cost, source in open_lots(db):
        position = grouped.setdefault(asset, [asset, 0.0, 0.0, source])
        position[1] += shares
        position[2] += cost
    return list(grouped.values())


def settlement_price(market, asset):
    """Closed alone is insufficient: require a complete binary winner vector."""
    tokens = market.get('tokens', [])
    if market.get('closed') is not True or len(tokens) != 2:
        return None
    if any(type(t.get('winner')) is not bool for t in tokens):
        return None
    if len({str(t.get('token_id')) for t in tokens}) != 2:
        raise ValueError('Duplicate outcome token')
    if sum(t['winner'] for t in tokens) != 1:
        return None
    matches = [t for t in tokens if str(t.get('token_id')) == asset]
    if len(matches) != 1:
        raise ValueError('Market does not identify the held token')
    return float(matches[0]['winner'])


def bid_value(book, shares):
    """Walk bid depth; shares without bids have zero immediate liquidation value."""
    levels = []
    for level in book['bids']:
        price, size = float(level['price']), float(level['size'])
        if not all(math.isfinite(x) for x in (price, size)) or not 0 <= price <= 1 or size <= 0:
            raise ValueError('Invalid bid level')
        levels.append((price, size))
    remaining, gross = shares, 0.0
    for price, size in sorted(levels, reverse=True):
        take = min(size, remaining)
        gross += take * price
        remaining -= take
        if remaining <= 1e-9:
            break
    note = 'bid depth estimate' if remaining <= 1e-9 else 'insufficient bid depth; unfilled shares valued at zero'
    return gross * (1 - EXIT_RESERVE), note


def refresh(ledger, fetch, now=None):
    now = time.time() if now is None else now
    for asset, shares, cost, source in open_positions(ledger.db):
        try:
            condition = json.loads(source)['conditionId']
            market = fetch('https://clob.polymarket.com/markets/' + condition)
            if market.get('condition_id') != condition:
                raise ValueError('Market identity mismatch')
            payout_price = settlement_price(market, asset)
            if payout_price is not None:
                with ledger.db:
                    ledger.db.execute('BEGIN IMMEDIATE')
                    # Re-read remaining lots after acquiring the writer lock; a concurrent
                    # watcher may have sold part of this holding during the API call.
                    current = next((p for p in open_positions(ledger.db) if p[0] == asset), None)
                    if current is not None:
                        _, current_shares, current_cost, _ = current
                        ledger.db.execute('INSERT OR IGNORE INTO settlements VALUES (?,?,?,?,?)',
                                          (asset, current_shares, current_cost, current_shares * payout_price, now))
                continue
            book = fetch('https://clob.polymarket.com/book', token_id=asset)
            if str(book.get('asset_id')) != asset:
                raise ValueError('Order book identity mismatch')
            value, note = bid_value(book, shares)
        except (OSError, ValueError, KeyError, TypeError) as error:
            value, note = None, 'valuation unavailable: ' + str(error)
        with ledger.db:
            ledger.db.execute('INSERT OR REPLACE INTO marks(asset,value,checked,note,shares,cost) VALUES (?,?,?,?,?,?)',
                              (asset, value, now, note, shares, cost))
    with ledger.db:
        ledger.db.execute('INSERT OR REPLACE INTO health VALUES (1,?)', (now,))


def summary(ledger, now=None):
    now = time.time() if now is None else now
    payout, settled_cost = ledger.db.execute(
        'SELECT COALESCE(SUM(payout),0), COALESCE(SUM(cost),0) FROM settlements').fetchone()
    sale_proceeds, sale_cost = ledger.db.execute(
        'SELECT COALESCE(SUM(proceeds),0), COALESCE(SUM(cost),0) FROM exits').fetchone()
    cash = 40 - ledger.spend() + payout + sale_proceeds
    positions, missing, value = [], 0, 0.0
    for asset, shares, cost, source in open_positions(ledger.db):
        mark = ledger.db.execute('SELECT value, checked, note, shares, cost FROM marks WHERE asset=?', (asset,)).fetchone()
        usable = (mark is not None and mark[0] is not None and
                  0 <= now - mark[1] <= MAX_MARK_AGE and mark[3] is not None and mark[4] is not None and
                  abs(mark[3]-shares) < 1e-8 and abs(mark[4]-cost) < 1e-8)
        if usable:
            value += mark[0]
        else:
            missing += 1
        trade = json.loads(source)
        positions.append(dict(asset=asset, title=trade.get('title', asset), outcome=trade.get('outcome', ''),
                              shares=shares, cost=cost, value=mark[0] if usable else None,
                              pnl=mark[0] - cost if usable else None,
                              checked=mark[1] if mark else None,
                              note=mark[2] if mark else 'not yet valued'))
    open_cost = sum(p['cost'] for p in positions)
    health = ledger.db.execute('SELECT checked FROM health WHERE id=1').fetchone()
    monitor = ledger.db.execute('SELECT targets FROM monitor WHERE id=1').fetchone()
    targets = json.loads(monitor[0]) if monitor else None
    return dict(mode='SIMULATION', cash=cash, initial_cash=40, open_cost=open_cost,
                open_value=value if not missing else None, realized_pnl=payout-settled_cost+sale_proceeds-sale_cost,
                sale_pnl=sale_proceeds-sale_cost, settlement_pnl=payout-settled_cost,
                sales=ledger.db.execute('SELECT COUNT(DISTINCT id) FROM exits').fetchone()[0],
                unrealized_pnl=value-open_cost if not missing else None,
                equity=cash+value if not missing else None,
                total_pnl=cash+value-40 if not missing else None,
                fills=ledger.db.execute('SELECT COUNT(*) FROM fills').fetchone()[0],
                settled_positions=ledger.db.execute('SELECT COUNT(*) FROM settlements').fetchone()[0],
                unpriced_positions=missing, positions=positions,
                last_refresh=health[0] if health else None, monitored_wallets=targets,
                decision_counts={r[0]: r[1] for r in ledger.db.execute(
                    'SELECT result, COUNT(*) FROM decisions GROUP BY result')},
                scans=[dict(wallet=r[0], checked=r[1], newest=r[2], records=r[3], error=r[4])
                       for r in ledger.db.execute('SELECT * FROM scans ORDER BY wallet')
                       if targets is None or r[0] in targets])


def display(report):
    def dollars(value):
        return 'UNKNOWN' if value is None else '$%.2f' % value
    print('SIMULATION | Cash: %s | Equity: %s | Total P/L: %s' %
          (dollars(report['cash']), dollars(report['equity']), dollars(report['total_pnl'])), flush=True)
    print('Realized P/L: %s | Open P/L (estimate): %s | Buys: %d | Settled positions: %d' %
          (dollars(report['realized_pnl']), dollars(report['unrealized_pnl']),
           report['fills'], report['settled_positions']), flush=True)
    print('Copied sales: %d | Sale P/L: %s | Settlement P/L: %s' %
          (report['sales'], dollars(report['sale_pnl']), dollars(report['settlement_pnl'])), flush=True)
    for scan in report['scans']:
        age = max(0, time.time() - scan['checked'])
        state = scan['error'] or ('OK' if age <= 120 else 'STALE; watcher may be stopped')
        if scan['newest'] is not None:
            state += '; newest source trade %.0fs old' % max(0, time.time()-scan['newest'])
        print('Watcher %s | Last scan %.0fs ago | %s' % (scan['wallet'], age, state), flush=True)
    for position in report['positions']:
        print('  %s [%s] | Cost: %s | Bid value: %s | P/L: %s | %s' %
              (position['title'], position['outcome'], dollars(position['cost']),
               dollars(position['value']), dollars(position['pnl']), position['note']), flush=True)
    if report['last_refresh'] is not None:
        stamp = datetime.fromtimestamp(report['last_refresh'], timezone.utc).isoformat(timespec='seconds')
        print('Last valuation refresh: ' + stamp, flush=True)
    if report['unpriced_positions']:
        print('Total P/L incomplete: %d positions lack fresh valuation.' % report['unpriced_positions'], flush=True)
