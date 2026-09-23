"""NFIX-style dip system: many concurrent slots over many coins, hourly bars, real fees. 24 coins x 3 years.
Pre-registered grid; reports trades/day, win rate, return, drawdown, held-out (last 40%) and shows what each piece adds.
    python3 nfix_lab.py"""
import itertools, json, os, time
from marketcoach import data, market_filters as mf, regime, bot_engine as be, community_bots as cb
from marketcoach.portfolio_optimize import DEFAULT_BASKET

BASE = os.path.dirname(os.path.abspath(__file__))


def main():
    coins = [f"{c}-USD" for c in DEFAULT_BASKET]
    bars = {p: data.fetch_coinbase_history(p, 3600, 26288, True, cache_tag="_deep") for p in coins}
    n = min(len(b) for b in bars.values()); bars = {p: b[-n:] for p, b in bars.items()}
    ts = [b.ts for b in bars["BTC-USD"]]
    daily = data.get_bars("BTC-USD", 86400, max_bars=1500)
    f1d = regime.risk_on_timestamps("BTC-USD", 86400, max_bars=1500) & mf.f1_timestamps(daily)
    f1 = {t for t in ts if ((t // 86400) * 86400 - 86400) in f1d}
    ok = {"none": None, "F1": (lambda i, s=f1: ts[i] in s)}
    days = n / 24; cut = int(n * 0.6)
    print(f"{len(coins)} coins x {n} hourly bars ({days:.0f} days)\n")
    rows = []
    for slots, rebuys, modes, maker, flt in itertools.product((6, 12), (0, 2), ("all", "m1-3"), (None, 0.002), ("none", "F1")):
        md = ("m1", "m2", "m3", "m4", "m5") if modes == "all" else ("m1", "m2", "m3")
        bot = cb.Breaker(cb.NFIXLite(slots=slots, modes=md, rebuys=rebuys, maker_entry=maker, market_ok=ok[flt], bpd=24), 0.20)
        t0 = time.time()
        r = be.run(bot, bars, cash=1000.0)
        eq = r.equity; peak, dd = eq[0], 0.0
        for v in eq:
            peak = max(peak, v); dd = min(dd, v / peak - 1)
        nets = [x for _, _, x in r.trips]
        rows.append({"cfg": f"slots{slots} rebuys{rebuys} modes:{modes} {'maker' if maker else 'mkt'}-entry {flt}", "tpd": len(r.trips) / days,
                     "win": 100 * sum(1 for x in nets if x > 0) / max(1, len(nets)), "net": 100 * sum(nets) / max(1, len(nets)),
                     "ret": (eq[-1] / eq[0] - 1) * 100, "is": (eq[cut] / eq[0] - 1) * 100, "oos": (eq[-1] / eq[cut] - 1) * 100, "dd": dd * 100})
        x = rows[-1]
        print(f"{x['cfg']:56s} {x['tpd']:5.1f}/day win {x['win']:4.1f}% net/trade {x['net']:+5.2f}% | ret {x['ret']:+7.1f}% (IS {x['is']:+6.1f} OOS {x['oos']:+6.1f}) dd {x['dd']:6.1f}%  [{time.time()-t0:.0f}s]", flush=True)
    good = [r for r in rows if r["is"] > 0 and r["oos"] > 0 and r["dd"] > -30]
    print(f"\n{len(good)}/{len(rows)} configs profitable in-sample AND held-out with drawdown better than -30%")
    for r in sorted(good, key=lambda r: -r["oos"])[:5]:
        print(f"  {r['cfg']:56s} {r['tpd']:5.1f}/day ret {r['ret']:+6.1f}% OOS {r['oos']:+6.1f}% dd {r['dd']:5.1f}% win {r['win']:.0f}%")
    json.dump(rows, open(os.path.join(BASE, "logs", "nfix_lab.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
