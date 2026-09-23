"""Yearly green study on ~6.7 years of history (2020-2026: covid crash, 2021 bull, 2022 bear, 2023-24 recovery,
2025-26 bear). Every bot, real fees, its own market filter.   python3 market_sim_study.py"""
import glob, json, os, time
from marketcoach import market_sim as ms
from marketcoach.learner import CHAMPIONS

BASE = os.path.dirname(os.path.abspath(__file__))


def bots():
    out = {}
    for a, s in CHAMPIONS.items():
        spec = json.load(open(os.path.join(BASE, "strategies", f"{s}.json")))
        out[a + " F0"] = (spec, "F0"); out[a + " F1"] = (spec, "F1")
    for f in sorted(glob.glob(os.path.join(BASE, "strategies", "factory", "chal_*.json"))):
        out[os.path.basename(f)[:-5]] = (json.load(open(f)), "F1")
    return out


def main():
    bars = ms.load_long()
    f0, f1 = ms.filters_for(bars)
    ts0 = next(iter(bars.values()))[0].ts
    print(f"{len(bars)} coins {list(bars)} | {time.strftime('%Y-%m-%d', time.gmtime(ts0))} -> {time.strftime('%Y-%m-%d', time.gmtime(next(iter(bars.values()))[-1].ts))}")
    hold = {}
    ser_hold = {b.ts: sum(bars[p][i].close / bars[p][0].close for p in bars) for i, b in enumerate(next(iter(bars.values())))}
    rows = {}
    t = time.time()
    for name, (spec, flt) in bots().items():
        s = ms.bot_equity(spec, bars, f0 if flt == "F0" else f1)
        rows[name] = ms.yearly(s)
    print(f"{len(rows)} bots simulated in {time.time()-t:.0f}s\n")
    hy = ms.yearly(ser_hold)
    years = sorted({y for r in rows.values() for y in r["calendar"]})
    print(f"{'bot':34s}" + "".join(f"{y:>8d}" for y in years) + "   green-yr%  median  p10   worst")
    print(f"{'BUY&HOLD (equal-weight)':34s}" + "".join(f"{hy['calendar'].get(y, 0):>+7.0f}%" for y in years) + f"   {hy['green_year_pct']:>7.0f}%  {hy['median_pct']:>+5.0f}% {hy['p10_pct']:>+5.0f}% {hy['worst_pct']:>+5.0f}%")
    for name, r in sorted(rows.items(), key=lambda kv: -(kv[1]["green_year_pct"] or 0)):
        print(f"{name:34s}" + "".join(f"{r['calendar'].get(y, 0):>+7.0f}%" for y in years) + f"   {r['green_year_pct']:>7.0f}%  {r['median_pct']:>+5.0f}% {r['p10_pct']:>+5.0f}% {r['worst_pct']:>+5.0f}%")
    json.dump(rows, open(os.path.join(BASE, "logs", "market_sim_yearly.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
