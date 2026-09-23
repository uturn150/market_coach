"""Do the grid / DCA bots transfer to OTHER coin sets? Same configs (chosen on the 9-coin 2020-26 set), run
unchanged on large12 / mid12 / new20 (coins never used to choose them).  python3 bot_universes.py"""
import json, os, time
from marketcoach import market_sim as ms, bot_engine as be, holdout
from marketcoach.factory import UNIVERSES
from marketcoach import data
from bot_lab import configs, make_ok, CASH

BASE = os.path.dirname(os.path.abspath(__file__))
PICK = ["grid L8 step6% F1", "grid L4 step6% F1", "grid L8 step4% F1", "dca SO5 dev5% x2.0 tp3% stop0.2 F1",
        "dca SO5 dev3% x2.0 tp3% stop0.2 F1", "dca SO5 dev5% x2.0 tp1.5% stop0.2 F1"]


def load(coins):
    out = {}
    for c in coins:
        p = c if "-" in c else f"{c}-USD"
        try:
            b = data.get_bars(p, 86400, max_bars=1500)
            if len(b) > 400: out[p] = b
        except Exception:
            pass
    n = min(len(b) for b in out.values())
    return {p: b[-n:] for p, b in out.items()}


def main():
    base = ms.load_long()                       # BTC for the filters, full 2020+ history
    btc = base["BTC-USD"]
    f0, f1 = ms.filters_for(base)
    U = {"large12": load(UNIVERSES["large"]), "mid12": load(UNIVERSES["mid"]), "new20": load([p[:-4] for p in holdout.holdout_products()])}
    res = {}
    print(f"{'universe':8s} {'config':44s} coins bars  win%  trips | calendar yrs | green-yr% worst-window  maxDD  ret")
    for uni, bars in U.items():
        ts = [b.ts for b in next(iter(bars.values()))]
        ok = {"none": None, "F0": (lambda i, s=f0, t=ts: t[i] in s), "F1": (lambda i, s=f1, t=ts: t[i] in s)}
        C = configs(ok)
        for name in PICK:
            r = be.run(C[name][1](), bars, cash=CASH)
            y = ms.yearly(dict(zip(r.ts, r.equity)))
            win = sum(1 for _, _, x in r.trips if x > 0) / max(1, len(r.trips))
            peak, dd = r.equity[0], 0.0
            for v in r.equity:
                peak = max(peak, v); dd = min(dd, v / peak - 1)
            res[f"{uni}|{name}"] = {"y": y, "win": win, "trips": len(r.trips), "dd": dd, "ret": r.equity[-1] / r.equity[0] - 1, "coins": len(bars), "bars": len(ts)}
            print(f"{uni:8s} {name:44s} {len(bars):4d} {len(ts):5d} {win*100:5.1f} {len(r.trips):6d} | " + " ".join(f"{k}:{v:+.0f}%" for k, v in sorted(y['calendar'].items())) +
                  f" | {y['green_year_pct'] if y['green_year_pct'] is not None else 'n/a'}% {y['worst_pct'] if y['worst_pct'] is not None else 'n/a'}%  {dd*100:.1f}%  {(r.equity[-1]/r.equity[0]-1)*100:+.1f}%", flush=True)
    json.dump(res, open(os.path.join(BASE, "logs", "bot_universes.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
