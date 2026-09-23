"""Run every bot through hundreds of simulated market YEARS (block-bootstrapped from ~6.7 real years,
see marketcoach/market_sim.py) and report how often a year ends green.   python3 market_sim_montecarlo.py [n_paths]
A path = 565 days; the first 200 are warm-up for the 200-day filter, the last 365 are the scored 'year'."""
import json, os, sys, time
from marketcoach import market_sim as ms, engine, kraken_fees, market_filters as mf
from marketcoach.strategy import Strategy
from market_sim_study import bots

BASE = os.path.dirname(os.path.abspath(__file__))
WARM, YEAR = 200, 365


def main():
    n_paths = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    bars = ms.load_long()
    B = {name: (kraken_fees.apply_to_spec(spec)[0], flt) for name, (spec, flt) in bots().items()}
    res = {name: [] for name in B}
    res["BUY&HOLD"] = []
    dd = {name: [] for name in B}
    combos = {"DIP4 (gr, large)": [n for n in B if n.startswith("chal_gr_") and n.endswith("_large")],
              "ALL F1 champions": [n for n in B if n.endswith(" F1")]}
    for c in combos: res[c] = []; dd[c] = []
    t = time.time()
    for k, path in enumerate(ms.synth_markets(bars, n_paths, days=WARM + YEAR + 5)):
        btc = path["BTC-USD"]; c = [b.close for b in btc]
        f0 = {b.ts for i, b in enumerate(btc) if i >= 199 and c[i] > sum(c[i - 199:i + 1]) / 200}
        f1 = f0 & mf.f1_timestamps(btc)
        cur = {}
        for name, (spec, flt) in B.items():
            tot = None
            for b in path.values():
                r = engine.run(Strategy(spec), b, cash=1.0, allow_buy_ts=f0 if flt == "F0" else f1)
                tot = r.equity if tot is None else [x + y for x, y in zip(tot, r.equity)]
            e = tot[WARM:]
            cur[name] = e
            res[name].append(e[-1] / e[0] - 1)
            peak, m = e[0], 0.0
            for v in e:
                peak = max(peak, v); m = min(m, v / peak - 1)
            dd[name].append(m)
        h = [sum(b[i].close / b[WARM].close for b in path.values()) for i in range(WARM, len(btc))]
        res["BUY&HOLD"].append(h[-1] / h[0] - 1)
        for cname, members in combos.items():
            e = [sum(cur[m][i] for m in members) for i in range(len(cur[members[0]]))]
            res[cname].append(e[-1] / e[0] - 1)
            peak, m_ = e[0], 0.0
            for v in e:
                peak = max(peak, v); m_ = min(m_, v / peak - 1)
            dd[cname].append(m_)
        if (k + 1) % 50 == 0:
            print(f"  {k+1}/{n_paths} paths ({time.time()-t:.0f}s)", flush=True)
    out = {}
    print(f"\n{n_paths} simulated years | {'bot':34s} green-yr%   median     p5      p95   avg max-dd   worst")
    for name, v in sorted(res.items(), key=lambda kv: -sum(1 for x in kv[1] if x > 0)):
        s = sorted(v); g = 100 * sum(1 for x in v if x > 0) / len(v)
        d = dd.get(name)
        out[name] = {"green_pct": round(g, 1), "median": round(100 * s[len(s) // 2], 1), "p5": round(100 * s[int(len(s) * .05)], 1),
                     "p95": round(100 * s[int(len(s) * .95)], 1), "worst": round(100 * s[0], 1),
                     "avg_dd": round(100 * sum(d) / len(d), 1) if d else None}
        o = out[name]
        print(f"{'':22s}{name:34s} {g:6.0f}%  {o['median']:>+6.1f}% {o['p5']:>+6.1f}% {o['p95']:>+6.1f}%  {o['avg_dd'] if o['avg_dd'] is not None else '':>8}   {o['worst']:>+6.1f}%")
    json.dump(out, open(os.path.join(BASE, "logs", "market_sim_montecarlo.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
