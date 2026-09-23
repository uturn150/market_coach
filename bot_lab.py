"""Lab for the community-style bots (grid / DCA-cycle / rotation) on ~6.7 real years (2020-2026), 9 coins.
Grids fixed in advance. Reports calendar-year returns, green-year %, worst year, and in-sample (first 60%) vs
held-out (last 40%) return; then the best-by-IN-SAMPLE config per family goes through 200 bootstrapped market years.
    python3 bot_lab.py"""
import itertools, json, os, sys, time
from marketcoach import market_sim as ms, bot_engine as be, community_bots as cb

BASE = os.path.dirname(os.path.abspath(__file__))
CASH = 100.0


def configs(ok):
    C = {}
    for lv, st, f in itertools.product((4, 8), (0.025, 0.04, 0.06), ("none", "F0", "F1")):
        C[f"grid L{lv} step{st*100:g}% {f}"] = ("grid", lambda ok=ok, lv=lv, st=st, f=f: cb.GridBot(levels=lv, step=st, market_ok=ok[f]))
    for so, dv, m, tp, sl, f in itertools.product((3, 5), (0.03, 0.05), (1.5, 2.0), (0.015, 0.03), (None, 0.20), ("none", "F0", "F1")):
        C[f"dca SO{so} dev{dv*100:g}% x{m} tp{tp*100:g}% stop{sl if sl else '-'} {f}"] = ("dca", lambda ok=ok, so=so, dv=dv, m=m, tp=tp, sl=sl, f=f: cb.DCACycleBot(safety=so, dev=dv, mult=m, tp=tp, stop=sl, market_ok=ok[f]))
    for tp_, lb, ev, f in itertools.product((2, 4), (20, 60), (7, 14), ("F0", "F1")):
        C[f"rotation top{tp_} lb{lb} every{ev} {f}"] = ("rot", lambda ok=ok, tp_=tp_, lb=lb, ev=ev, f=f: cb.RotationBot(top=tp_, lookback=lb, every=ev, market_ok=ok[f]))
    return C


def make_ok(bars):
    f0, f1 = ms.filters_for(bars)
    ts = [b.ts for b in next(iter(bars.values()))]
    return {"none": None, "F0": (lambda i, s=f0: ts[i] in s), "F1": (lambda i, s=f1: ts[i] in s)}


def main():
    bars = ms.load_long()
    ok = make_ok(bars)
    C = configs(ok)
    print(f"{len(C)} configs, {len(bars)} coins, {len(next(iter(bars.values())))} bars", flush=True)
    rows = {}
    t = time.time()
    n = len(next(iter(bars.values()))); cut = int(n * 0.6)
    for name, (fam, mk) in C.items():
        r = be.run(mk(), bars, cash=CASH)
        ser = dict(zip(r.ts, r.equity))
        y = ms.yearly(ser)
        wins = sum(1 for _, _, x in r.trips if x > 0)
        rows[name] = {"fam": fam, "y": y, "is_ret": round((r.equity[cut] / r.equity[0] - 1) * 100, 1),
                      "oos_ret": round((r.equity[-1] / r.equity[cut] - 1) * 100, 1),
                      "trips": len(r.trips), "win": round(100 * wins / max(1, len(r.trips)), 1),
                      "maxdd": round(min(e / max(r.equity[:k + 1]) - 1 for k, e in enumerate(r.equity)) * 100, 1)}
    print(f"ran in {time.time()-t:.0f}s\n")
    years = sorted({y for r in rows.values() for y in r["y"]["calendar"]})
    print(f"{'config':52s} {'trips':>5s} {'win%':>5s}" + "".join(f"{y:>6d}" for y in years) + "  green-yr% median  worst | IS ret  OOS ret  maxDD")
    for fam in ("grid", "dca", "rot"):
        sub = sorted(((n_, r) for n_, r in rows.items() if r["fam"] == fam), key=lambda kv: -(kv[1]["y"]["green_year_pct"] or 0) - kv[1]["y"]["median_pct"] / 100)
        for n_, r in sub[:6]:
            y = r["y"]
            print(f"{n_:52s} {r['trips']:>5d} {r['win']:>5.1f}" + "".join(f"{y['calendar'].get(yy, 0):>+5.0f}%" for yy in years) + f"  {y['green_year_pct']:>7.0f}% {y['median_pct']:>+6.1f}% {y['worst_pct']:>+6.1f}% | {r['is_ret']:>+6.1f}% {r['oos_ret']:>+7.1f}% {r['maxdd']:>6.1f}%")
        print()
    json.dump(rows, open(os.path.join(BASE, "logs", "bot_lab.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
