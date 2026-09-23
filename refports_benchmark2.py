"""Freqtrade batch 2 (5m / 15m / 4h) on the minute data downloaded so far (recent months) + 4h history.   python3 refports_benchmark2.py"""
import glob, json, os, time
from marketcoach import data, engine, refports as rp, minute_data as md
from marketcoach.data import Bar
from refports_benchmark import run as run4h, load as load_hist

BASE = os.path.dirname(os.path.abspath(__file__))


def minute_bars(product, minutes):
    rows = []
    for f in sorted(glob.glob(os.path.join(md.DIR, product, "*.bin.z"))):
        y, m = int(os.path.basename(f)[:4]), int(os.path.basename(f)[5:7])
        rows += md.load_month(product, y, m)
    out, cur, key = [], None, None
    for t, o, h, l, c, v in rows:
        k = t - t % (minutes * 60)
        if k != key:
            if cur: out.append(Bar(*cur))
            cur, key = [k, o, h, l, c, v], k
        else:
            cur[2] = max(cur[2], h); cur[3] = min(cur[3], l); cur[4] = c; cur[5] += v
    if cur: out.append(Bar(*cur))
    return out


def run(cls, bars, fee=None):
    tot, trips = None, 0
    for b in bars.values():
        st = cls(fee_bps=fee); r = engine.run(st, b, cash=1.0, ft=st.ft())
        tot = r.equity if tot is None else [x + y for x, y in zip(tot, r.equity)]
        trips += sum(1 for f in r.fills if f.side == "sell")
    peak, dd = tot[0], 0.0
    for v in tot: peak = max(peak, v); dd = min(dd, v / peak - 1)
    return (tot[-1] / tot[0] - 1) * 100, dd * 100, trips


def main():
    coins = [p for p in md.universe() if os.path.isdir(os.path.join(md.DIR, p)) and len(glob.glob(os.path.join(md.DIR, p, "*.bin.z"))) >= 3]
    print(f"{len(coins)} coins with >=3 months of 1-minute data\n")
    out = {}
    for name, cls in (("Strategy004 (5m)", rp.Strategy004), ("Strategy005 (5m)", rp.Strategy005), ("Bandtastic (15m)", rp.Bandtastic), ("SwingHighToSky (15m)", rp.SwingHighToSky)):
        tf = cls.tf_minutes
        bars = {p: minute_bars(p, tf) for p in coins}
        n = min(len(b) for b in bars.values()); bars = {p: b[-n:] for p, b in bars.items()}
        days = n * tf / 1440
        hold = (sum(b[-1].close / b[0].close for b in bars.values()) / len(bars) - 1) * 100
        a = run(cls, bars); b = run(cls, bars, fee=10)
        out[name] = {"days": days, "ours": a, "typical": b, "hold": hold}
        print(f"{name:22s} {len(bars)} coins x {days:.0f} days | @ our fees: {a[0]:+7.1f}% dd {a[1]:6.1f}% {a[2]/days:5.2f} trades/day | @0.10%: {b[0]:+7.1f}% dd {b[1]:6.1f}% | hold {hold:+.1f}%", flush=True)
    bars = load_hist(14400); n = len(next(iter(bars.values()))); days = n * 4 / 24
    hold = (sum(b[-1].close / b[0].close for b in bars.values()) / len(bars) - 1) * 100
    a = run4h(rp.MabStra, bars); b = run4h(rp.MabStra, bars, fee=10)
    print(f"{'mabStra (4h)':22s} 24 coins x {days:.0f} days | @ our fees: {a[0]:+7.1f}% dd {a[1]:6.1f}% {a[2]/days:5.2f} trades/day | @0.10%: {b[0]:+7.1f}% dd {b[1]:6.1f}% | hold {hold:+.1f}%")
    out["mabStra (4h)"] = {"ours": a, "typical": b, "hold": hold}
    json.dump(out, open(os.path.join(BASE, "logs", "refports_benchmark2.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
