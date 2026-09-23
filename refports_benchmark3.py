"""Classic dip strategies (berlinguyinca folder): BbandRsi 1h (3y), Cluc / Combined 5m and BinHV45 1m on the minute data so far.
    python3 refports_benchmark3.py"""
import glob, json, os, time
from marketcoach import engine, refports as rp, minute_data as md
from refports_benchmark import load as load_hist
from refports_benchmark2 import minute_bars, run

BASE = os.path.dirname(os.path.abspath(__file__))


def main():
    out = {}
    bars = load_hist(3600); n = len(next(iter(bars.values()))); days = n / 24
    hold = (sum(b[-1].close / b[0].close for b in bars.values()) / len(bars) - 1) * 100
    a = run(rp.BbandRsi, bars); b = run(rp.BbandRsi, bars, fee=10)
    print(f"{'BbandRsi (1h)':26s} 24 coins x {days:.0f}d | @ our fees {a[0]:+7.1f}% dd {a[1]:6.1f}% {a[2]/days:5.2f}/day | @0.10% {b[0]:+7.1f}% dd {b[1]:6.1f}% | hold {hold:+.1f}%", flush=True)
    coins = [p for p in md.universe() if len(glob.glob(os.path.join(md.DIR, p, "*.bin.z"))) >= 3]
    for name, cls, sub in (("ClucMay72018 (5m)", rp.ClucMay72018, None), ("CombinedBinHAndCluc (5m)", rp.CombinedBinHAndCluc, None), ("BinHV45 (1m, 8 coins)", rp.BinHV45, 8)):
        cs = coins[:sub] if sub else coins
        bars = {p: minute_bars(p, cls.tf_minutes) for p in cs}
        n = min(len(x) for x in bars.values()); bars = {p: x[-n:] for p, x in bars.items()}
        days = n * cls.tf_minutes / 1440
        hold = (sum(x[-1].close / x[0].close for x in bars.values()) / len(bars) - 1) * 100
        t0 = time.time(); a = run(cls, bars); b = run(cls, bars, fee=10)
        print(f"{name:26s} {len(bars)} coins x {days:.0f}d | @ our fees {a[0]:+7.1f}% dd {a[1]:6.1f}% {a[2]/days:5.2f}/day | @0.10% {b[0]:+7.1f}% dd {b[1]:6.1f}% | hold {hold:+.1f}%  [{time.time()-t0:.0f}s]", flush=True)
        out[name] = {"ours": a, "typical": b, "hold": hold, "days": days}
    json.dump(out, open(os.path.join(BASE, "logs", "refports_benchmark3.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
