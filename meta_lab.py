"""Does re-weighting a fleet of bots by past results beat splitting evenly? Replay 2020-2026 (real history),
weekly re-allocation using ONLY data up to the previous Sunday close, 0.10% cost on the weight turned over.
Fleet = 34 signal bots (11 champions x F0/F1 + factory bots) + the grid/DCA/rotation bots, 9-coin universe.
    python3 meta_lab.py"""
import json, os, sys, time
from marketcoach import market_sim as ms, bot_engine as be, meta_allocator as ma
from market_sim_study import bots as signal_bots
from bot_lab import configs
from green_study import stats, week_key

BASE = os.path.dirname(os.path.abspath(__file__))
COST = 0.001
WARM = 120


def fleet_series():
    bars = ms.load_long()
    f0, f1 = ms.filters_for(bars)
    ts = [b.ts for b in next(iter(bars.values()))]
    ok = {"none": None, "F0": (lambda i, s=f0: ts[i] in s), "F1": (lambda i, s=f1: ts[i] in s)}
    S, fam = {}, {}
    for name, (spec, flt) in signal_bots().items():
        e = ms.bot_equity(spec, bars, f0 if flt == "F0" else f1)
        S[name] = [e[t] for t in ts]; fam[name] = "mean" if ("dip" in name or "pullback" in name or "_gr_" in name) else "trend"
    C = configs(ok)
    for name in ("grid L8 step6% F1", "grid L4 step6% F1", "dca SO5 dev5% x2.0 tp3% stop0.2 F1", "dca SO5 dev3% x2.0 tp3% stop0.2 F1", "rotation top4 lb60 every7 F1"):
        r = be.run(C[name][1](), bars, cash=100.0)
        S["bot:" + name] = r.equity; fam["bot:" + name] = "mean" if not name.startswith("rotation") else "trend"
    R = {n: [0.0] + [v[i] / v[i - 1] - 1 for i in range(1, len(v))] for n, v in S.items()}
    btc = bars["BTC-USD"]; c = [b.close for b in btc]
    up = [i >= 199 and c[i] > sum(c[i - 199:i + 1]) / 200 for i in range(len(c))]
    return ts, R, fam, up


def replay(method, ts, R, fam, up, **kw):
    fn = ma.METHODS[method]
    n = len(ts); names = list(R)
    eq, w_prev, cur_w = [1.0], {}, {}
    turnover_total = 0.0
    for i in range(1, n):
        if i >= WARM and (week_key(ts[i - 1]) != week_key(ts[i - 2]) or not cur_w):     # a new week just started: re-allocate from data up to i-1
            hist = {k: R[k][:i] for k in names}
            w = fn(hist, btc_up=up[i - 1], families=fam, **kw)
            tv = sum(abs(w.get(k, 0.0) - cur_w.get(k, 0.0)) for k in set(w) | set(cur_w))
            turnover_total += tv
            cur_w = w
            eq.append(eq[-1] * (1 - COST * tv))
            eq[-1] *= 1 + sum(cur_w.get(k, 0.0) * R[k][i] for k in names)
        else:
            eq.append(eq[-1] * (1 + sum(cur_w.get(k, 0.0) * R[k][i] for k in names)))
    return eq, turnover_total


def main():
    ts, R, fam, up = fleet_series()
    print(f"fleet of {len(R)} bots, {len(ts)} days {time.strftime('%Y-%m-%d', time.gmtime(ts[0]))} -> {time.strftime('%Y-%m-%d', time.gmtime(ts[-1]))}\n", flush=True)
    start = WARM + 5
    hold = {t: sum(1 for _ in [0]) for t in ts[:0]}
    rows = {}
    print(f"{'method':11s} {'return':>9s} {'CAGR':>7s} {'maxDD':>7s} {'green wk%':>9s} {'of active':>9s} {'worst wk':>8s} {'worst yr':>8s} {'green yrs':>9s} {'turnover/yr':>11s}")
    for m in ma.METHODS:
        eq, tv = replay(m, ts, R, fam, up)
        ser = {t: e for t, e in zip(ts[start:], eq[start:])}
        st = stats(ser)
        y = ms.yearly(ser)
        yrs = (len(ser)) / 365
        cagr = (eq[-1] / eq[start]) ** (1 / yrs) - 1
        rows[m] = {"stats": st, "yearly": y, "turnover_per_year": round(tv / yrs, 2), "cagr": round(cagr * 100, 1)}
        print(f"{m:11s} {st['total_return_pct']:>+8.1f}% {cagr*100:>+6.1f}% {st['max_dd_pct']:>6.1f}% {st['green_week_pct']:>8.1f}% {st['green_of_active_wk_pct']:>8.1f}% {st['worst_week_pct']:>7.1f}% {y['worst_pct']:>+7.1f}% {y['green_year_pct']:>8.0f}% {tv/yrs:>10.1f}x", flush=True)
        print(f"{'':11s} calendar: " + " ".join(f"{k}:{v:+.0f}%" for k, v in sorted(y['calendar'].items())))
    json.dump(rows, open(os.path.join(BASE, "logs", "meta_lab.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
