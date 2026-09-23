"""How many trades a day can a bot make AFTER fees, and what does frequency cost? 27 coins x 5 years (daily bars),
real fee tier. The DCA-cycle bot is the frequency engine (small take-profit, many coins, optional MAKER entry to
halve the entry fee). Reports the frontier: trades/day vs held-out return and drawdown.   python3 frequency_lab.py"""
import itertools, json, os, time
from marketcoach import data, market_sim as ms, bot_engine as be, community_bots as cb, market_filters as mf, regime
from coin_universe_list import coins27

BASE = os.path.dirname(os.path.abspath(__file__))


def main():
    prods = coins27()
    bars = {p: data.fetch_coinbase_history(p, 86400, 3300, True, cache_tag="_long") for p in prods}
    n = min(len(b) for b in bars.values())
    bars = {p: b[-n:] for p, b in bars.items()}
    ts = [b.ts for b in bars["BTC-USD"]]
    btc = bars["BTC-USD"]; c = [b.close for b in btc]
    f0 = {b.ts for i, b in enumerate(btc) if i >= 199 and c[i] > sum(c[i - 199:i + 1]) / 200}
    f1 = f0 & mf.f1_timestamps(btc)
    ok = {"none": None, "F1": (lambda i, s=f1: ts[i] in s)}
    days = n
    cut = int(n * 0.6)
    print(f"{len(prods)} coins x {n} days ({time.strftime('%Y-%m-%d', time.gmtime(ts[0]))} -> {time.strftime('%Y-%m-%d', time.gmtime(ts[-1]))})\n", flush=True)
    rows = []
    for tp, dev, base, so, flt in itertools.product((0.008, 0.01, 0.015, 0.02, 0.03), (0.02, 0.03, 0.05), (None, 0.002), (4, 6), ("F1", "none")):
        bot = cb.Breaker(cb.DCACycleBot(safety=so, dev=dev, mult=1.8, tp=tp, stop=0.25, market_ok=ok[flt], base_limit=base), 0.15)
        r = be.run(bot, bars, cash=100.0)
        eq = r.equity
        peak, dd = eq[0], 0.0
        for v in eq:
            peak = max(peak, v); dd = min(dd, v / peak - 1)
        trips_is = sum(1 for k, (_, hold, x) in enumerate(r.trips))
        nets = [x for _, _, x in r.trips]
        rows.append({"cfg": f"tp{tp*100:g}% dev{dev*100:g}% SO{so} {'maker-entry' if base else 'mkt-entry'} {flt}", "tpd": len(r.trips) / days,
                     "net_trade": 100 * sum(nets) / max(1, len(nets)), "win": 100 * sum(1 for x in nets if x > 0) / max(1, len(nets)),
                     "ret": (eq[-1] / eq[0] - 1) * 100, "oos": (eq[-1] / eq[cut] - 1) * 100, "is": (eq[cut] / eq[0] - 1) * 100, "dd": dd * 100})
    print(f"{len(rows)} configs. FRONTIER: best held-out return at each minimum trades/day (must be positive in-sample AND held-out, dd > -25%)")
    for minfreq in (2, 4, 6, 8, 12):
        ok_rows = [r for r in rows if r["tpd"] >= minfreq and r["is"] > 0 and r["oos"] > 0 and r["dd"] > -25]
        if ok_rows:
            b = max(ok_rows, key=lambda r: r["oos"])
            print(f"  >= {minfreq:2d} trades/day: {b['cfg']:46s} {b['tpd']:5.1f}/day net/trade {b['net_trade']:+5.2f}% win {b['win']:4.1f}% return {b['ret']:+6.1f}% (held-out {b['oos']:+5.1f}%) maxDD {b['dd']:5.1f}%")
        else:
            print(f"  >= {minfreq:2d} trades/day: NO config is profitable in-sample AND held-out with dd > -25%")
    print(f"\nmost active configs overall:")
    for r in sorted(rows, key=lambda r: -r["tpd"])[:8]:
        print(f"  {r['cfg']:46s} {r['tpd']:5.1f}/day net/trade {r['net_trade']:+5.2f}% win {r['win']:4.1f}% return {r['ret']:+7.1f}% (IS {r['is']:+6.1f}% OOS {r['oos']:+6.1f}%) dd {r['dd']:6.1f}%")
    json.dump(rows, open(os.path.join(BASE, "logs", "frequency_lab.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
