"""Multi-coin paper portfolio: fake money, equal-weight sleeves, one strategy run
independently per coin with trailing stops. Same execution + signal + risk logic
as the backtest, so paper and sim agree. This is the diversified 'databorn' stage.

State: paper_state/<account>.json with a per-coin sleeve {cash, units, avg_cost,
peak, last_bar_ts, pending, last_px}. A tick processes each coin's newly closed
bars. Run on a schedule; it only trades when a bar actually closes.
"""
import json, os, time
from . import data, execution, journal, regime, risk, alerts
from .strategy import Strategy

STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "paper_state")


def _path(account):
    return os.path.join(STATE_DIR, f"{account}.json")


def load(account):
    with open(_path(account)) as f:
        return json.load(f)


def _save(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(_path(state["account"]), "w") as f:
        json.dump(state, f, indent=2)


def open_account(account, strategy_path, coins, granularity=86400, cash=50.0,
                 regime_filter=True, max_drawdown=0.25):
    os.makedirs(STATE_DIR, exist_ok=True)
    now = int(time.time())
    per = float(cash) / len(coins)
    sleeves = {}
    for sym in coins:
        product = sym if "-" in sym else f"{sym}-USD"
        try:
            bars = data.fetch_recent(product, granularity)
            seed = max((b.ts for b in bars if b.ts + granularity <= now), default=0)
            last_px = bars[-1].close if bars else 0.0
        except Exception:
            seed, last_px = 0, 0.0
        sleeves[product] = {"cash": per, "units": 0.0, "avg_cost": 0.0, "peak": 0.0,
                            "last_bar_ts": seed, "pending": None, "last_px": last_px}
        time.sleep(0.25)
    state = {"account": account, "strategy_path": strategy_path, "granularity": granularity,
             "start_cash": float(cash), "sleeves": sleeves, "fills": [], "created": now,
             "regime_filter": bool(regime_filter), "max_drawdown": float(max_drawdown),
             "peak_equity": float(cash), "halted": False}
    _save(state)
    print(f"Opened portfolio '{account}': ${cash:.2f} across {len(coins)} coins "
          f"(${per:.2f} each) @ {strategy_path}")
    print(f"  coins: {', '.join(sleeves)}")
    return state


def reset_breaker(account):
    """Clear a tripped circuit breaker and re-arm from the current equity as the
    new peak. Use only after you've decided the strategy should keep running."""
    state = load(account)
    state["halted"] = False
    total = sum(sl["cash"] + sl["units"] * sl.get("last_px", 0) for sl in state["sleeves"].values())
    state["peak_equity"] = max(total, state["start_cash"])
    _save(state)
    journal.log(account, "reset", equity=round(total, 2), note="circuit breaker re-armed")
    print(f"[{account}] circuit breaker reset; trading resumes. peak reset to ${state['peak_equity']:.2f}")


def tick(account, now=None, verbose=True):
    state = load(account)
    strat = Strategy.load(state["strategy_path"])  # meta only (risk/costs) — see below
    gran = state["granularity"]
    now = now or int(time.time())
    rate = strat.side_rate
    stop = strat.risk.get("stop_loss_pct")
    take = strat.risk.get("take_profit_pct")
    trail = strat.risk.get("trailing_stop_pct")
    risk_on = regime.check_and_alert() if state.get("regime_filter") else True
    if state.get("regime_filter") and not risk_on:
        journal.log(account, "regime", status="risk_off", note="new buys blocked (BTC<200d)")
    halted = state.get("halted", False)
    can_buy = risk_on and not halted

    # --- fetch every sleeve's bars ONCE, then size by volatility ---
    # A wild coin (e.g. a small-cap on a run) getting the same dollar allocation
    # as a calm one (e.g. BTC) means it silently dominates the portfolio's actual
    # risk. Sizing multipliers only ever scale DOWN wilder-than-average coins
    # (never up), so unused allocation just sits as cash — simple, and it never
    # needs to move cash between sleeves.
    all_closed, coin_vols = {}, {}
    for product in state["sleeves"]:
        try:
            bars = data.fetch_recent(product, gran)
        except Exception as e:
            if verbose: print(f"  {product}: fetch failed ({type(e).__name__})")
            continue
        closed = [b for b in bars if b.ts + gran <= now]
        if closed:
            all_closed[product] = closed
            coin_vols[product] = risk.realized_vol(closed)
        time.sleep(0.2)
    vol_weights = risk.vol_target_weights(coin_vols)
    returns_by_coin = {p: risk.daily_returns(b)[-40:] for p, b in all_closed.items()}

    for product, sl in state["sleeves"].items():
        closed = all_closed.get(product)
        if not closed:
            continue
        sl["last_px"] = closed[-1].close
        prices = [b.close for b in closed]
        volumes = [b.volume for b in closed]
        size_mult = vol_weights.get(product, 1.0)
        # A FRESH Strategy instance per product: Strategy caches computed indicator
        # series keyed only by name (e.g. "sma:20"), with no awareness of whose
        # prices computed it. Reusing one instance across coins in the same tick
        # made every coin after the first silently read the FIRST coin's cached
        # indicators — a real bug this file had until caught by reconcile.py.
        product_strat = Strategy.load(state["strategy_path"])
        new = [i for i, b in enumerate(closed) if b.ts > sl["last_bar_ts"]]
        for i in new:
            bar = closed[i]
            # 1) execute pending at this bar's open
            if sl["pending"]:
                fill = _execute(state, sl, product, sl["pending"], bar, rate)
                if fill and verbose:
                    print(f"  {product} FILL {fill.side:4} @ ${fill.price:,.4f}")
            sl["pending"] = None
            # 2) trailing / hard stop / take profit vs this bar's close
            if sl["units"] > 0 and sl["avg_cost"] > 0:
                sl["peak"] = max(sl["peak"], bar.close) if sl["peak"] else bar.close
                chg = bar.close / sl["avg_cost"] - 1
                hit_trail = trail is not None and sl["peak"] > 0 and bar.close <= sl["peak"] * (1 - abs(trail))
                if (hit_trail or (stop is not None and chg <= -abs(stop))
                        or (take is not None and chg >= abs(take))):
                    sl["pending"] = {"action": "sell", "size": "all"}
            else:
                sl["peak"] = 0.0
            # 3) new signal (new buys blocked when the market regime is risk-off)
            if not sl["pending"]:
                sigs = product_strat.signals_at(i, prices, volumes)
                if sigs:
                    act = sigs[0]
                    if act.get("action") == "buy" and not can_buy:
                        continue  # regime off OR circuit breaker halted: no new entries
                    if act.get("action") == "buy":
                        # Combine two risk multipliers: volatility (scale down wild
                        # coins) AND correlation to what's already held in THIS
                        # account (scale down redundant, same-direction bets). A
                        # coin that's both wild and highly correlated with an
                        # existing position gets hit by both — that's the coin
                        # contributing the least real diversification.
                        held = [p for p, s in state["sleeves"].items()
                               if p != product and s["units"] > 0]
                        held_rets = [returns_by_coin[p] for p in held if p in returns_by_coin]
                        cand_rets = returns_by_coin.get(product, [])
                        max_corr = risk.max_correlation_to_held(cand_rets, held_rets)
                        corr_mult = risk.correlation_size_multiplier(max_corr)
                        total_mult = size_mult * corr_mult
                        if total_mult < 0.999:
                            act = {**act, "size": f"{total_mult*100:.0f}%"}
                            journal.log(state["account"], "risk_sized", coin=product,
                                        vol_mult=round(size_mult, 2), corr_mult=round(corr_mult, 2),
                                        max_corr=round(max_corr, 2), total_mult=round(total_mult, 2))
                    sl["pending"] = act
                    if act.get("action") == "buy" and act.get("why"):
                        journal.log(state["account"], "buy_signal", coin=product,
                                    why=act["why"], score=act.get("score"))
            sl["last_bar_ts"] = bar.ts

    # --- circuit breaker: liquidate + halt if drawdown from peak is breached ---
    total = sum(sl["cash"] + sl["units"] * sl["last_px"] for sl in state["sleeves"].values())
    state["peak_equity"] = max(state.get("peak_equity", state["start_cash"]), total)
    dd = total / state["peak_equity"] - 1
    if not state.get("halted") and dd <= -state.get("max_drawdown", 0.25):
        for product, sl in state["sleeves"].items():
            if sl["units"] > 0 and sl["last_px"] > 0:
                cash, units, avg, fill = execution.apply_sell(
                    sl["cash"], sl["units"], sl["avg_cost"], sl["last_px"], 1.0, rate, now)
                sl["cash"], sl["units"], sl["avg_cost"] = cash, units, avg
                if fill:
                    state["fills"].append({"ts": now, "coin": product, "side": "sell",
                                           "price": sl["last_px"], "units": fill.units,
                                           "cost": fill.cost, "reason": "circuit_breaker"})
        state["halted"] = True
        journal.log(account, "circuit_breaker", drawdown=round(dd * 100, 1),
                    equity=round(total, 2), note="liquidated all, new buys halted until reset")
        alerts.notify(f"market_coach: {account} circuit breaker TRIPPED",
                     f"Drawdown {dd*100:.1f}% breached the -{state.get('max_drawdown',0.25)*100:.0f}% "
                     f"limit. Liquidated everything to cash (${total:.2f}); new buys halted. "
                     f"Run: python3 paper_portfolio.py reset {account}",
                     priority="urgent", tags=["rotating_light"])

    _save(state)
    held = sum(1 for sl in state["sleeves"].values() if sl["units"] > 0)
    journal.log(account, "tick", equity=round(total, 2), holding=held,
                halted=state.get("halted", False), trades=len(state["fills"]))
    if verbose:
        status(account, state)
    return state


def _execute(state, sl, product, act, bar, rate):
    side = act["action"]
    if side == "buy" and sl["cash"] > 0:
        cash, units, avg, fill = execution.apply_buy(
            sl["cash"], sl["units"], sl["avg_cost"], bar.open, 1.0, rate, bar.ts)
    elif side == "sell" and sl["units"] > 0:
        cash, units, avg, fill = execution.apply_sell(
            sl["cash"], sl["units"], sl["avg_cost"], bar.open, 1.0, rate, bar.ts)
    else:
        return None
    sl["cash"], sl["units"], sl["avg_cost"] = cash, units, avg
    if fill:
        state["fills"].append({"ts": fill.ts, "coin": product, "side": fill.side,
                               "price": fill.price, "units": fill.units, "cost": fill.cost})
        journal.log(state["account"], "fill", coin=product, side=fill.side,
                    price=round(fill.price, 4), cost=round(fill.cost, 4))
    return fill


def status(account, state=None):
    state = state or load(account)
    total = 0.0
    held = []
    for product, sl in state["sleeves"].items():
        eq = sl["cash"] + sl["units"] * sl["last_px"]
        total += eq
        if sl["units"] > 0:
            held.append(f"{product}({(sl['last_px']/sl['avg_cost']-1)*100:+.0f}%)")
    ret = total / state["start_cash"] - 1
    print(f"[{account}] equity ${total:,.2f} ({ret*100:+.1f}%)  "
          f"start ${state['start_cash']:.0f}  trades {len(state['fills'])}  "
          f"holding {len(held)}/{len(state['sleeves'])}: {', '.join(held) if held else 'all cash'}")
