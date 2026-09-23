"""Does F1 also help the 4H fleet?  Same pre-registered filter, 4H bars, real fees, 60/40 split,
plus a random-day placebo of equal size.   python3 filter_study_4h.py"""
import json, os, random
from marketcoach import data, fleet4h, kraken_fees, market_filters as mf
from marketcoach.portfolio_optimize import _pooled

BASE = os.path.dirname(os.path.abspath(__file__))


def main():
    bars, F0 = fleet4h.load_market()
    daily = data.get_bars("BTC-USD", 86400, max_bars=1500)
    F1 = F0 & mf.f1_timestamps_4h(daily, bars["BTC-USD"])
    drop = len(F0) - len(F1)
    print(f"4h bars allowed: F0 {len(F0)}  F1 {len(F1)} (removed {drop})")
    plac = [F0 - set(random.Random(s).sample(sorted(F0), drop)) for s in range(4)]
    tot = {"F0": [], "F1": [], "P": []}
    print(f"{'family':10s}{'F0 PFin':>9}{'F1 PFin':>9}{'F0 OOS':>9}{'F1 OOS':>9}{'F0 n':>7}{'F1 n':>7}  improves")
    for fam in fleet4h.FAMILIES:
        path = os.path.join(BASE, "strategies", f"c4h_{fam}.json")
        if not os.path.exists(path):
            continue
        spec, _ = kraken_fees.apply_to_spec(json.load(open(path)))
        r0 = _pooled(spec, bars, 50.0, 0.6, F0); r1 = _pooled(spec, bars, 50.0, 0.6, F1)
        if not r0 or not r1:
            continue
        pl = [(_pooled(spec, bars, 50.0, 0.6, m) or {"out_return": 0})["out_return"] for m in plac]
        imp = r1["pf_in"] > r0["pf_in"] and r1["out_return"] > r0["out_return"]
        tot["F0"].append(r0["out_return"] * 100); tot["F1"].append(r1["out_return"] * 100)
        tot["P"].append(100 * sum(pl) / len(pl))
        print(f"{fam:10s}{r0['pf_in']:9.2f}{r1['pf_in']:9.2f}{r0['out_return']*100:+8.1f}%{r1['out_return']*100:+8.1f}%{r0['n_in']:7d}{r1['n_in']:7d}  {imp}")
    n = len(tot["F0"])
    print(f"MEAN OOS: F0 {sum(tot['F0'])/n:+.1f}%  F1 {sum(tot['F1'])/n:+.1f}%  random-removal placebo {sum(tot['P'])/n:+.1f}%   positive OOS: F0 {sum(x>0 for x in tot['F0'])}/{n}  F1 {sum(x>0 for x in tot['F1'])}/{n}")


if __name__ == "__main__":
    main()
