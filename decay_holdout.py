"""Held-out test of the time-decaying take-profit on the DCA bot: unseen coin sets + per-year + bootstrap markets.
Variant chosen on the 9-coin 2020-26 set; here it must beat baseline OUT of that set.  python3 decay_holdout.py"""
import bot_universes as bu
from marketcoach import market_sim as ms, bot_engine as be
from bot_lab import configs, CASH
NAME = "dca SO5 dev5% x2.0 tp3% stop0.2 F1"
VARS = {"baseline": None, "decay 3->1 @15d": [(0, .03), (15, .01)], "decay 3->1.5->.5": [(0, .03), (10, .015), (30, .005)]}

def run(C, bars, dec):
    b = C[NAME][1]()
    if dec: b.tp_decay = dec
    r = be.run(b, bars, cash=CASH)
    peak, dd = r.equity[0], 0.0
    for v in r.equity: peak = max(peak, v); dd = min(dd, v / peak - 1)
    return r, dd

def main():
    base = ms.load_long(); f0, f1 = ms.filters_for(base)
    U = {"core9": base, "large12": bu.load(bu.UNIVERSES["large"]), "mid12": bu.load(bu.UNIVERSES["mid"]),
         "new20": bu.load([p[:-4] for p in bu.holdout.holdout_products()])}
    wins = tot = 0
    for uni, bars in U.items():
        ts = [b.ts for b in next(iter(bars.values()))]
        C = configs({"none": None, "F0": (lambda i, s=f0, t=ts: t[i] in s), "F1": (lambda i, s=f1, t=ts: t[i] in s)})
        ref = None
        for tag, dec in VARS.items():
            r, dd = run(C, bars, dec); y = ms.yearly(dict(zip(r.ts, r.equity)))["calendar"]
            ret = r.equity[-1] / r.equity[0] - 1
            print(f"{uni:8s} {tag:18s} ret {ret*100:+7.1f}% dd {dd*100:5.1f}% trips {len(r.trips):5d} | " + " ".join(f"{k}:{v:+.0f}" for k, v in sorted(y.items())), flush=True)
            if tag == "baseline": ref = (ret, y, dd)
            elif uni != "core9":
                tot += 1; wins += (ret > ref[0] and dd >= ref[2] - 0.01 and sum(v > ref[1].get(k, 0) for k, v in y.items()) >= len(y) / 2)
    print(f"\nunseen-universe wins (better return, dd not worse, better in >=half the years): {wins}/{tot}")
main()
