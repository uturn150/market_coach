"""Train the SLOW trader for a small ($50) account.

Fits the real constraints: 3-5 coins so each position is >= $10 (Kraken's minimum order is ~$7), one position per coin,
real fees, behind the F0/F1 market filter. The learner evolves the signal family (turtle / keltner / sma-cross / multi),
its periods, the trailing stop and hard stop, the filter and WHICH coins to hold. Fitness rewards return but punishes
drawdown hard (a $50 account cannot sit through -45%), and every genome is scored on training episodes only. Survivors are
then judged on years AND coins the search never saw. Hall of fame / seen genomes persist (paper_state/slow_learner.json).
"""
import json, math, os, random, time
from . import engine, kraken_fees, metrics
from .strategy import Strategy

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILE = os.path.join(BASE, "paper_state", "slow_learner_v2.json")
# Broadened 2026-09-22: was 5 train coins / 4 pseudo-unseen coins, both drawn from the SAME 24-coin basket used
# everywhere else -> the "unseen" check wasn't independent evidence, just a second sample from familiar coins.
# TRAIN_COINS now covers most of that basket (more combinations to search); UNSEEN_COINS is the REAL holdout set
# (marketcoach.holdout.holdout_products(): the ~20 coins never used to build ANY strategy in this project).
# Every one of these individually clears load_long's ~6-year depth requirement (checked empirically 2026-09-22;
# SOL/ADA/AVAX/XRP/DOT/DOGE/FIL do NOT have that much cached daily history yet and were dropped, not guessed at).
TRAIN_COINS = ["BTC-USD", "ETH-USD", "LTC-USD", "LINK-USD", "BCH-USD", "ATOM-USD", "ALGO-USD", "ETC-USD", "XLM-USD"]
try:
    from .holdout import holdout_products as _holdout_products
    UNSEEN_COINS = _holdout_products()
except Exception:
    UNSEEN_COINS = ["SUI-USD", "INJ-USD", "RENDER-USD", "TIA-USD", "SEI-USD", "WLD-USD", "HBAR-USD", "VET-USD"]
SPACE = {"family": ["turtle", "keltner", "sma", "multi"],
         "entry_n": [20, 30, 40, 55, 75], "exit_n": [10, 15, 20, 30], "kel_n": [10, 20, 30], "kel_mult": [1.5, 2.0, 2.5, 3.0],
         "fast": [10, 20, 30, 50], "slow": [60, 100, 150, 200], "threshold": [3, 4, 5], "roc_min": [0.05, 0.08, 0.12],
         "vol_x": [1.2, 1.5, 2.0], "donch": [15, 20, 30],
         "trail": [None, 0.10, 0.15, 0.20, 0.25], "stop": [None, 0.08, 0.12, 0.15],
         "filter": ["F0", "F1"], "ncoins": [3, 4, 5],
         "size_mode": ["full", "half_in_chop", "third_in_chop", "adx_scaled"],     # risk: smaller entries when the market is choppy
         "maker": [False, True]}                                                   # cost: rest entries/exits as limit orders
FEES = {"order_type": "taker", "taker_fee_bps": 80, "half_spread_bps": 5, "slippage_bps": 3, "maker_fee_bps": 40}


def random_genome(rng):
    g = {k: rng.choice(v) for k, v in SPACE.items()}
    g["coins"] = sorted(rng.sample(TRAIN_COINS, g["ncoins"]))
    return g


def mutate(g, rng, rate=0.3):
    g = dict(g)
    for k, v in SPACE.items():
        if rng.random() < rate:
            g[k] = rng.choice(v)
    if rng.random() < rate or len(g["coins"]) != g["ncoins"]:
        g["coins"] = sorted(rng.sample(TRAIN_COINS, g["ncoins"]))
    return g


def cross(a, b, rng):
    g = {k: (a[k] if rng.random() < 0.5 else b[k]) for k in SPACE}
    g["coins"] = sorted(rng.sample(TRAIN_COINS, g["ncoins"]))
    return g


def key(g):
    fam = {"turtle": ["entry_n", "exit_n"], "keltner": ["kel_n", "kel_mult"], "sma": ["fast", "slow"],
           "multi": ["threshold", "roc_min", "vol_x", "donch"]}[g["family"]]
    return json.dumps({k: g.get(k) for k in ["family", "trail", "stop", "filter", "coins", "size_mode", "maker"] + fam}, sort_keys=True)


def spec_of(g):
    risk = {}
    if g["trail"]: risk["trailing_stop_pct"] = g["trail"]
    if g["stop"]: risk["stop_loss_pct"] = g["stop"]
    f = g["family"]
    if f == "turtle":
        rules = [{"when": {"left": "price", "op": "cross_above", "right": f"donchian_high:{g['entry_n']}"}, "then": {"action": "buy", "size": "100%"}},
                 {"when": {"left": "price", "op": "cross_below", "right": f"donchian_low:{g['exit_n']}"}, "then": {"action": "sell", "size": "all"}}]
        return {"name": "slow50 turtle", "costs": FEES, "risk": risk, "rules": rules}
    if f == "keltner":
        ref = lambda side: f"keltner_{side}:{g['kel_n']}:10:{g['kel_mult']}"
        rules = [{"when": {"left": "price", "op": "cross_above", "right": ref("upper")}, "then": {"action": "buy", "size": "100%"}},
                 {"when": {"left": "price", "op": "cross_below", "right": ref("lower")}, "then": {"action": "sell", "size": "all"}}]
        return {"name": "slow50 keltner", "costs": FEES, "risk": risk, "rules": rules}
    if f == "sma":
        fast, slow = min(g["fast"], g["slow"] - 10), g["slow"]
        rules = [{"when": {"left": f"sma:{fast}", "op": "cross_above", "right": f"sma:{slow}"}, "then": {"action": "buy", "size": "100%"}},
                 {"when": {"left": f"sma:{fast}", "op": "cross_below", "right": f"sma:{slow}"}, "then": {"action": "sell", "size": "all"}}]
        return {"name": "slow50 sma", "costs": FEES, "risk": risk, "rules": rules}
    return {"name": "slow50 multi", "costs": FEES, "risk": risk,
            "entry": {"threshold": g["threshold"], "size": "100%", "signals": [
                {"name": "trend", "when": {"left": "sma:20", "op": ">", "right": "sma:50"}, "weight": 1},
                {"name": "breakout", "when": {"left": "price", "op": ">", "right": f"donchian_high:{g['donch']}"}, "weight": 1},
                {"name": "momentum", "when": {"left": "roc:7", "op": ">", "right": g["roc_min"]}, "weight": 1},
                {"name": "volume", "when": {"left": "vol_ratio:20", "op": ">", "right": g["vol_x"]}, "weight": 1},
                {"name": "not_overbought", "when": {"left": "rsi:14", "op": "<", "right": 75}, "weight": 1}]},
            "rules": [{"when": {"left": "sma:20", "op": "cross_below", "right": "sma:50"}, "then": {"action": "sell", "size": "all"}}]}


_SIZE_CACHE = {}


def size_map_for(mode, bars):
    """{bar ts: fraction of the normal entry size}. Uses only that bar's own regime / ADX (known at its close)."""
    if mode in (None, "full"):
        return None
    if mode in _SIZE_CACHE:
        return _SIZE_CACHE[mode]
    from . import regimes, indicators as I
    btc = bars["BTC-USD"]
    m = {}
    if mode in ("half_in_chop", "third_in_chop"):
        lab = regimes.label_map(btc)
        f = 0.5 if mode == "half_in_chop" else 0.34
        for b in btc:
            m[b.ts] = f if lab.get(b.ts) in ("SIDEWAYS", "HIGH_VOLATILITY") else 1.0
    else:       # adx_scaled: 1.0 when BTC trend strength (ADX) >= 30, falling to 0.4 by ADX 15
        a = I.adx([b.high for b in btc], [b.low for b in btc], [b.close for b in btc], 14)
        for b, v in zip(btc, a):
            m[b.ts] = 1.0 if v is None else max(0.4, min(1.0, (v - 15) / 15 * 0.6 + 0.4))
    _SIZE_CACHE[mode] = m
    return m


def run_window(g, bars, allow, a, b, cash=50.0):
    """Pooled equity of the genome over bars[a:b] (with 220 warm-up bars before a)."""
    spec, _ = kraken_fees.apply_to_spec(spec_of(g))
    per = cash / len(g["coins"])
    tot, trips = None, 0
    for c in g["coins"]:
        bs = bars[c][max(0, a - 220):b]
        off = min(220, a)
        kw = {}
        sm = size_map_for(g.get("size_mode", "full"), bars)
        if sm is not None:
            kw["size_map"] = sm
        if g.get("maker"):
            from . import maker_fill
            kw["maker"] = maker_fill.config(); kw["stats"] = {}
        r = engine.run(Strategy(spec), bs, cash=per, allow_buy_ts=allow, **kw)
        e = r.equity[off:]
        tot = e if tot is None else [x + y for x, y in zip(tot, e)]
        trips += sum(1 for f in r.fills if f.side == "sell")
    return tot, trips


def metrics_of(eq, trips):
    peak, dd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v); dd = min(dd, v / peak - 1)
    return {"ret": eq[-1] / eq[0] - 1, "dd": dd, "trips": trips}


def score(eps):
    rets = sorted(e["ret"] for e in eps)
    med = rets[len(rets) // 2]
    mean = sum(rets) / len(rets)
    avg_dd = sum(abs(e["dd"]) for e in eps) / len(eps)
    worst_dd = max(abs(e["dd"]) for e in eps)
    s = 100 * (0.5 * med + 0.5 * mean - 1.5 * avg_dd) - 40 * max(0.0, worst_dd - 0.30)
    if sum(e["trips"] for e in eps) / len(eps) < 3:
        s -= 20
    return round(s, 2)


class World:
    def __init__(self, bars, allow_by_filter):
        self.bars, self.allow = bars, allow_by_filter
        self.ts = [b.ts for b in (bars.get("BTC-USD") or next(iter(bars.values())))]

    def episodes(self, first_ts, last_ts, length=365, step=30):
        idx = [i for i, t in enumerate(self.ts) if first_ts <= t <= last_ts]
        return [(s, s + length) for s in range(idx[0], idx[-1] - length + 1, step)] if idx else []

    def evaluate(self, g, eps, coins=None):
        gg = dict(g)
        if coins is not None:
            gg["coins"] = coins
        out = []
        for a, b in eps:
            eq, tr = run_window(gg, self.bars, self.allow[g["filter"]], a, b)
            out.append(metrics_of(eq, tr))
        return out


def load():
    try:
        return json.load(open(FILE))
    except Exception:
        return {"seen": {}, "hall_of_fame": [], "generations": 0, "log": []}


_UNSEEN_CACHE = {}


def unseen_world(coins, allow_by_filter, min_bars=450, bars_wanted=2600):
    """Build a World over exactly these (truly held-out) coins, aligned only to EACH OTHER -- not padded down
    to the shortest-listed coin across all 20 holdout coins, so an older subset keeps its longer history.
    Cached by coin combo (validate() calls this with the same few subsets repeatedly)."""
    from . import data as _data
    key = tuple(sorted(coins))
    if key in _UNSEEN_CACHE:
        return _UNSEEN_CACHE[key]
    out = {}
    for c in coins:
        try:
            b = _data.get_bars(c, 86400, max_bars=bars_wanted)
            if len(b) >= min_bars:
                out[c] = b
        except Exception:
            pass
    if len(out) < len(coins):
        _UNSEEN_CACHE[key] = None
        return None
    n = min(len(b) for b in out.values())
    aligned = {c: b[-n:] for c, b in out.items()}
    w = World(aligned, allow_by_filter)
    _UNSEEN_CACHE[key] = w
    return w


def evolve(world, eps, generations=12, pop=30, elite=6, seed=1, verbose=True):
    mem = load()
    rng = random.Random(seed + mem["generations"])
    seeds = [h["genome"] for h in mem["hall_of_fame"][:elite]]
    population = (seeds + [mutate(g, rng, 0.4) for g in seeds] + [random_genome(rng) for _ in range(pop)])[:pop]
    curve = []
    for gen in range(generations):
        scored = []
        for g in population:
            k = key(g)
            if k not in mem["seen"]:
                e = world.evaluate(g, eps)
                mem["seen"][k] = {"genome": g, "train_score": score(e), "train_ret": round(sorted(x["ret"] for x in e)[len(e) // 2] * 100, 1),
                                  "train_dd": round(sum(abs(x["dd"]) for x in e) / len(e) * 100, 1),
                                  "train_trades": round(sum(x["trips"] for x in e) / len(e), 1)}
            scored.append(mem["seen"][k])
        scored.sort(key=lambda r: -r["train_score"])
        curve.append(scored[0]["train_score"])
        if verbose:
            b = scored[0]
            print(f"gen {gen+1:2d}: best {b['train_score']:7.2f} | median yr {b['train_ret']:+6.1f}% avg dd {b['train_dd']:4.1f}% trades/yr {b['train_trades']:4.1f} | {key(b['genome'])[:100]}", flush=True)
        parents = [r["genome"] for r in scored[:elite]]
        nxt = list(parents)
        while len(nxt) < pop:
            r = rng.random()
            nxt.append(mutate(rng.choice(parents), rng, 0.3) if r < 0.5 else cross(rng.choice(parents), rng.choice(parents), rng) if r < 0.85 else random_genome(rng))
        population = nxt
    top = sorted(mem["seen"].values(), key=lambda r: -r["train_score"])[:12]
    mem["hall_of_fame"] = [{"genome": r["genome"], "train_score": r["train_score"]} for r in top]
    mem["generations"] += generations
    mem["log"].append({"iso": time.strftime("%Y-%m-%d %H:%M"), "generations": generations, "best_curve": curve})
    json.dump(mem, open(FILE, "w"), indent=1)
    return top, curve
