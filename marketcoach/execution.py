"""Single source of truth for how an order becomes a fill, including costs.

Both the backtest engine and the live paper loop call these, so a fill is priced
identically whether it's simulated over history or 'executed' on today's live bar.
Cost model: a buy pays price*(1+cost_rate), a sell receives price*(1-cost_rate),
where cost_rate = (fee_bps + slippage_bps)/10000. Costs always work against you.
"""
from collections import namedtuple

Fill = namedtuple("Fill", "ts side price units cost")


def cost_rate(fee_bps, slippage_bps):
    return (fee_bps + slippage_bps) / 10_000.0


def apply_buy(cash, units, avg_cost, price, size_frac, rate, ts):
    """Spend size_frac of cash at price (+cost). Returns (cash, units, avg_cost, fill|None)."""
    spend = cash * size_frac
    if spend <= 0:
        return cash, units, avg_cost, None
    fill_px = price * (1 + rate)
    bought = spend / fill_px
    if bought <= 0:
        return cash, units, avg_cost, None
    new_units = units + bought
    avg_cost = ((avg_cost * units) + price * bought) / new_units if new_units else price
    return cash - spend, new_units, avg_cost, Fill(ts, "buy", price, bought, spend * rate)


def apply_sell(cash, units, avg_cost, price, size_frac, rate, ts):
    """Sell size_frac of position at price (-cost). Returns (cash, units, avg_cost, fill|None)."""
    sell_units = units * size_frac
    if sell_units <= 0:
        return cash, units, avg_cost, None
    fill_px = price * (1 - rate)
    proceeds = sell_units * fill_px
    units2 = units - sell_units
    if units2 <= 1e-12:
        units2, avg_cost = 0.0, 0.0
    return cash + proceeds, units2, avg_cost, Fill(ts, "sell", price, sell_units, sell_units * price * rate)
