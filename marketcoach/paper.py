"""Paper trading: run a strategy live on fake money, using the SAME execution and
signal logic as the backtest. This is the 'databorn' stage — prove it works
forward in real time, no real money, before a single cent is at risk.

State lives in paper_state/<account>.json. A 'tick' pulls fresh bars, acts on any
newly CLOSED bar (a bar is closed once ts+granularity <= now), and persists. Run
it on a schedule (cron / the schedule skill); for daily bars, once an hour is fine
— it only trades when a new daily candle actually closes.
"""
import json, os, time
from . import data, execution, indicators
from .strategy import Strategy

STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "paper_state")


def _path(account):
    return os.path.join(STATE_DIR, f"{account}.json")


def open_account(account, strategy_path, source="BTC-USD", granularity=86400, cash=50.0):
    os.makedirs(STATE_DIR, exist_ok=True)
    # Seed last_bar_ts to the latest already-closed bar so the account starts flat
    # and only acts on bars that close from now on ("watch it grow from today").
    now = int(time.time())
    try:
        bars = data.fetch_recent(source, granularity)
        seed_ts = max((b.ts for b in bars if b.ts + granularity <= now), default=0)
    except Exception:
        seed_ts = 0
    state = {
        "account": account, "strategy_path": strategy_path, "source": source,
        "granularity": granularity, "cash": float(cash), "units": 0.0,
        "avg_cost": 0.0, "last_bar_ts": seed_ts, "pending": None,
        "fills": [], "created": now, "start_cash": float(cash),
    }
    _save(state)
    print(f"Opened paper account '{account}': ${cash:.2f} on {source} "
          f"@ {strategy_path} (starts flat, acts on the next bar close)")
    return state


def load(account):
    with open(_path(account)) as f:
        return json.load(f)


def _save(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(_path(state["account"]), "w") as f:
        json.dump(state, f, indent=2)


def tick(account, now=None, verbose=True):
    """Process any newly closed bars. Mirrors engine.run's decide-at-close /
    act-at-next-open discipline, one bar at a time."""
    state = load(account)
    strat = Strategy.load(state["strategy_path"])
    gran = state["granularity"]
    now = now or int(time.time())

    bars = data.fetch_recent(state["source"], gran)
    closed = [b for b in bars if b.ts + gran <= now]
    if not closed:
        if verbose: print("no closed bars yet")
        return state

    prices = [b.close for b in closed]
    volumes = [b.volume for b in closed]
    rate = strat.side_rate
    new = [i for i, b in enumerate(closed) if b.ts > state["last_bar_ts"]]
    if not new:
        if verbose: _print_status(state, closed[-1].close)
        return state

    for i in new:
        bar = closed[i]
        # 1) execute pending decision from the previous bar at this bar's open
        pend = state["pending"]
        if pend:
            state, fill = _execute(state, pend, bar, rate)
            if fill and verbose:
                print(f"  FILL {fill.side:4} {fill.units:.6f} @ ${fill.price:,.2f} "
                      f"(cost ${fill.cost:.4f})")
        state["pending"] = None
        # 2) risk exit check vs this bar's close
        if state["units"] > 0 and state["avg_cost"] > 0:
            chg = bar.close / state["avg_cost"] - 1
            stop = strat.risk.get("stop_loss_pct"); take = strat.risk.get("take_profit_pct")
            if (stop is not None and chg <= -abs(stop)) or (take is not None and chg >= abs(take)):
                state["pending"] = {"action": "sell", "size": "all"}
        # 3) decide new signal from info through bar i (acts next bar)
        if not state["pending"]:
            sigs = strat.signals_at(i, prices, volumes)
            if sigs:
                state["pending"] = sigs[0]
        state["last_bar_ts"] = bar.ts

    _save(state)
    if verbose:
        _print_status(state, closed[-1].close)
    return state


def _execute(state, act, bar, rate):
    side = act["action"]
    if side == "buy" and state["cash"] > 0:
        cash, units, avg, fill = execution.apply_buy(
            state["cash"], state["units"], state["avg_cost"], bar.open,
            _pct(act.get("size", "100%")), rate, bar.ts)
    elif side == "sell" and state["units"] > 0:
        frac = 1.0 if act.get("size") in ("all", None) else _pct(act["size"])
        cash, units, avg, fill = execution.apply_sell(
            state["cash"], state["units"], state["avg_cost"], bar.open, frac, rate, bar.ts)
    else:
        return state, None
    state["cash"], state["units"], state["avg_cost"] = cash, units, avg
    if fill:
        state["fills"].append({"ts": fill.ts, "side": fill.side, "price": fill.price,
                               "units": fill.units, "cost": fill.cost})
    return state, fill


def _print_status(state, last_px):
    eq = state["cash"] + state["units"] * last_px
    ret = eq / state["start_cash"] - 1
    pos = f"{state['units']:.6f} @ ${state['avg_cost']:,.2f}" if state["units"] else "flat"
    print(f"[{state['account']}] equity ${eq:,.2f} ({ret*100:+.1f}%)  "
          f"cash ${state['cash']:,.2f}  pos {pos}  px ${last_px:,.2f}  "
          f"trades {len(state['fills'])}")


def _pct(size):
    if isinstance(size, (int, float)):
        return float(size)
    s = str(size).strip()
    if s.endswith("%"):
        return float(s[:-1]) / 100.0
    return 1.0 if s == "all" else float(s)
