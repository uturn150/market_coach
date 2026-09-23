"""Utility strategies: HourBased (1h), hlhb (4h), Strategy001_custom_exit (5m), CustomStoplossWithPSAR (1h).  python3 refports_benchmark10.py"""
import glob, json, os, time
from marketcoach import refports as rp, minute_data as md
from refports_benchmark import load as load_hist
from refports_benchmark2 import minute_bars
from refports_benchmark6 import run_ts
BASE = os.path.dirname(os.path.abspath(__file__)); out = {}
def show(name, bars, cls, dpb):
    n = len(next(iter(bars.values()))); days = n * dpb
    hold = (sum(x[-1].close / x[0].close for x in bars.values()) / len(bars) - 1) * 100
    t0 = time.time(); a = run_ts(cls, bars); b = run_ts(cls, bars, 10)
    out[name] = {"ours": a, "typical": b, "hold": hold}
    print(f"{name:30s} {len(bars)} coins x {days:.0f}d | @ our fees {a[0]:+8.1f}% dd {a[1]:6.1f}% {a[2]/days:5.2f}/day | @0.10% {b[0]:+8.1f}% dd {b[1]:6.1f}% | hold {hold:+.1f}%  [{time.time()-t0:.0f}s]", flush=True)
h1 = load_hist(3600)
show("HourBased (1h)", h1, rp.HourBased, 1 / 24); show("PsarStop (1h)", h1, rp.PsarStop, 1 / 24)
show("hlhb long (4h)", load_hist(14400), rp.Hlhb, 4 / 24)
coins = [p for p in md.universe() if len(glob.glob(os.path.join(md.DIR, p, "*.bin.z"))) >= 3]
bars = {p: minute_bars(p, 5) for p in coins}; n = min(len(x) for x in bars.values()); bars = {p: x[-n:] for p, x in bars.items()}
show("Strategy001_custom_exit (5m)", bars, rp.Strategy001CustomExit, 5 / 1440)
json.dump(out, open(os.path.join(BASE, "logs", "refports_benchmark10.json"), "w"), indent=1)
