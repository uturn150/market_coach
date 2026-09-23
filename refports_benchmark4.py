"""Classics batch 2: ADXMomentum/AwesomeMacd (1h, 3y), AverageStrategy (4h, 3y), CofiBitStrategy/EMASkipPump (5m) and Low_BB (1m) on the minute data.
    python3 refports_benchmark4.py"""
import glob, json, os, time
from marketcoach import refports as rp, minute_data as md
from refports_benchmark import load as load_hist
from refports_benchmark2 import minute_bars, run
BASE = os.path.dirname(os.path.abspath(__file__))

def main():
    out = {}
    hist = {3600: load_hist(3600), 14400: load_hist(14400)}
    coins = [p for p in md.universe() if len(glob.glob(os.path.join(md.DIR, p, "*.bin.z"))) >= 3]
    for name, cls, src, sub in (("ADXMomentum (1h)", rp.ADXMomentum, 3600, None), ("AwesomeMacd (1h)", rp.AwesomeMacd, 3600, None), ("AverageStrategy (4h)", rp.AverageStrategy, 14400, None),
                                ("CofiBitStrategy (5m)", rp.CofiBitStrategy, "min", None), ("EMASkipPump (5m)", rp.EMASkipPump, "min", None), ("Low_BB (1m, 8 coins)", rp.LowBB, "min", 8)):
        if src == "min":
            cs = coins[:sub] if sub else coins
            bars = {p: minute_bars(p, cls.tf_minutes) for p in cs}
            n = min(len(x) for x in bars.values()); bars = {p: x[-n:] for p, x in bars.items()}; days = n * cls.tf_minutes / 1440
        else:
            bars = hist[src]; n = len(next(iter(bars.values()))); days = n * src / 86400
        hold = (sum(x[-1].close / x[0].close for x in bars.values()) / len(bars) - 1) * 100
        t0 = time.time(); a = run(cls, bars); b = run(cls, bars, fee=10)
        out[name] = {"ours": a, "typical": b, "hold": hold, "days": days}
        print(f"{name:26s} {len(bars)} coins x {days:.0f}d | @ our fees {a[0]:+8.1f}% dd {a[1]:6.1f}% {a[2]/days:5.2f}/day | @0.10% {b[0]:+8.1f}% dd {b[1]:6.1f}% | hold {hold:+.1f}%  [{time.time()-t0:.0f}s]", flush=True)
    json.dump(out, open(os.path.join(BASE, "logs", "refports_benchmark4.json"), "w"), indent=1)

if __name__ == "__main__":
    main()
