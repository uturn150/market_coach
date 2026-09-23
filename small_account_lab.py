"""$50 account: what if each trade is $10 (5 at a time), $25 (2 at a time) or the WHOLE $50 (1 at a time)?
27 coins x 5 years of daily bars, real fees, Kraken-like minimum order ($7), dip-buying with a small take-profit.
    python3 small_account_lab.py"""
import itertools, json, os, time
from marketcoach import data, bot_engine as be, community_bots as cb, market_filters as mf
from coin_universe_list import coins27

BASE = os.path.dirname(os.path.abspath(__file__))


def main():
    prods = [p for p in coins27() if p not in ("ZEC-USD", "UNI-USD")]        # their real minimum order is >$12
    bars = {p: data.fetch_coinbase_history(p, 86400, 3300, True, cache_tag="_long") for p in prods}
    n = min(len(b) for b in bars.values()); bars = {p: b[-n:] for p, b in bars.items()}
    ts = [b.ts for b in bars["BTC-USD"]]; c = [b.close for b in bars["BTC-USD"]]
    f0 = {b.ts for i, b in enumerate(bars["BTC-USD"]) if i >= 199 and c[i] > sum(c[i - 199:i + 1]) / 200}
    f1 = f0 & mf.f1_timestamps(bars["BTC-USD"])
    ok = {"none": None, "F1": (lambda i, s=f1: ts[i] in s)}
    cut = int(n * 0.6)
    print(f"{len(prods)} coins x {n} days; start balance $50\n")
    rows = []
    for (slot, k, ladder), tp, stop, dip, flt in itertools.product(((10, 5, 0), (10, 5, 1), (25, 2, 2), (25, 2, 0), (50, 1, 4), (50, 1, 0)), (0.015, 0.02, 0.03, 0.05), (None, 0.08), (0.02, 0.04), ("none", "F1")):
        bot = cb.SlotBot(slot_usd=slot, max_slots=k, ladder_n=ladder, dev=0.04, tp=tp, stop=stop, dip_min=dip, min_order=7.0, market_ok=ok[flt])
        r = be.run(bot, bars, cash=50.0)
        eq = r.equity
        peak, dd = eq[0], 0.0
        for v in eq:
            peak = max(peak, v); dd = min(dd, v / peak - 1)
        nets = [x for _, _, x in r.trips]
        rows.append({"cfg": f"${slot}x{k} ladder{ladder} tp{tp*100:g}% stop{'-' if stop is None else int(stop*100)} dip{int(dip*100)}% {flt}", "slot": slot, "k": k, "tpd": len(r.trips) / n,
                     "net": 100 * sum(nets) / max(1, len(nets)), "win": 100 * sum(1 for x in nets if x > 0) / max(1, len(nets)),
                     "ret": (eq[-1] / eq[0] - 1) * 100, "is": (eq[cut] / eq[0] - 1) * 100, "oos": (eq[-1] / eq[cut] - 1) * 100, "dd": dd * 100, "final": eq[-1]})
    print(f"{len(rows)} configs.  Best held-out result per trade size (profitable in-sample AND held-out, max drawdown better than -30%):")
    for slot, k in ((10, 5), (25, 2), (50, 1)):
        sub = [r for r in rows if r["slot"] == slot and r["k"] == k and r["is"] > 0 and r["oos"] > 0 and r["dd"] > -30]
        allr = [r for r in rows if r["slot"] == slot and r["k"] == k]
        if sub:
            b = max(sub, key=lambda r: r["oos"])
            print(f"  ${slot:>2d} x {k}: {b['cfg']:52s} {b['tpd']:4.1f} trades/day, net/trade {b['net']:+5.2f}%, win {b['win']:4.1f}%, final ${b['final']:6.2f} ({b['ret']:+6.1f}% in 5y, held-out {b['oos']:+5.1f}%), maxDD {b['dd']:5.1f}%")
        else:
            print(f"  ${slot:>2d} x {k}: NO config is profitable in-sample AND held-out with drawdown better than -30%   (best final ${max(r['final'] for r in allr):.2f}, worst ${min(r['final'] for r in allr):.2f})")
    print("\nmedian outcome across all configs of each size:")
    for slot, k in ((10, 5), (25, 2), (50, 1)):
        a = sorted(r["ret"] for r in rows if r["slot"] == slot and r["k"] == k)
        t = sorted(r["tpd"] for r in rows if r["slot"] == slot and r["k"] == k)
        print(f"  ${slot} x {k}: median return {a[len(a)//2]:+.1f}%, best {a[-1]:+.1f}%, worst {a[0]:+.1f}%; median trades/day {t[len(t)//2]:.1f}, max {t[-1]:.1f}; {sum(1 for x in a if x>0)}/{len(a)} configs profitable")
    json.dump(rows, open(os.path.join(BASE, "logs", "small_account_lab.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
