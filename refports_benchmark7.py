"""Futures-folder strategies, LONG side only: FAdxSma (1h, 3y), TrendFollowing (5m).   python3 refports_benchmark7.py"""
import glob, os
from marketcoach import refports as rp, minute_data as md
from refports_benchmark import load as load_hist
from refports_benchmark2 import minute_bars, run
bars = load_hist(3600); days = len(next(iter(bars.values()))) / 24
hold = (sum(x[-1].close / x[0].close for x in bars.values()) / len(bars) - 1) * 100
a, b = run(rp.FAdxSmaStrategy, bars), run(rp.FAdxSmaStrategy, bars, 10)
print(f"{'FAdxSma long-only (1h)':28s} 24 coins x {days:.0f}d | @ our fees {a[0]:+8.1f}% dd {a[1]:6.1f}% {a[2]/days:5.2f}/day | @0.10% {b[0]:+8.1f}% dd {b[1]:6.1f}% | hold {hold:+.1f}%", flush=True)
coins = [p for p in md.universe() if len(glob.glob(os.path.join(md.DIR, p, "*.bin.z"))) >= 3]
bars = {p: minute_bars(p, 5) for p in coins}; n = min(len(x) for x in bars.values()); bars = {p: x[-n:] for p, x in bars.items()}; days = n * 5 / 1440
hold = (sum(x[-1].close / x[0].close for x in bars.values()) / len(bars) - 1) * 100
a, b = run(rp.TrendFollowingStrategy, bars), run(rp.TrendFollowingStrategy, bars, 10)
print(f"{'TrendFollowing long-only (5m)':28s} {len(bars)} coins x {days:.0f}d | @ our fees {a[0]:+8.1f}% dd {a[1]:6.1f}% {a[2]/days:5.2f}/day | @0.10% {b[0]:+8.1f}% dd {b[1]:6.1f}% | hold {hold:+.1f}%", flush=True)
