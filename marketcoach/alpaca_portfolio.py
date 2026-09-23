"""Multi-coin paper portfolio executed through Alpaca's PAPER account.

Signals come from our own strategy on Coinbase bars (same as the backtest); orders
are placed on Alpaca paper (real broker plumbing, fake money). We keep a tiny local
state per coin (last processed bar, a pending decision, and the trailing-stop peak)
so the decide-at-close / act-at-next-open discipline holds; Alpaca is the source of
truth for actual cash and positions.

In DRY-RUN (no keys) it prints the orders it would send, so you can watch the whole
pipeline before signing up. Add keys -> it places real paper orders. No real money.
"""
import json, os, time
from . import data
from .strategy import Strategy
from .broker_alpaca import Alpaca

STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "paper_state")


def _path(account):
    return os.path.join(STATE_DIR, f"{account}.alpaca.json")


def load(account):
    with open(_path(account)) as f:
        return json.load(f)


def _save(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(_path(state["account"]), "w") as f:
        json.dump(state, f, indent=2)


def open_account(account, strategy_path, coins, granularity=86400, cash=50.0):
    os.makedirs(STATE_DIR, exist_ok=True)
    now = int(time.time())
    sleeves = {}
    for sym in coins:
        product = sym if "-" in sym else f"{sym}-USD"
        try:
            bars = data.fetch_recent(product, granularity)
            seed = max((b.ts for b in bars if b.ts + granularity <= now), default=0)
        except Exception:
            seed = 0
        sleeves[product] = {"last_bar_ts": seed, "pending": None, "peak": 0.0,
                            "in_position": False, "entry_px": 0.0}
        time.sleep(0.2)
    state = {"account": account, "strategy_path": strategy_path, "granularity": granularity,
             "start_cash": float(cash), "per_notional": round(float(cash) / len(coins), 2),
             "sleeves": sleeves, "created": now}
    _save(state)
    broker = Alpaca()
    mode = "LIVE PAPER (Alpaca keys found)" if broker.live else "DRY-RUN (no keys yet)"
    print(f"Opened Alpaca portfolio '{account}': ${cash:.2f} across {len(coins)} coins "
          f"(${state['per_notional']:.2f} each)  [{mode}]")
    return state


def tick(account, now=None, verbose=True):
    state = load(account)
    strat = Strategy.load(state["strategy_path"])
    gran = state["granularity"]
    now = now or int(time.time())
    per = state["per_notional"]
    stop = strat.risk.get("stop_loss_pct")
    take = strat.risk.get("take_profit_pct")
    trail = strat.risk.get("trailing_stop_pct")
    broker = Alpaca()

    for product, sl in state["sleeves"].items():
        try:
            bars = data.fetch_recent(product, gran)
        except Exception:
            continue
        closed = [b for b in bars if b.ts + gran <= now]
        if not closed:
            continue
        prices = [b.close for b in closed]
        volumes = [b.volume for b in closed]
        # Fresh Strategy instance per product — see paper_portfolio.py's tick() for
        # why: reusing one instance across coins lets a later coin silently read an
        # earlier coin's cached indicator series (a real bug caught by reconcile.py).
        product_strat = Strategy.load(state["strategy_path"])
        new = [i for i, b in enumerate(closed) if b.ts > sl["last_bar_ts"]]
        for i in new:
            bar = closed[i]
            # 1) execute pending decision at this bar's open
            act = sl["pending"]
            if act == "buy" and not sl["in_position"]:
                broker.buy_notional(product, per)
                sl["in_position"], sl["entry_px"], sl["peak"] = True, bar.open, bar.open
            elif act == "sell" and sl["in_position"]:
                broker.close(product)
                sl["in_position"], sl["entry_px"], sl["peak"] = False, 0.0, 0.0
            sl["pending"] = None
            # 2) trailing / hard stop / take profit
            if sl["in_position"] and sl["entry_px"] > 0:
                sl["peak"] = max(sl["peak"], bar.close) if sl["peak"] else bar.close
                chg = bar.close / sl["entry_px"] - 1
                hit_trail = trail is not None and sl["peak"] > 0 and bar.close <= sl["peak"] * (1 - abs(trail))
                if (hit_trail or (stop is not None and chg <= -abs(stop))
                        or (take is not None and chg >= abs(take))):
                    sl["pending"] = "sell"
            # 3) new signal (gated by position state)
            if not sl["pending"]:
                for a in product_strat.signals_at(i, prices, volumes):
                    if a["action"] == "buy" and not sl["in_position"]:
                        sl["pending"] = "buy"; break
                    if a["action"] == "sell" and sl["in_position"]:
                        sl["pending"] = "sell"; break
            sl["last_bar_ts"] = bar.ts
        time.sleep(0.2)

    _save(state)
    if verbose:
        status(account, state, broker)
    return state


def status(account, state=None, broker=None):
    state = state or load(account)
    broker = broker or Alpaca()
    held = [p for p, sl in state["sleeves"].items() if sl["in_position"]]
    if broker.live:
        acct = broker.account()
        eq = acct.get("equity")
        print(f"[{account}] Alpaca paper equity ${float(eq):,.2f} (start ${state['start_cash']:.0f})  "
              f"holding {len(held)}/{len(state['sleeves'])}: {', '.join(held) or 'all cash'}")
    else:
        print(f"[{account}] DRY-RUN (no Alpaca keys)  start ${state['start_cash']:.0f}  "
              f"signal-holding {len(held)}/{len(state['sleeves'])}: {', '.join(held) or 'all cash'}")
        print("  -> add keys to paper_state/alpaca.keys.json to place real paper orders")
