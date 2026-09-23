"""Moon funnel: a paper sleeve that rotates idle cash into the strongest breakouts.

Logic each tick (on new closed bars):
  1. Manage holdings first — trailing stop / hard stop / breakdown exits. Protect
     the moon: give back at most trailing_stop_pct from the peak, then out.
  2. Funnel — rank the whole coin list by moon score; for each FREE slot, buy the
     top coin that passes all moon triggers. Free cash flows to fresh strength.

Capped by max_positions so no single pump can sink the sleeve (positive-skew
diversification: catch the occasional 5x, cut the many duds small). Fake money,
fully journaled. This is the OFFENSE sleeve; smart_swing is the DEFENSE sleeve.
"""
import json, os, time
from . import data, execution, moon, journal, risk, alerts
from .broker_alpaca import to_alpaca_symbol  # noqa: F401 (reserved for live wiring)

# Two "different" coins that move together 75%+ of the time aren't diversifying
# the funnel — they're the same bet twice. Skip a candidate this correlated with
# anything already held (or already picked earlier in the same tick).
CORRELATION_THRESHOLD = 0.75

STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "paper_state")

DEFAULTS = {
    "max_positions": 4,
    "trailing_stop_pct": 0.20,   # ride the moon, cut on a 20% pullback from peak
    "stop_loss_pct": 0.15,       # hard floor from entry
    "max_drawdown": 0.30,        # circuit breaker: liquidate + halt past this DD
    "fee_bps": 60, "half_spread_bps": 5, "slippage_bps": 3,  # taker
    "moon_cfg": {},              # overrides for moon.DEFAULT triggers
}


def _path(account):
    return os.path.join(STATE_DIR, f"{account}.json")


def load(account):
    with open(_path(account)) as f:
        return json.load(f)


def _save(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(_path(state["account"]), "w") as f:
        json.dump(state, f, indent=2)


def _rate(state):
    return (state["fee_bps"] + state["half_spread_bps"] + state["slippage_bps"]) / 10_000.0


def open_account(account, coins, granularity=86400, cash=50.0, **cfg):
    os.makedirs(STATE_DIR, exist_ok=True)
    products = [c if "-" in c else f"{c}-USD" for c in coins]
    state = {"account": account, "coins": products, "granularity": granularity,
             "start_cash": float(cash), "cash": float(cash), "positions": {},
             "last_close_ts": 0, "fills": [], "peak_equity": float(cash), "halted": False,
             **DEFAULTS}
    state.update({k: v for k, v in cfg.items() if k in DEFAULTS})
    _save(state)
    journal.log(account, "open", cash=cash, coins=len(products),
                max_positions=state["max_positions"])
    print(f"Opened moon funnel '{account}': ${cash:.2f}, up to {state['max_positions']} "
          f"positions across {len(products)} coins")
    return state


def tick(account, now=None, verbose=True):
    state = load(account)
    gran = state["granularity"]
    now = now or int(time.time())
    rate = _rate(state)
    trail, stop = state["trailing_stop_pct"], state["stop_loss_pct"]
    K = state["max_positions"]
    per = state["start_cash"] / K

    # gather latest closed bar + moon eval per coin
    evals, price, latest = {}, {}, 0
    coin_bars = {}
    for product in state["coins"]:
        try:
            bars = data.fetch_recent(product, gran)
        except Exception:
            continue
        closed = [b for b in bars if b.ts + gran <= now]
        if len(closed) < 30:
            continue
        coin_bars[product] = closed
        price[product] = closed[-1].close
        latest = max(latest, closed[-1].ts)
        evals[product] = moon.evaluate(closed, state["moon_cfg"])
        time.sleep(0.12)

    # Volatility-based sizing: a wilder candidate gets a smaller slice of its slot
    # instead of the full equal share — the moon funnel already accepts more risk
    # by design (breakout-chasing), so this keeps one especially wild pick from
    # eating a disproportionate share of the sleeve's total risk.
    coin_vols = {p: risk.realized_vol(b) for p, b in coin_bars.items()}
    vol_weights = risk.vol_target_weights(coin_vols)

    if latest <= state["last_close_ts"]:
        if verbose:
            _status(state, price)
        return state  # nothing new closed since last tick

    # 1) manage existing positions (exits protect the moon)
    for product in list(state["positions"]):
        pos = state["positions"][product]
        px = price.get(product)
        if px is None:
            continue
        pos["peak"] = max(pos.get("peak", 0.0), px) or px
        chg = px / pos["avg_cost"] - 1
        hit_trail = pos["peak"] > 0 and px <= pos["peak"] * (1 - trail)
        if hit_trail or chg <= -stop:
            cash, units, _, fill = execution.apply_sell(
                state["cash"], pos["units"], pos["avg_cost"], px, 1.0, rate, latest)
            state["cash"] = cash
            reason = "trailing_stop" if hit_trail else "hard_stop"
            state["fills"].append({"ts": latest, "coin": product, "side": "sell",
                                   "price": px, "units": pos["units"], "reason": reason})
            journal.log(account, "exit", coin=product, price=round(px, 4),
                        reason=reason, pnl_pct=round(chg * 100, 1))
            del state["positions"][product]

    # 2) funnel free cash into the top mooning coins not already held (unless
    #    halted), skipping any candidate too correlated with what's already in
    #    the funnel (or with another pick made earlier in this same tick) — real
    #    diversification, not just "N different tickers."
    free = K - len(state["positions"])
    if free > 0 and not state.get("halted"):
        candidates = [(p, ev) for p, ev in evals.items()
                      if ev and ev["is_mooning"] and p not in state["positions"]]
        candidates.sort(key=lambda t: t[1]["score"], reverse=True)

        active_returns = [risk.daily_returns(coin_bars[c])[-40:]
                          for c in state["positions"] if c in coin_bars]
        filled = 0
        for product, ev in candidates:
            if filled >= free:
                break
            cand_returns = risk.daily_returns(coin_bars[product])[-40:] if product in coin_bars else []
            max_corr = risk.max_correlation_to_held(cand_returns, active_returns)
            if max_corr > CORRELATION_THRESHOLD:
                journal.log(account, "skipped_correlated", coin=product, score=ev["score"],
                            max_corr=round(max_corr, 2), note="too correlated with a held/picked position")
                continue
            vmult = vol_weights.get(product, 1.0)
            spend = min(per * vmult, state["cash"])
            if spend < 1.0:
                break
            cash, units, avg, fill = execution.apply_buy(
                state["cash"], 0.0, 0.0, price[product], spend / state["cash"] if state["cash"] else 0,
                rate, latest)
            if fill:
                state["cash"] = cash
                state["positions"][product] = {"units": units, "avg_cost": price[product],
                                               "peak": price[product]}
                state["fills"].append({"ts": latest, "coin": product, "side": "buy",
                                       "price": price[product], "units": units,
                                       "reason": f"moon score {ev['score']}"})
                journal.log(account, "funnel_buy", coin=product, price=round(price[product], 4),
                            score=ev["score"], roc=round(ev["roc"], 3),
                            vol_ratio=round(ev["vol_ratio"], 2), max_corr=round(max_corr, 2),
                            vol_mult=round(vmult, 2))
                alerts.notify(f"market_coach: moon funnel caught {product}",
                             f"score {ev['score']}, +{ev['roc']*100:.1f}% momentum, "
                             f"{ev['vol_ratio']:.1f}x volume. ${spend:.2f} in (vol-sized "
                             f"{vmult:.0%}). Trailing stop protects the gain from here.",
                             priority="default", tags=["rocket"])
                active_returns.append(cand_returns)
                filled += 1

    state["last_close_ts"] = latest
    equity = state["cash"] + sum(p["units"] * price.get(c, p["avg_cost"])
                                 for c, p in state["positions"].items())
    # circuit breaker: liquidate all + halt if drawdown from peak is breached
    state["peak_equity"] = max(state.get("peak_equity", state["start_cash"]), equity)
    dd = equity / state["peak_equity"] - 1
    if not state.get("halted") and dd <= -state.get("max_drawdown", 0.30):
        for c in list(state["positions"]):
            px = price.get(c) or state["positions"][c]["avg_cost"]
            cash, _, _, fill = execution.apply_sell(
                state["cash"], state["positions"][c]["units"], state["positions"][c]["avg_cost"],
                px, 1.0, rate, latest)
            state["cash"] = cash
            del state["positions"][c]
        state["halted"] = True
        journal.log(account, "circuit_breaker", drawdown=round(dd * 100, 1),
                    note="liquidated all moons, halted until reset")
        alerts.notify(f"market_coach: {account} circuit breaker TRIPPED",
                     f"Drawdown {dd*100:.1f}% breached the -{state.get('max_drawdown',0.30)*100:.0f}% "
                     f"limit. Liquidated all moon positions to cash; funnel halted. "
                     f"Run: python3 moon_portfolio.py reset {account}",
                     priority="urgent", tags=["rotating_light"])
    journal.log(account, "tick", equity=round(equity, 2), cash=round(state["cash"], 2),
                holding=len(state["positions"]), halted=state.get("halted", False))
    _save(state)
    if verbose:
        _status(state, price)
    return state


def reset_breaker(account):
    state = load(account)
    state["halted"] = False
    eq = state["cash"] + sum(p["units"] * p["avg_cost"] for p in state["positions"].values())
    state["peak_equity"] = max(eq, state["start_cash"])
    _save(state)
    journal.log(account, "reset", note="moon circuit breaker re-armed")
    print(f"[{account}] moon circuit breaker reset; funnel resumes.")


def _status(state, price=None):
    price = price or {}
    eq = state["cash"] + sum(p["units"] * price.get(c, p["avg_cost"])
                             for c, p in state["positions"].items())
    ret = eq / state["start_cash"] - 1
    held = ", ".join(f"{c}({(price.get(c, p['avg_cost'])/p['avg_cost']-1)*100:+.0f}%)"
                     for c, p in state["positions"].items())
    print(f"[{state['account']}] equity ${eq:,.2f} ({ret*100:+.1f}%)  cash ${state['cash']:,.2f}  "
          f"holding {len(state['positions'])}/{state['max_positions']}: {held or 'all cash'}")
