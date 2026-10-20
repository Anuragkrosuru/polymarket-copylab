"""Invented $100 accounting examples; not a forecast of reaching $1,000."""
import contextlib
import io
import json
from pathlib import Path
from copylab.__main__ import Ledger,scan
from copylab.portfolio import summary
from copylab.arbitrage import Account,quote
from copylab.btc15 import simulate_scalp
from copylab.presentation import without_quantities


def copy_case(exit_price):
    ledger=Ledger(':memory:',initial_cash=100,goal=1000,compound=True)
    now=1700000000;wallet='0x'+'1'*40
    with ledger.db:ledger.db.execute('INSERT INTO wallets VALUES (?,?)',(wallet,now-1))
    historical=[dict(proxyWallet=wallet,transactionHash='h'+str(i),timestamp=now-1000,conditionId='old'+str(i),asset='old'+str(i),side='BUY',price=.5,size=100) for i in range(20)]
    trades=[dict(proxyWallet=wallet,transactionHash='b'+str(i),timestamp=now,conditionId='c'+str(i),asset='a'+str(i),side='BUY',price=.5,size=200,title='INVENTED market '+str(i),outcome='Yes') for i in range(3)]
    def buy_book(url,**params):
        asset=params['token_id'];return dict(asset_id=asset,market='c'+asset[1:],min_order_size=5,asks=[dict(price=.5,size=100)],bids=[dict(price=.49,size=100)])
    def sell_book(url,**params):
        book=buy_book(url,**params);book.update(asks=[dict(price=exit_price+.01,size=100)],bids=[dict(price=exit_price,size=100)]);return book
    with contextlib.redirect_stdout(io.StringIO()):
        scan(ledger,wallet,buy_book,lambda:now,max_cost=5,prefetched=historical+trades)
        sells=[dict(t,side='SELL',price=exit_price,timestamp=now+1,transactionHash='s'+str(i)) for i,t in enumerate(trades)]
        scan(ledger,wallet,sell_book,lambda:now+1,max_cost=5,prefetched=sells)
    state=summary(ledger,now+1)
    return dict(cash=state['cash'],equity=state['equity'],realized_pnl=state['realized_pnl'],entries=state['fills'],
                exits=state['sales'],goal=state['goal'],remaining_to_goal=state['remaining_to_goal'])


def arbitrage_case():
    a=Account(':memory:',initial_cash=100,goal=1000,compound=True)
    m=dict(condition_id='invented',question='INVENTED binary market',active=True,closed=False,accepting_orders=True,paper_fee_rate=.07,
           minimum_order_size=5,tokens=[dict(token_id='a',outcome='Yes'),dict(token_id='b',outcome='No')])
    def books(prices):
        return [dict(asset_id=t,market='invented',min_order_size=5,asks=[dict(price=p,size=100)],bids=[dict(price=p-.01,size=100)]) for t,p in zip(('a','b'),prices)]
    q=quote(m,books((.42,.43)),budget=15)
    assert a.enter(m,q,now=1700000000)
    a.settle('invented',dict(m,closed=True,tokens=[dict(token_id='a',winner=True),dict(token_id='b',winner=False)]))
    normal=quote(m,books((.50,.51)),budget=15,require_edge=False)
    s=a.state()
    return dict(cash=s['cash'],realized_pnl=s['realized_pnl'],goal=s['goal'],remaining_to_goal=s['remaining_to_goal'],
                normal_spread_expected_gain=normal['expected_gain'],normal_spread_action='reject')


def run(output='goal-simulation-results.json'):
    result=dict(label='SYNTHETIC ONLY. Invented inputs, not historical/live returns or a forecast. No scenario assumes the $1,000 target is reached.',
                initial_cash_per_bot=100,goal_per_bot=1000,compound=True,
                copy=dict(favorable=copy_case(.65),adverse=copy_case(.30)),
                arbitrage=arbitrage_case(),btc=simulate_scalp(initial_cash=100,goal=1000,compound=True))
    Path(output).write_text(json.dumps(without_quantities(result),indent=2)+'\n')
    print('Saved invented $100 scenarios:',output)
    return result


if __name__=='__main__':run()
