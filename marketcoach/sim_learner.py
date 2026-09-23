"""Learning inside the simulator: evolve bot configurations against simulated market YEARS.

  genome   = bot family (dca | grid) + numeric settings + market filter + breaker
  episode  = one simulated 12-month stretch (start dates roll monthly) played through the market simulator
  fitness  = rewards CONSISTENT green: median episode return, minus 2x average max drawdown, plus the share of
             ACTIVE weeks that end green and the longest run of green weeks; a genome that loses > 10% in any
             training episode, or barely trades, is penalised hard
  learning = evolutionary search (elite survival + mutation + crossover), every genome evaluated on TRAINING
             episodes only; the survivors are then judged on years they never saw AND on coins they never saw
The hall of fame and every evaluated genome are stored (paper_state/sim_learner.json), so each run continues from what
was learned before.  Engine: 'daily' (bot_engine on daily bars) for fast development, 'minute' (time_machine) when the
minute data is complete.
"""
import copy, json, math, os, random, time
from . import bot_engine as be, community_bots as cb, market_sim as ms

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILE = os.path.join(BASE, "paper_state", "sim_learner.json")

SPACE = {"family": ["dca", "grid"],
         "safety": [3, 4, 5, 6], "dev": [0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.09], "mult": [1.3, 1.5, 1.8, 2.0, 2.4],
         "tp": [0.01, 0.015, 0.02, 0.03, 0.04, 0.05, 0.07], "stop": [None, 0.15, 0.20, 0.30],
         "levels": [4, 6, 8, 10], "step": [0.025, 0.03, 0.04, 0.05, 0.06, 0.08],
         "filter": ["none", "F0", "F1"], "breaker": [None, 0.08, 0.12, 0.15]}


def random_genome(rng):
    g = {k: rng.choice(v) for k, v in SPACE.items()}
    return g


def mutate(g, rng, rate=0.3):
    g = dict(g)
    for k, v in SPACE.items():
        if rng.random() < rate:
            g[k] = rng.choice(v)
    return g


def cross(a, b, rng):
    return {k: (a[k] if rng.random() < 0.5 else b[k]) for k in SPACE}


def key(g):
    rel = ["family", "filter", "breaker"] + (["safety", "dev", "mult", "tp", "stop"] if g["family"] == "dca" else ["levels", "step"])
    return json.dumps({k: g[k] for k in rel}, sort_keys=True)


def make_bot(g, ok):
    mo = {"none": None, "F0": ok["F0"], "F1": ok["F1"]}[g["filter"]]
    if g["family"] == "dca":
        bot = cb.DCACycleBot(safety=g["safety"], dev=g["dev"], mult=g["mult"], tp=g["tp"], stop=g["stop"], market_ok=mo)
    else:
        bot = cb.GridBot(levels=g["levels"], step=g["step"], market_ok=mo)
    return cb.Breaker(bot, dd_limit=g["breaker"]) if g["breaker"] else bot


def week_stats(eq, ts):
    wk = {}
    for t, e in zip(ts, eq):
        wk[(t // 86400 + 3) // 7] = e
    ks = sorted(wk)
    r = [wk[ks[i]] / wk[ks[i - 1]] - 1 for i in range(1, len(ks))]
    act = [x for x in r if abs(x) > 1e-9]
    run = best = 0
    for x in r:
        run = run + 1 if x > 1e-9 else 0
        best = max(best, run)
    return (sum(1 for x in act if x > 0) / len(act) if act else 0.0), best, len(act)


def episode_metrics(eq, ts, trips):
    ret = eq[-1] / eq[0] - 1
    peak, dd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v); dd = min(dd, v / peak - 1)
    g, streak, active = week_stats(eq, ts)
    return {"ret": ret, "dd": dd, "green_active": g, "streak": streak, "active_weeks": active, "trips": len(trips)}


def score(eps):
    rets = sorted(e["ret"] for e in eps)
    med = rets[len(rets) // 2]
    avg_dd = sum(abs(e["dd"]) for e in eps) / len(eps)
    green = sum(e["green_active"] for e in eps) / len(eps)
    streak = sum(e["streak"] for e in eps) / len(eps)
    s = 100 * (med - 2.0 * avg_dd) + 20 * green + 1.0 * min(streak, 12)
    if rets[0] < -0.10:
        s -= 100 * abs(rets[0] + 0.10) * 3
    if sum(e["trips"] for e in eps) / len(eps) < 15:
        s -= 15
    return round(s, 2)


_UNSEEN_CACHE = {}


def unseen_world(coins, f0, f1, min_bars=450, bars_wanted=2600):
    """A World over exactly these REAL held-out coins (marketcoach.holdout.holdout_products() -- the ~20 coins
    never used to build any strategy in this project), aligned only to each other so an older subset keeps its
    longer history instead of everyone being truncated to the newest listing. Cached by coin combo.
    Added 2026-09-22 alongside the same fix in slow_learner.py: the OLD "unseen" check here (sim_learner_run.py's
    valid_coins = coins[5:]) was still drawing from the same in-basket coin list as training -- not independent."""
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
    w = World({c: b[-n:] for c, b in out.items()}, f0, f1)
    _UNSEEN_CACHE[key] = w
    return w


class MinuteWorld:
    """Same genome -> episode -> score pipeline as World, but fills are simulated MINUTE BY MINUTE on real minute
    data (marketcoach.time_machine) instead of the daily engine. Slower (seconds per episode instead of
    milliseconds), so this is meant for a final check on a handful of finalist genomes, not the search loop itself
    -- exactly the 'minute data is complete now' step flagged as pending in this project's notes."""
    def __init__(self, coins, f0, f1):
        from . import time_machine as tm
        self.coins, self.f0, self.f1 = coins, f0, f1
        self.store = tm.MinuteDays(coins)

    def episodes(self, first_ts, last_ts, length_days=365, step_days=30):
        return [(s, s + length_days * 86400) for s in range(first_ts, last_ts - length_days * 86400 + 1, step_days * 86400)]

    def run_episode(self, g, a, b):
        from . import time_machine as tm
        DAY = 86400
        days = list(range(a, b + DAY, DAY))     # must match tm.run's own internal day list exactly (same a, b)
        ok = {"F0": (lambda i, t=days, S=self.f0: t[i] in S), "F1": (lambda i, t=days, S=self.f1: t[i] in S)}
        bot = make_bot(g, ok)
        eq, ts, ctx = tm.run(bot, self.coins, a, b, cash=100.0, store=self.store)
        return episode_metrics(eq, ts, ctx.trips)

    def evaluate(self, g, eps):
        return [self.run_episode(g, s, e) for s, e in eps]


class World:
    """A set of coins over a stretch of days, with filters, that can produce episodes for a genome."""
    def __init__(self, bars, f0, f1):
        self.bars, self.f0, self.f1 = bars, f0, f1
        self.ts = [b.ts for b in next(iter(bars.values()))]

    def episodes(self, first_ts, last_ts, length_days=365, step_days=30):
        idx = [i for i, t in enumerate(self.ts) if first_ts <= t <= last_ts]
        if not idx:
            return []
        a, b = idx[0], idx[-1]
        return [(s, s + length_days) for s in range(a, b - length_days + 1, step_days)]

    def run_episode(self, g, s, e):
        sub = {c: bs[max(0, s - 220):e] for c, bs in self.bars.items()}       # 220 warm-up days for the 200-day filters
        off = min(220, s)
        ts = [b.ts for b in next(iter(sub.values()))]
        ok = {"F0": (lambda i, t=ts, S=self.f0: t[i] in S), "F1": (lambda i, t=ts, S=self.f1: t[i] in S)}
        r = be.run(make_bot(g, ok), sub, cash=100.0)
        return episode_metrics(r.equity[off:], ts[off:], r.trips)

    def evaluate(self, g, eps):
        return [self.run_episode(g, s, e) for s, e in eps]


def load():
    try:
        return json.load(open(FILE))
    except Exception:
        return {"seen": {}, "hall_of_fame": [], "generations": 0, "log": []}


def evolve(train_world, train_eps, generations=12, pop=30, elite=6, seed=1, verbose=True):
    mem = load()
    rng = random.Random(seed + mem["generations"])
    seeds = [h["genome"] for h in mem["hall_of_fame"][:elite]]
    population = seeds + [mutate(g, rng, 0.4) for g in seeds] + [random_genome(rng) for _ in range(pop - 2 * len(seeds))]
    population = population[:pop]
    curve = []
    for gen in range(generations):
        scored = []
        for g in population:
            k = key(g)
            if k not in mem["seen"]:
                eps = train_world.evaluate(g, train_eps)
                mem["seen"][k] = {"genome": g, "train_score": score(eps),
                                  "train_median_ret": round(sorted(e["ret"] for e in eps)[len(eps) // 2] * 100, 1),
                                  "train_avg_dd": round(sum(abs(e["dd"]) for e in eps) / len(eps) * 100, 1),
                                  "train_green_active": round(sum(e["green_active"] for e in eps) / len(eps) * 100, 1),
                                  "train_streak": round(sum(e["streak"] for e in eps) / len(eps), 1)}
            scored.append(mem["seen"][k])
        scored.sort(key=lambda r: -r["train_score"])
        curve.append(scored[0]["train_score"])
        if verbose:
            b = scored[0]
            print(f"gen {gen+1:2d}: best train score {b['train_score']:7.2f} | median ret {b['train_median_ret']:+5.1f}% avg dd {b['train_avg_dd']:4.1f}% "
                  f"green(active wk) {b['train_green_active']:4.1f}% streak {b['train_streak']} | {key(b['genome'])[:95]}", flush=True)
        parents = [r["genome"] for r in scored[:elite]]
        nxt = list(parents)
        while len(nxt) < pop:
            r = rng.random()
            if r < 0.5: nxt.append(mutate(rng.choice(parents), rng, 0.3))
            elif r < 0.85: nxt.append(cross(rng.choice(parents), rng.choice(parents), rng))
            else: nxt.append(random_genome(rng))
        population = nxt
    top = sorted(mem["seen"].values(), key=lambda r: -r["train_score"])[:12]
    mem["hall_of_fame"] = [{"genome": r["genome"], "train_score": r["train_score"]} for r in top]
    mem["generations"] += generations
    mem["log"].append({"iso": time.strftime("%Y-%m-%d %H:%M"), "generations": generations, "best_curve": curve})
    json.dump(mem, open(FILE, "w"), indent=1)
    return top, curve
