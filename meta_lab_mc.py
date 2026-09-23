"""Allocator methods vs equal split across bootstrapped market years.  python3 meta_lab_mc.py [n_paths]"""
import json, os, sys, time
from marketcoach import market_sim as ms, bot_engine as be, meta_allocator as ma, market_filters as mf, kraken_fees
from marketcoach.strategy import Strategy
from marketcoach import engine
from market_sim_study import bots as signal_bots
from bot_lab import configs
from meta_lab import replay

BASE = os.path.dirname(os.path.abspath(__file__))
YEAR = 365
COMM = ("grid L8 step6% F1", "grid L4 step6% F1", "dca SO5 dev5% x2.0 tp3% stop0.2 F1", "dca SO5 dev3% x2.0 tp3% stop0.2 F1", "rotation top4 lb60 every7 F1")


def build(path):
    btc = path["BTC-USD"]; c = [b.close for b in btc]
    f0 = {b.ts for i, b in enumerate(btc) if i >= 199 and c[i] > sum(c[i - 199:i + 1]) / 200}
    f1 = f0 & mf.f1_timestamps(btc)
    ts = [b.ts for b in btc]
    ok = {"none": None, "F0": (lambda i, s=f0: ts[i] in s), "F1": (lambda i, s=f1: ts[i] in s)}
    S, fam = {}, {}
    for name, (spec, flt) in SPECS.items():
        tot = None
        for b in path.values():
            r = engine.run(Strategy(spec), b, cash=1.0, allow_buy_ts=f0 if flt == "F0" else f1)
            tot = r.equity if tot is None else [x + y for x, y in zip(tot, r.equity)]
        S[name] = tot; fam[name] = "mean" if ("dip" in name or "pullback" in name or "_gr_" in name) else "trend"
    C = configs(ok)
    for n in COMM:
        S["bot:" + n] = be.run(C[n][1](), path, cash=100.0).equity; fam["bot:" + n] = "trend" if n.startswith("rotation") else "mean"
    R = {n: [0.0] + [v[i] / v[i - 1] - 1 for i in range(1, len(v))] for n, v in S.items()}
    up = [i >= 199 and c[i] > sum(c[i - 199:i + 1]) / 200 for i in range(len(c))]
    return ts, R, fam, up


SPECS = {n: (kraken_fees.apply_to_spec(s)[0], f) for n, (s, f) in signal_bots().items()}


def main():
    n_paths = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    bars = ms.load_long()
    res = {m: [] for m in ma.METHODS}; dd = {m: [] for m in ma.METHODS}
    t = time.time()
    for k, path in enumerate(ms.synth_markets(bars, n_paths, days=565)):
        ts, R, fam, up = build(path)
        for m in ma.METHODS:
            eq, _ = replay(m, ts, R, fam, up)
            e = eq[-YEAR:]
            res[m].append(e[-1] / e[0] - 1)
            peak, mm = e[0], 0.0
            for v in e:
                peak = max(peak, v); mm = min(mm, v / peak - 1)
            dd[m].append(mm)
        if (k + 1) % 20 == 0:
            print(f"  {k+1}/{n_paths} ({time.time()-t:.0f}s)", flush=True)
    print(f"\n{n_paths} simulated years | method     green-yr%  median     p5      p95   avg maxDD   worst")
    out = {}
    for m, v in sorted(res.items(), key=lambda kv: -sum(1 for x in kv[1] if x > 0)):
        s = sorted(v); g = 100 * sum(1 for x in v if x > 0) / len(v)
        out[m] = {"green_pct": round(g, 1), "median": round(100 * s[len(s) // 2], 1), "p5": round(100 * s[int(len(s) * .05)], 1),
                  "p95": round(100 * s[int(len(s) * .95)], 1), "avg_dd": round(100 * sum(dd[m]) / len(dd[m]), 1), "worst": round(100 * s[0], 1)}
        o = out[m]
        print(f"{'':24s}{m:11s} {g:6.0f}% {o['median']:>+7.1f}% {o['p5']:>+7.1f}% {o['p95']:>+7.1f}%  {o['avg_dd']:>8}  {o['worst']:>+7.1f}%")
    json.dump(out, open(os.path.join(BASE, "logs", "meta_lab_mc.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
