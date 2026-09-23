"""Are there bot types that are genuinely DIFFERENT (low correlation with the trend fleet AND with the dip/grid/DCA
group) and still make money?  Three new structural families tested on 2020-26 real history, 9 coins, real fees.
Pre-registered grids; judged on the first 60% / last 40% and by correlation.   python3 diversity_lab.py"""
import itertools, json, math, os
from marketcoach import market_sim as ms, bot_engine as be, community_bots as cb
from bot_lab import configs, make_ok
from market_sim_study import bots as signal_bots
from fleet_audit import daily_rets, corr

BASE = os.path.dirname(os.path.abspath(__file__))


def main():
    bars = ms.load_long(); f0, f1 = ms.filters_for(bars)
    ts = [b.ts for b in next(iter(bars.values()))]
    ok = {"none": None, "F0": (lambda i, s=f0: ts[i] in s), "F1": (lambda i, s=f1: ts[i] in s)}
    # reference groups (daily returns)
    trend = []
    for n, (spec, flt) in signal_bots().items():
        if n.endswith(" F1") and not n.startswith("chal_"):
            trend.append(daily_rets(ms.bot_equity(spec, bars, f1)))
    C = configs(ok)
    div = [daily_rets(dict(zip(ts, be.run(C[n][1](), bars, cash=100.0).equity))) for n in ("grid L8 step6% F1", "dca SO5 dev5% x2.0 tp3% stop0.2 F1")]
    avg = lambda L: {d: sum(x.get(d, 0) for x in L) / len(L) for d in L[0]}
    T, D = avg(trend), avg(div)
    btc = {ts[i]: bars["BTC-USD"][i].close / bars["BTC-USD"][i - 1].close - 1 for i in range(1, len(ts))}
    cand = {}
    for dow, hold, f in itertools.product(range(7), (1, 2, 3), ("none", "F1")):
        cand[f"seasonal buy-dow{dow} hold{hold} {f}"] = lambda dow=dow, hold=hold, f=f: cb.SeasonalBot(dow, hold, ok[f])
    for k, lb, hd, f in itertools.product((2, 3), (14, 30), (7, 14), ("none", "F1")):
        cand[f"catch-up k{k} lb{lb} hold{hd} {f}"] = lambda k=k, lb=lb, hd=hd, f=f: cb.CatchUpBot(k, lb, hd, 7, ok[f])
    for k, hd, tp, f in itertools.product((2.0, 2.5, 3.0), (2, 4), (0.05, 0.08), ("none", "F1")):
        cand[f"vol-spike k{k} hold{hd} tp{tp*100:g}% {f}"] = lambda k=k, hd=hd, tp=tp, f=f: cb.VolSpikeBot(k, hd, tp, ok[f])
    rows = []
    cut = int(len(ts) * 0.6)
    for name, mk in cand.items():
        r = be.run(mk(), bars, cash=100.0)
        eq = r.equity
        rr = daily_rets(dict(zip(ts, eq)))
        peak, dd = eq[0], 0.0
        for v in eq:
            peak = max(peak, v); dd = min(dd, v / peak - 1)
        y = ms.yearly(dict(zip(ts, eq)))
        rows.append({"name": name, "ret": (eq[-1] / eq[0] - 1) * 100, "is": (eq[cut] / eq[0] - 1) * 100, "oos": (eq[-1] / eq[cut] - 1) * 100,
                     "dd": dd * 100, "green_yr": y["green_year_pct"], "trips": len(r.trips), "win": 100 * sum(1 for _, _, x in r.trips if x > 0) / max(1, len(r.trips)),
                     "c_trend": corr(rr, T) or 0, "c_div": corr(rr, D) or 0, "c_btc": corr(rr, btc) or 0})
    good = [r for r in rows if r["ret"] > 0 and r["oos"] > 0 and r["is"] > 0 and r["dd"] > -25 and max(r["c_trend"], r["c_div"]) < 0.5]
    print(f"{len(rows)} candidates; profitable in-sample AND held-out, dd>-25%, corr<0.5 to BOTH reference groups: {len(good)}\n")
    for fam in ("seasonal", "catch-up", "vol-spike"):
        sub = [r for r in rows if r["name"].startswith(fam)]
        pos = [r for r in sub if r["ret"] > 0 and r["oos"] > 0 and r["is"] > 0]
        print(f"{fam:10s}: {len(sub)} configs, {len(pos)} positive in-sample AND held-out; median full-history return {sorted(r['ret'] for r in sub)[len(sub)//2]:+.1f}%, median corr trend {sorted(r['c_trend'] for r in sub)[len(sub)//2]:.2f} / divers {sorted(r['c_div'] for r in sub)[len(sub)//2]:.2f} / BTC {sorted(r['c_btc'] for r in sub)[len(sub)//2]:.2f}")
    print(f"\n{'top by held-out return among the qualifying':60s} ret%   IS%   OOS%   maxDD  trips win% grn-yr  corr: trend divers BTC")
    for r in sorted(good, key=lambda r: -r["oos"])[:10]:
        print(f"{r['name']:60s} {r['ret']:>+6.1f} {r['is']:>+6.1f} {r['oos']:>+6.1f} {r['dd']:>6.1f} {r['trips']:>6d} {r['win']:>4.0f} {str(r['green_yr']):>6s}        {r['c_trend']:.2f}  {r['c_div']:.2f}  {r['c_btc']:.2f}")
    json.dump(rows, open(os.path.join(BASE, "logs", "diversity_lab.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
