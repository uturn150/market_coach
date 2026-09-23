"""Learner replay ("time machine"): would the learner's decisions have made the fleet better, judged
ONLY with information available at each date?

Every DECISION_STEP days (after a warm-up), using bars[:d] only (no future), it does what the live
learner does, on the 11 champions at the real fee tier:
  1. re-validates each champion with the same gates (fee, sample, out-of-sample edge);
  2. tests the pre-registered hypotheses (market_filter_F1, avoid_SIDEWAYS) with the same rule:
     >=40% of strategies improve in both halves AND more than a random-day placebo;
  3. sets each champion's MODE for the next interval:
       PAUSED  fails its gates and no supported variant rescues it (no new entries)
       F1      F1 hypothesis supported, variant improves both halves and passes gates
       SIDE    avoid-sideways hypothesis supported, improves and passes gates
       F0      otherwise (the original behaviour)
Then the next interval is simulated with that mode's precomputed full-history run. Compared against:
static F0 fleet (never learns), static F1 (hindsight, unattainable), and equal-weight buy&hold.

Approximations, stated plainly: (a) a mode switch does not carry the exact open positions across
(each mode's return over the interval comes from its own run); (b) PAUSED is cash (existing positions'
exit is ignored); (c) fleet = equal-weight average of the champions' equity, each compounding
separately; (d) daily bars, 24-coin basket. No lookahead: gates/hypotheses only see data before d.
"""
import json, os, random, time
from . import data, kraken_fees, regime, regimes, market_filters as mf
from . import learner as L
from . import regime_learner as RL
from .learner import gates, CHAMPIONS
from .portfolio_optimize import DEFAULT_BASKET
from .strategy import Strategy
from . import engine

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DECISION_STEP = 45
WARMUP_BARS = 560           # need ~1.5y+ of history before the first decision
PLACEBO_SEEDS = 3
MIN_TESTED, MIN_REPL = 5, 0.4


def _equity(spec, bars, allow):
    """Pooled equity per bar ($1 per coin)."""
    n = min(len(b) for b in bars.values())
    tot = [0.0] * n
    for b in bars.values():
        r = engine.run(Strategy(spec), b[-n:], cash=1.0, allow_buy_ts=allow)
        for i in range(n):
            tot[i] += r.equity[i]
    return tot


def _hyp(name, masked_fn, specs, bars_d, allow_d, cutoff, btc_d):
    """Returns (supported, {champion: (improves_both, var_passes)})."""
    masked = masked_fn(allow_d, btc_d)
    res, improved, tested = {}, 0, 0
    for a, spec in specs.items():
        b, v = gates(spec, bars_d, allow_d), gates(spec, bars_d, masked)
        if "raw" not in b or "raw" not in v:
            continue
        imp = v["raw"]["pf_in"] > b["raw"]["pf_in"] and v["raw"]["out_return"] > b["raw"]["out_return"]
        res[a] = (imp, v["pass"], b)
        tested += 1
        improved += imp
    plac = []
    for seed in range(PLACEBO_SEEDS):
        drop = set(random.Random(seed).sample(sorted(allow_d), max(0, len(allow_d) - len(masked))))
        pm, cnt = allow_d - drop, 0
        for a, spec in specs.items():
            if a not in res:
                continue
            pv = gates(spec, bars_d, pm).get("raw")
            b = res[a][2]["raw"]
            cnt += bool(pv and pv["pf_in"] > b["pf_in"] and pv["out_return"] > b["out_return"])
        plac.append(cnt)
    supported = tested >= MIN_TESTED and improved / tested >= MIN_REPL and improved > max(plac)
    return supported, res, improved, tested, plac


def run(step=DECISION_STEP, verbose=True):
    raw = {f"{c}-USD": data.get_bars(f"{c}-USD", 86400, max_bars=1500) for c in DEFAULT_BASKET}
    n = min(len(b) for b in raw.values())
    bars = {p: b[-n:] for p, b in raw.items()}
    ts = [b.ts for b in bars["BTC-USD"]]
    f0 = regime.risk_on_timestamps("BTC-USD", 86400, max_bars=1500)
    btc = bars["BTC-USD"]
    f1 = f0 & mf.f1_timestamps(btc)
    labels = regimes.label_map(btc)
    side = {t for t in f0 if labels.get(t) != "SIDEWAYS"}
    specs = {a: kraken_fees.apply_to_spec(json.load(open(os.path.join(BASE, "strategies", f"{s}.json"))))[0]
             for a, s in CHAMPIONS.items()}
    if verbose:
        print(f"{n} aligned daily bars ({time.strftime('%Y-%m-%d', time.gmtime(ts[0]))} -> {time.strftime('%Y-%m-%d', time.gmtime(ts[-1]))}); precomputing {len(specs)*3} runs...", flush=True)
    eq = {a: {"F0": _equity(s, bars, f0), "F1": _equity(s, bars, f1), "SIDE": _equity(s, bars, side)} for a, s in specs.items()}
    hold = [sum(bars[p][i].close / bars[p][0].close for p in bars) for i in range(n)]

    managed = {a: [1.0] for a in specs}         # per-champion equity path, index aligned from first decision
    static0 = {a: [1.0] for a in specs}
    static1 = {a: [1.0] for a in specs}
    days, log = [], []
    d = WARMUP_BARS
    while d < n - 1:
        cut = ts[d]
        end = min(d + step, n)
        bars_d = {p: b[:d] for p, b in bars.items()}
        allow_d = {t for t in f0 if t < cut}
        btc_d = bars_d["BTC-USD"]
        sup1, r1, imp1, t1, p1 = _hyp("F1", lambda al, bt: al & mf.f1_timestamps(bt), specs, bars_d, allow_d, cut, btc_d)
        supS, rS, impS, tS, pS = _hyp("SIDE", lambda al, bt: {t for t in al if regimes.label_map(bt).get(t) != "SIDEWAYS"},
                                      specs, bars_d, allow_d, cut, btc_d)
        modes = {}
        for a, spec in specs.items():
            base = gates(spec, bars_d, allow_d)
            if sup1 and a in r1 and r1[a][0] and r1[a][1]:
                modes[a] = "F1"
            elif supS and a in rS and rS[a][0] and rS[a][1]:
                modes[a] = "SIDE"
            elif not base["pass"]:
                modes[a] = "PAUSED"
            else:
                modes[a] = "F0"
        counts = {m: sum(1 for x in modes.values() if x == m) for m in ("F0", "F1", "SIDE", "PAUSED")}
        log.append({"date": time.strftime("%Y-%m-%d", time.gmtime(cut)), "F1_supported": sup1, "F1": f"{imp1}/{t1} vs {p1}",
                    "SIDE_supported": supS, "SIDE": f"{impS}/{tS} vs {pS}", "modes": counts})
        if verbose:
            print(log[-1], flush=True)
        for a in specs:
            base_i = d - 1
            for mode_key, store in ((modes[a], managed[a]), ("F0", static0[a]), ("F1", static1[a])):
                pass
            for i in range(d, end):
                m = modes[a]
                prev = managed[a][-1]
                managed[a].append(prev * (eq[a][m][i] / eq[a][m][i - 1]) if m != "PAUSED" else prev)
                static0[a].append(static0[a][-1] * eq[a]["F0"][i] / eq[a]["F0"][i - 1])
                static1[a].append(static1[a][-1] * eq[a]["F1"][i] / eq[a]["F1"][i - 1])
        days += ts[d:end]
        d = end
    def fleet(paths):
        L_ = len(next(iter(paths.values())))
        return [sum(p[i] for p in paths.values()) / len(paths) for i in range(1, L_)]
    series = {"learner_managed": fleet(managed), "static_F0_never_learns": fleet(static0), "static_F1_hindsight": fleet(static1)}
    h0 = hold[WARMUP_BARS - 1]
    series["buy_and_hold"] = [hold[i] / h0 / len(bars) for i in range(WARMUP_BARS, WARMUP_BARS + len(days))]
    from green_study import stats
    out = {"period": [log[0]["date"], time.strftime("%Y-%m-%d", time.gmtime(days[-1]))], "decisions": log, "results": {}}
    for k, s in series.items():
        st = stats({t: v for t, v in zip(days, s)})
        out["results"][k] = st
    os.makedirs(os.path.join(BASE, "logs"), exist_ok=True)
    json.dump(out, open(os.path.join(BASE, "logs", "learner_replay.json"), "w"), indent=1)
    return out
