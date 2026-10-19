"""Single-watcher locking and durable operational progress."""
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def watcher_lock(database):
    path = Path(database).resolve().with_suffix('.watch.lock')
    handle = path.open('a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            handle.seek(0)
            if not handle.read(1):
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise ValueError('A watcher is already running for this database')
        else:
            import fcntl
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise ValueError('A watcher is already running for this database')
        yield
    finally:
        handle.close()


def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS monitor(id INTEGER PRIMARY KEY CHECK(id=1), checked REAL, targets TEXT);
    CREATE TABLE IF NOT EXISTS scans(wallet TEXT PRIMARY KEY, checked REAL,
        newest REAL, records INTEGER, error TEXT);
    CREATE TABLE IF NOT EXISTS snapshots(id INTEGER PRIMARY KEY, checked REAL,
        report TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS decisions(id TEXT PRIMARY KEY, checked REAL,
        result TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, source TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS exits(id TEXT, fill_id TEXT, shares REAL,
        cost REAL, proceeds REAL, checked REAL, PRIMARY KEY(id, fill_id));
    ''')


def scan_result(ledger, wallet, trades=None, error=None, now=None):
    now = time.time() if now is None else now
    newest = max((float(t['timestamp']) for t in (trades or [])), default=None)
    with ledger.db:
        ledger.db.execute('INSERT OR REPLACE INTO scans VALUES (?,?,?,?,?)',
                          (wallet, now, newest, len(trades or []), error))


def save_snapshot(ledger, report, now=None):
    with ledger.db:
        ledger.db.execute('INSERT INTO snapshots(checked,report) VALUES (?,?)',
                          (time.time() if now is None else now, json.dumps(report)))


def watch_targets(ledger, manual, watchlist_path=None, now=None):
    import math
    import re
    from copylab.portfolio import open_lots
    now = time.time() if now is None else now
    config = json.loads(Path(watchlist_path).read_text()) if watchlist_path else {'wallets':[]}
    if not isinstance(config,dict) or not isinstance(config.get('wallets'),list):
        raise ValueError('Invalid watchlist')
    profiles = config.get('profiles',{})
    if not isinstance(profiles,dict):
        raise ValueError('Invalid wallet profiles')
    configured = config['wallets']
    if config.get('generated') is not None:
        age = now-float(config['generated'])
        if age < -60 or age > 7200:
            configured=[]
    if any(not isinstance(w,str) or not re.fullmatch(r'0x[0-9a-fA-F]{40}',w) for w in manual+configured):
        raise ValueError('Invalid watched wallet')
    selected = {w.lower() for w in manual+configured}
    held={json.loads(lot[4]).get('proxyWallet','').lower() for lot in open_lots(ledger.db)}-{''}
    targets={}
    for wallet in selected|held:
        profile=profiles.get(wallet,{})
        max_cost=float(profile.get('max_cost',2))
        if not math.isfinite(max_cost) or not 0<=max_cost<=2:
            raise ValueError('Invalid copied position budget')
        targets[wallet]=dict(allow_buys=wallet in selected,max_cost=max_cost,
                             classification=profile.get('classification','manual'),name=profile.get('name',wallet))
    return targets
