"""Why do the champions lose in the held-out (last 40%) period? Attributes every
out-of-sample round trip to the market regime at ENTRY and splits gross move
from fees. Report only.   python3 oos_diagnosis.py"""
import json, os
from collections import defaultdict
from marketcoach import data, engine, kraken_fees, regime, regimes
from marketcoach.learner import CHAMPIONS
from marketcoach.portfolio_optimize import DEFAULT_BASKET
from marketcoach.strategy import Strategy

BASE = os.path.dirname(os.path.abspath(__file__))


def trips(res, rate):
    lots, out = [], []
    for f in res.fills:
        if f.side == "buy":
            lots.append([f.units, f.price, f.ts])
        else:
            rem = f.units
            while rem > 1e-12 and lots:
                l = lots[0]; take = min(l[0], rem)
                out.append((l[2], f.ts, f.price / l[1] - 1, (f.price * (1 - rate)) / (l[1] * (1 + rate)) - 1))
                l[0] -= take; rem -= take
                if l[0] <= 1e-12: lots.pop(0)
    return out


def main():
    coin_bars = {f"{c}-USD": data.get_bars(f"{c}-USD", 86400, max_bars=1500) for c in DEFAULT_BASKET}
    allow = regime.risk_on_timestamps("BTC-USD", 86400, max_bars=1500)
    labels = regimes.label_map(coin_bars["BTC-USD"])
    btc = coin_bars["BTC-USD"]; cut_ts = btc[int(len(btc) * 0.6)].ts
    days = defaultdict(int)
    for b in btc:
        if b.ts >= cut_ts and labels.get(b.ts): days[labels[b.ts]] += 1
    print("OOS period regime days:", dict(days), f"(from {__import__('time').strftime('%Y-%m-%d', __import__('time').gmtime(cut_ts))})")
    agg = defaultdict(lambda: [0, 0.0, 0.0])
    for acct, sname in CHAMPIONS.items():
        spec, _ = kraken_fees.apply_to_spec(json.load(open(os.path.join(BASE, "strategies", f"{sname}.json"))))
        by = defaultdict(list)
        for p, bars in coin_bars.items():
            c = int(len(bars) * 0.6)
            st = Strategy(spec)
            r = engine.run(st, bars[c:], cash=2.0, allow_buy_ts=allow)
            for ets, xts, gross, net in trips(r, st.side_rate):
                lab = labels.get(ets - 86400) or "?"
                by[lab].append((gross, net))
        line = []
        for lab in ("TRENDING_UP", "SIDEWAYS", "HIGH_VOLATILITY", "TRENDING_DOWN"):
            v = by.get(lab, [])
            if v:
                g = 100 * sum(x[0] for x in v) / len(v); n = 100 * sum(x[1] for x in v) / len(v)
                line.append(f"{lab[:5]} n={len(v)} gross {g:+.1f}% net {n:+.1f}%")
                a = agg[lab]; a[0] += len(v); a[1] += sum(x[0] for x in v); a[2] += sum(x[1] for x in v)
        print(f"{acct:15s} " + " | ".join(line))
    print("\nALL champions pooled, OOS, by entry regime:")
    out = {"generated": __import__("time").strftime("%Y-%m-%d %H:%M"), "regime_days": dict(days), "pooled": {}}
    for lab, (n, g, nt) in agg.items():
        out["pooled"][lab] = {"trips": n, "avg_gross_pct": round(100 * g / n, 2), "avg_net_pct": round(100 * nt / n, 2)}
        print(f"  {lab:16s} trips {n:5d}  avg gross {100*g/n:+.2f}%  avg net {100*nt/n:+.2f}%  (fees+costs ~{100*(g-nt)/n:.2f}%/trip)")
    os.makedirs(os.path.join(BASE, "logs"), exist_ok=True)
    json.dump(out, open(os.path.join(BASE, "logs", "oos_diagnosis.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
