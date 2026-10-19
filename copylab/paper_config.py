"""Persistent paper account funding; never rebase a funded ledger."""
import json
import math
from pathlib import Path

DEFAULT=dict(initial_cash=40.0,goal=None,compound=False)


def settings(db):
    try:
        row=db.execute('SELECT value FROM paper_settings WHERE id=1').fetchone()
        return json.loads(row[0]) if row else dict(DEFAULT)
    except Exception as error:
        import sqlite3
        if isinstance(error,sqlite3.OperationalError) and 'no such table' in str(error):
            return dict(DEFAULT)
        raise


def initialize(db, initial_cash=None, goal=None, compound=None):
    for value in (initial_cash,goal):
        if value is not None and (not math.isfinite(value) or value<=0):
            raise ValueError('Paper cash and goal must be finite and positive')
    db.execute('CREATE TABLE IF NOT EXISTS paper_settings(id INTEGER PRIMARY KEY CHECK(id=1), value TEXT)')
    row=db.execute('SELECT value FROM paper_settings WHERE id=1').fetchone()
    if row:
        result=json.loads(row[0])
        for key,value in [('initial_cash',initial_cash),('goal',goal),('compound',compound)]:
            if value is not None and result[key]!=value:
                raise ValueError('Account settings already saved; use a new ledger for a new experiment')
        return result
    result=dict(DEFAULT)
    result.update({k:v for k,v in [('initial_cash',initial_cash),('goal',goal),('compound',compound)] if v is not None})
    if result['initial_cash']!=40:
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ('fills','trades','pairs'):
            if table in tables and db.execute('SELECT 1 FROM '+table+' LIMIT 1').fetchone():
                raise ValueError('Existing $40 history cannot be rebased; use a new ledger')
    if result['goal'] is not None and result['goal']<=result['initial_cash']:
        raise ValueError('Goal must exceed starting cash')
    with db:db.execute('INSERT INTO paper_settings VALUES (1,?)',(json.dumps(result),))
    return result


def scale(config,realized=0):
    capital=config['initial_cash']+(realized if config['compound'] else 0)
    return max(0,capital)/40


def progress(config,equity):
    goal=config.get('goal');start=config['initial_cash']
    known=isinstance(equity,(int,float)) and math.isfinite(equity)
    return dict(goal=goal,remaining_to_goal=max(0,goal-equity) if goal and known else None,
                goal_progress_percent=100*(equity-start)/(goal-start) if goal and known else None,
                goal_reached=bool(goal and known and equity>=goal),compound=config['compound'])


def active_database(bot):
    path=Path('active-experiment.json')
    if path.exists():
        return json.loads(path.read_text())['databases'][bot]
    return {'copy':'paper.sqlite3','arbitrage':'arbitrage.sqlite3','btc':'btc15.sqlite3'}[bot]
