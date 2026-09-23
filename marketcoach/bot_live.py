"""Live PAPER runner for the order-level bots (grid / DCA cycle / rotation).

Design: an account is (bot kind + params + coins + anchor day + cash). Each tick it FETCHES the real closed
daily candles and RE-SIMULATES the bot from the anchor with the very same engine the backtests use
(bot_engine.run), so live paper == backtest by construction (no second code path to drift), and only data
that had actually closed by now is ever used. Forward-only: nothing before the anchor is traded.

The result is written in the same state shape the dashboard/leaderboard already read
(paper_state/<name>.kraken.json with 'sleeves', 'fills'), plus the bot's open orders. PAPER ONLY: there is
no broker call anywhere in this file. Never ticked by kraken_portfolio.
"""
import json, os, sys, time
from . import kraken_data, market_filters as mf, journal, bot_engine, community_bots as cb, kraken_fees

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.join(BASE, "paper_state")
GRAN = 86400


def _path(name):
    return os.path.join(STATE_DIR, f"{name}.kraken.json")


def make_bot(kind, params, ok):
    mo = {"none": None, "F0": ok["F0"], "F1": ok["F1"]}[params.get("filter", "F1")]
    p = {k: v for k, v in params.items() if k not in ("filter", "breaker", "breaker_pause")}
    if kind == "dca_adaptive":
        bot = cb.AdaptiveDCA(table_at=ok["table_at"], regime_at=ok["regime_at"], market_ok=mo, **p)
    elif kind == "grid":
        bot = cb.GridBot(market_ok=mo, **p)
    elif kind == "dca":
        bot = cb.DCACycleBot(market_ok=mo, **p)
    elif kind == "rot":
        bot = cb.RotationBot(market_ok=mo, **p)
    else:
        raise ValueError(kind)
    if params.get("breaker"):
        bot = cb.Breaker(bot, dd_limit=params["breaker"], pause_days=params.get("breaker_pause", 30))
    return bot


def open_account(name, kind, params, coins, cash=100.0):
    now = int(time.time())
    anchor = (now // GRAN) * GRAN            # today's (still forming) bar is where trading starts: first bar traded is tomorrow's open
    state = {"account": name, "kind": "bot_live", "bot_kind": kind, "bot_params": params, "coins": coins,
             "granularity": GRAN, "start_cash": float(cash), "anchor_ts": anchor, "paper": True, "created": now,
             "sleeves": {c: {"cash": cash / len(coins), "units": 0.0, "last_px": 0.0, "last_bar_ts": 0, "pending": None,
                             "peak": 0.0, "in_position": False, "entry_px": 0.0} for c in coins},
             "fills": [], "orders": [], "halted": False, "peak_equity": float(cash)}
    os.makedirs(STATE_DIR, exist_ok=True)
    json.dump(state, open(_path(name), "w"), indent=2)
    return state


def _closed_bars(coins):
    now = time.time()
    out = {}
    for c in coins:
        b = [x for x in kraken_data.fetch_recent(c, GRAN) if x.ts + GRAN <= now]
        out[c] = b
    n = min(len(b) for b in out.values())
    return {c: b[-n:] for c, b in out.items()}


def simulate(state, bars):
    """Deterministically replay the account from its anchor over `bars` (dict coin->closed bars, tail aligned)."""
    ts = [b.ts for b in bars["BTC-USD"]] if "BTC-USD" in bars else [b.ts for b in next(iter(bars.values()))]
    btc = bars.get("BTC-USD") or None
    from . import market_filters as mfl
    if btc is not None:
        c = [b.close for b in btc]
        f0 = {b.ts for i, b in enumerate(btc) if i >= 199 and c[i] > sum(c[i - 199:i + 1]) / 200}
        f1 = f0 & mfl.f1_timestamps(btc)
    else:
        f0 = f1 = set(ts)
    # run only from the anchor: bars strictly after the anchor day are tradable
    start = next((i for i, t in enumerate(ts) if t >= state["anchor_ts"]), len(ts))
    if start >= len(ts):
        return None
    sub = {c: b[start:] for c, b in bars.items()}
    sts = [b.ts for b in next(iter(sub.values()))]
    ok = {"none": None, "F0": (lambda i, s=f0, t=sts: t[i] in s), "F1": (lambda i, s=f1, t=sts: t[i] in s)}
    if state["bot_kind"] == "dca_adaptive":
        from . import regimes as rg, adaptive_tables as at
        lab = rg.label_map(btc) if btc is not None else {}
        allts = ts
        ok["regime_at"] = lambda i, t=sts: lab.get(t[i - 1] if i >= 1 else t[0])
        ok["table_at"] = lambda i, t=sts: at.table_at(t[i])
    bot = make_bot(state["bot_kind"], state["bot_params"], ok)
    rules = None
    if state.get("kraken_rules"):                     # real per-coin Kraken minimums / lot sizes / tick sizes
        from . import kraken_rules
        rules = kraken_rules.rules_for(list(sub))
    return bot_engine.run(bot, sub, cash=state["start_cash"], decide_last=True, rules=rules, order_step=float(state.get("order_step", 0.0)))


def tick(name, verbose=True):
    state = json.load(open(_path(name)))
    bars = _closed_bars(state["coins"])
    res = simulate(state, bars)
    price = {c: bars[c][-1].close for c in bars}
    if res is None:                                   # anchor day has not closed yet: nothing to simulate
        eq = state["start_cash"]
        held = {}
        orders, fills = [], []
        cash = state["start_cash"]
    else:
        ctx = res.ctx
        cash = ctx.cash
        held = {c: u for c, u in ctx.pos.items() if u > 1e-12}
        eq = res.equity[-1]
        orders = [{"coin": o.coin, "side": o.side, "price": round(o.price, 8), "units": round(o.units, 8), "tag": str(o.tag)} for o in ctx.orders.values()]
        fills = [{"ts": f["ts"], "coin": f["coin"], "side": f["side"], "price": f["price"], "units": f["units"],
                  **({"notional": f["notional"]} if f["side"] == "buy" else {"proceeds": f["proceeds"]})} for f in res.fills]
    coins = state["coins"]
    for c in coins:
        u = held.get(c, 0.0)
        cb_ = res.ctx.cost_basis[c] / u if (res is not None and u) else 0.0
        state["sleeves"][c].update({"cash": cash / len(coins), "units": u, "last_px": price[c], "in_position": u > 0,
                                    "entry_px": cb_, "last_bar_ts": bars[c][-1].ts})
    state.update(fills=fills, orders=orders, peak_equity=max(state.get("peak_equity", eq), eq), last_tick=int(time.time()),
                 engine_stats=(res.stats if res is not None else {}))
    json.dump(state, open(_path(name), "w"), indent=2)
    journal.log(name, "tick", equity=round(eq, 2), holding=len(held), halted=False, paper=True)
    if verbose:
        print(f"[{name}] PAPER equity ${eq:,.2f} ({(eq/state['start_cash']-1)*100:+.2f}%)  holding {len(held)}/{len(coins)}, "
              f"{len(orders)} resting orders, {len(fills)} fills since anchor")
    return state


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "tick":
        tick(sys.argv[2])
    else:
        print("usage: python3 bot_live.py tick <account>")
