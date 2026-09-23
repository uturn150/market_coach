"""Bootstrapped-market test for the community-style bots: top configs (chosen by IN-SAMPLE return / green years on
real history) run through N resampled market years. Reports how often a year ends green and the tail.
    python3 bot_lab_mc.py [n_paths]"""
import json, os, sys, time
from marketcoach import market_sim as ms, bot_engine as be, market_filters as mf
from bot_lab import configs, CASH

BASE = os.path.dirname(os.path.abspath(__file__))
WARM, YEAR = 200, 365


def main():
    n_paths = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    rows = json.load(open(os.path.join(BASE, "logs", "bot_lab.json")))
    picks = []
    for fam in ("grid", "dca", "rot"):
        sub = [(n, r) for n, r in rows.items() if r["fam"] == fam]
        picks += [n for n, r in sorted(sub, key=lambda kv: -kv[1]["is_ret"])[:2]]                      # best IN-SAMPLE return
        picks += [n for n, r in sorted(sub, key=lambda kv: -(kv[1]["y"]["green_year_pct"] or 0) * 1000 - kv[1]["is_ret"])[:2]]   # best green-year rate
    picks = list(dict.fromkeys(picks))
    bars = ms.load_long()
    res = {p: [] for p in picks}; dds = {p: [] for p in picks}; wins = {p: [] for p in picks}
    res["BUY&HOLD"] = []
    t = time.time()
    for k, path in enumerate(ms.synth_markets(bars, n_paths, days=WARM + YEAR + 5)):
        btc = path["BTC-USD"]; c = [b.close for b in btc]
        f0 = {b.ts for i, b in enumerate(btc) if i >= 199 and c[i] > sum(c[i - 199:i + 1]) / 200}
        f1 = f0 & mf.f1_timestamps(btc)
        ts = [b.ts for b in btc]
        ok = {"none": None, "F0": (lambda i, s=f0: ts[i] in s), "F1": (lambda i, s=f1: ts[i] in s)}
        C = configs(ok)
        for name in picks:
            r = be.run(C[name][1](), path, cash=CASH)
            e = r.equity[WARM:]
            res[name].append(e[-1] / e[0] - 1)
            peak, m = e[0], 0.0
            for v in e:
                peak = max(peak, v); m = min(m, v / peak - 1)
            dds[name].append(m)
        h = [sum(b[i].close / b[WARM].close for b in path.values()) for i in range(WARM, len(btc))]
        res["BUY&HOLD"].append(h[-1] / h[0] - 1)
        if (k + 1) % 50 == 0:
            print(f"  {k+1}/{n_paths} ({time.time()-t:.0f}s)", flush=True)
    print(f"\n{n_paths} simulated years | config{'':44s} green-yr%  median     p5      p95   avg maxDD  worst")
    out = {}
    for name, v in sorted(res.items(), key=lambda kv: -sum(1 for x in kv[1] if x > 0)):
        s = sorted(v); g = 100 * sum(1 for x in v if x > 0) / len(v)
        d = dds.get(name)
        out[name] = {"green_pct": round(g, 1), "median": round(100 * s[len(s) // 2], 1), "p5": round(100 * s[int(len(s) * .05)], 1),
                     "p95": round(100 * s[int(len(s) * .95)], 1), "worst": round(100 * s[0], 1), "avg_dd": round(100 * sum(d) / len(d), 1) if d else None}
        o = out[name]
        print(f"{'':22s}{name:52s} {g:6.0f}% {o['median']:>+7.1f}% {o['p5']:>+7.1f}% {o['p95']:>+7.1f}%  {str(o['avg_dd']):>8}  {o['worst']:>+7.1f}%")
    json.dump(out, open(os.path.join(BASE, "logs", "bot_lab_mc.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
