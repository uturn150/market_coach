"""Penny-trader feasibility: can a maker-only grid harvest many small gains per day AFTER fees?
Hourly bars (3 years, 12 coins), the daily F1 filter mapped onto hours, real fee schedule and cheaper ones.
Grid step = the gain target per cycle before fees; a maker round trip costs 2 x maker fee.
    python3 penny_lab.py"""
import itertools, json, os, time
from marketcoach import data, market_filters as mf, regime, bot_engine as be, community_bots as cb
from marketcoach.factory import UNIVERSES

BASE = os.path.dirname(os.path.abspath(__file__))
SCHED = {"current 0.40/0.80": (0.0040, 0.0080), "Pro 0.25/0.40": (0.0025, 0.0040), "0.16/0.26": (0.0016, 0.0026), "0.10/0.20": (0.0010, 0.0020)}


def main():
    coins = [f"{c}-USD" for c in UNIVERSES["large"]]
    bars = {p: data.fetch_coinbase_history(p, 3600, 26288, True, cache_tag="_deep") for p in coins}
    n = min(len(b) for b in bars.values())
    bars = {p: b[-n:] for p, b in bars.items()}
    ts = [b.ts for b in bars["BTC-USD"]]
    daily = data.get_bars("BTC-USD", 86400, max_bars=1500)
    f1d = regime.risk_on_timestamps("BTC-USD", 86400, max_bars=1500) & mf.f1_timestamps(daily)
    f1 = {t for t in ts if ((t // 86400) * 86400 - 86400) in f1d}
    days = n / 24
    print(f"{len(coins)} coins x {n} hourly bars ({days:.0f} days)\n")
    print(f"{'grid (levels x step) filter':30s} " + "".join(f"{s:>30s}" for s in SCHED))
    print(f"{'':30s} " + "".join(f"{'ret  trades/day  net/cycle  dd':>30s}" for _ in SCHED))
    res = {}
    for lv, st, flt in itertools.product((3, 5, 8), (0.01, 0.015, 0.02, 0.03), ("none", "F1")):
        row = []
        for sname, (mk, tk) in SCHED.items():
            ok = None if flt == "none" else (lambda i, s=f1: ts[i] in s)
            r = be.run(cb.GridBot(levels=lv, step=st, market_ok=ok, cooldown=24), bars, cash=100.0, maker=mk, taker=tk)
            peak, dd = r.equity[0], 0.0
            for v in r.equity:
                peak = max(peak, v); dd = min(dd, v / peak - 1)
            net = sum(x for _, _, x in r.trips) / max(1, len(r.trips))
            row.append(((r.equity[-1] / r.equity[0] - 1) * 100, len(r.trips) / days, net * 100, dd * 100))
        res[f"L{lv} step{st*100:g}% {flt}"] = row
        print(f"{'L%d step%.1f%% %s' % (lv, st * 100, flt):30s} " + "".join(f"{a:>+7.1f}% {b:>5.1f} {c:>+6.2f}% {d:>6.1f}%" for a, b, c, d in row), flush=True)
    json.dump(res, open(os.path.join(BASE, "logs", "penny_lab.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
