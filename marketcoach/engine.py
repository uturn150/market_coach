"""Backtest engine. Replays bars, fires strategy triggers, simulates fills with
fees + slippage (via execution.py), and returns an equity curve. This is the
'sim' — the market equivalent of deckcoach.play_match.

Two rules keep it honest (the whole point):
  1. No lookahead. A signal computed from bar i's close can only be *acted on* at
     bar i+1's open. You never trade on information you couldn't have had.
  2. Costs are real. Every fill pays fee_bps + slippage_bps. A strategy that only
     wins before costs has no edge; costs are where paper dreams meet the floor.
"""
from collections import namedtuple
from . import execution, maker_fill
from .execution import Fill  # re-export for callers

Result = namedtuple("Result", "equity closes timestamps fills cash_start")


def run(strategy, bars, cash=50.0, allow_buy_ts=None, maker=None, stats=None, size_map=None, ft=None):
    """allow_buy_ts: optional set of timestamps where NEW BUYS are permitted (a
    market-regime filter — e.g. only when BTC is above its 200-day average). Sells
    and risk exits always run, so a regime turning risk-off lets positions exit
    normally but blocks fresh entries into a falling market."""
    prices = [b.close for b in bars]
    volumes = [b.volume for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    n = len(bars)
    rate = strategy.side_rate

    cash_bal = float(cash)
    units = 0.0
    avg_cost = 0.0            # for stop-loss / take-profit reference
    peak = 0.0               # high-water price since entry, for trailing stop
    equity, fills = [], []
    stop = strategy.risk.get("stop_loss_pct")
    take = strategy.risk.get("take_profit_pct")
    trail = strategy.risk.get("trailing_stop_pct")

    pending = []  # actions decided at bar i, executed at bar i+1 open
    ft_state = {}

    for i, bar in enumerate(bars):
        # --- execute yesterday's decisions at today's open ---
        if maker is not None:
            cash_bal, units, avg_cost, fills = maker_fill.execute(
                pending, bar, cash_bal, units, avg_cost, fills, maker, stats)
            pending = []
        for act in pending:
            side = act["action"]
            if side == "buy" and cash_bal > 0:
                cash_bal, units, avg_cost, fill = execution.apply_buy(
                    cash_bal, units, avg_cost, bar.open, _pct(act.get("size", "100%")), rate, bar.ts)
                if fill:
                    fills.append(fill)
                    if ft is not None:
                        ft_state.clear(); ft_state.update(entry_i=i, peak=avg_cost)
            elif side == "sell" and units > 0:
                frac = 1.0 if act.get("size") in ("all", None) else _pct(act["size"])
                cash_bal, units, avg_cost, fill = execution.apply_sell(
                    cash_bal, units, avg_cost, bar.open, frac, rate, bar.ts)
                if fill:
                    fills.append(fill)
        pending = []

        # --- Freqtrade-style intra-bar exits (ROI table / stoploss / trailing), evaluated with this bar's high/low ---
        if ft is not None and units > 0 and avg_cost > 0 and ft_state.get("entry_i") is not None and i > ft_state["entry_i"]:
            age = i - ft_state["entry_i"]
            ft_state["peak"] = max(ft_state.get("peak", avg_cost), bar.high)
            exit_px = None
            stop_px = avg_cost * (1 + ft["stoploss"]) if ft.get("stoploss") is not None else None
            if ft.get("trail_pos"):
                armed = (ft_state["peak"] / avg_cost - 1) >= ft.get("trail_off", 0.0)
                if armed or not ft.get("trail_only_off"):
                    tstop = ft_state["peak"] * (1 - ft["trail_pos"])
                    stop_px = tstop if stop_px is None else max(stop_px, tstop)
            if stop_px is not None and bar.low <= stop_px:
                exit_px = min(stop_px, bar.open)                    # a gap below the stop fills at the open
            else:
                target = None
                for a_bars, pct in ft.get("roi", []):
                    if age >= a_bars:
                        target = pct
                if target is not None and bar.high >= avg_cost * (1 + target):
                    exit_px = max(avg_cost * (1 + target), bar.open)   # a gap above the target fills at the open
            if exit_px is not None:
                cash_bal, units, avg_cost, fill = execution.apply_sell(cash_bal, units, avg_cost, exit_px, 1.0, rate, bar.ts)
                if fill:
                    fills.append(fill)
                ft_state.clear()
                pending = [a_ for a_ in pending if a_.get("action") != "sell"]
        elif ft is not None and units <= 0:
            ft_state.clear()
        if ft is not None and units > 0 and avg_cost > 0 and ft.get("custom_exit") and ft_state.get("entry_i") is not None and i > ft_state["entry_i"]:
            if ft["custom_exit"](i - ft_state["entry_i"], bar.close / avg_cost - 1):        # Freqtrade custom_exit(age, profit)
                pending = [a_ for a_ in pending if a_.get("action") != "sell"] + [{"action": "sell", "size": "all", "urgent": True}]

        # --- risk exits checked against today's close (acted next open) ---
        if units > 0 and avg_cost > 0:
            peak = max(peak, bar.close) if peak else bar.close
            chg = bar.close / avg_cost - 1
            hit_trail = trail is not None and peak > 0 and bar.close <= peak * (1 - abs(trail))
            if (hit_trail or (stop is not None and chg <= -abs(stop))
                    or (take is not None and chg >= abs(take))):
                pending.append({"action": "sell", "size": "all", "urgent": True})
        else:
            peak = 0.0

        if ft is not None and ft.get("cooldown_bars"):
            if ft_state.get("_had_units") and units <= 1e-12:
                ft_state["_cool_until"] = i + ft["cooldown_bars"]
            ft_state["_had_units"] = units > 1e-12

        # --- mark-to-market equity at close ---
        equity.append(cash_bal + units * bar.close)

        # --- decide signals from info available *through* bar i (acted next bar) ---
        if i < n - 1 and not pending:
            for act in strategy.signals_at(i, prices, volumes, highs, lows):
                if (act.get("action") == "buy" and allow_buy_ts is not None
                        and bar.ts not in allow_buy_ts):
                    continue  # regime risk-off: block new entries
                if act.get("action") == "buy" and ft is not None and i < ft_state.get("_cool_until", -1):
                    continue                    # Freqtrade CooldownPeriod protection
                if act.get("action") == "sell" and ft is not None and ft.get("exit_profit_only") and (units <= 0 or prices[i] <= avg_cost):
                    continue                    # Freqtrade exit_profit_only: an exit SIGNAL only counts while the trade is in profit
                if act.get("action") == "buy" and size_map is not None:
                    act = dict(act)
                    act["size"] = _pct(act.get("size", "100%")) * size_map.get(bar.ts, 1.0)   # smaller entries in bad conditions
                pending.append(act)

    return Result(equity, prices, [b.ts for b in bars], fills, float(cash))


def _pct(size):
    if isinstance(size, (int, float)):
        return float(size)
    s = str(size).strip()
    if s.endswith("%"):
        return float(s[:-1]) / 100.0
    if s == "all":
        return 1.0
    return float(s)
