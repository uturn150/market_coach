"""Faithful Freqtrade ports vs buy&hold, at our real fees and at 0.10%.   python3 refports_benchmark.py"""
import json, os, time
from marketcoach import data, engine, refports as rp
from marketcoach.portfolio_optimize import DEFAULT_BASKET

BASE = os.path.dirname(os.path.abspath(__file__))


def load(gran):
    coins = [f"{c}-USD" for c in DEFAULT_BASKET]
    if gran == 3600:
        bars = {p: data.fetch_coinbase_history(p, 3600, 26288, True, cache_tag="_deep") for p in coins}
    else:
        bars = {p: data.get_bars(p, granularity=14400, max_bars=6570) for p in coins}
    n = min(len(b) for b in bars.values())
    return {p: b[-n:] for p, b in bars.items()}


def run(cls, bars, fee=None):
    tot, trips, wins = None, 0, 0
    for b in bars.values():
        st = cls(fee_bps=fee)
        r = engine.run(st, b, cash=1.0, ft=st.ft())
        tot = r.equity if tot is None else [x + y for x, y in zip(tot, r.equity)]
        sells = [f for f in r.fills if f.side == "sell"]; trips += len(sells)
    peak, dd = tot[0], 0.0
    for v in tot: peak = max(peak, v); dd = min(dd, v / peak - 1)
    return (tot[-1] / tot[0] - 1) * 100, dd * 100, trips


def main():
    out = {}
    for name, cls, gran in (("Heracles (4h)", rp.Heracles, 14400), ("MultiMa (4h)", rp.MultiMa, 14400), ("Supertrend (1h)", rp.Supertrend, 3600)):
        bars = load(gran); n = len(next(iter(bars.values()))); days = n * gran / 86400
        hold = (sum(b[-1].close / b[0].close for b in bars.values()) / len(bars) - 1) * 100
        a = run(cls, bars); b = run(cls, bars, fee=10)
        out[name] = {"ours": a, "typical": b, "hold": hold}
        print(f"{name:18s} {len(bars)} coins x {days:.0f} days | @ our fees: {a[0]:+8.1f}% dd {a[1]:6.1f}% {a[2]/days:4.2f} trades/day | @0.10%: {b[0]:+8.1f}% dd {b[1]:6.1f}% | hold {hold:+.1f}%", flush=True)
    json.dump(out, open(os.path.join(BASE, "logs", "refports_benchmark.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
