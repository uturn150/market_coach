"""Remaining classic ports: TechnicalExample/ASDTSRockwell/MultiRSI/UniversalMACD/PowerTower/Diamond (5m), TDSequential (1h), ReinforcedSmoothScalp (1m, 6 coins)."""
import glob, json, os, time
from marketcoach import engine, refports as rp, minute_data as md
from refports_benchmark import load as load_hist
from refports_benchmark2 import minute_bars
from refports_benchmark6 import run_ts
BASE = os.path.dirname(os.path.abspath(__file__))
coins = [p for p in md.universe() if len(glob.glob(os.path.join(md.DIR, p, "*.bin.z"))) >= 3]
out = {}
def show(name, bars, cls, days_per_bar):
    n = len(next(iter(bars.values()))); days = n * days_per_bar
    hold = (sum(x[-1].close / x[0].close for x in bars.values()) / len(bars) - 1) * 100
    t0 = time.time(); a = run_ts(cls, bars); b = run_ts(cls, bars, 10)
    out[name] = {"ours": a, "typical": b, "hold": hold}
    print(f"{name:30s} {len(bars)} coins x {days:.0f}d | @ our fees {a[0]:+8.1f}% dd {a[1]:6.1f}% {a[2]/days:5.2f}/day | @0.10% {b[0]:+8.1f}% dd {b[1]:6.1f}% | hold {hold:+.1f}%  [{time.time()-t0:.0f}s]", flush=True)
b5 = {p: minute_bars(p, 5) for p in coins}; n = min(len(x) for x in b5.values()); b5 = {p: x[-n:] for p, x in b5.items()}
for name, cls in (("TechnicalExample (5m)", rp.TechnicalExampleStrategy), ("ASDTSRockwell (5m)", rp.ASDTSRockwellTrading), ("MultiRSI (5m)", rp.MultiRSI), ("UniversalMACD (5m)", rp.UniversalMACD),
                  ("PowerTower as written (5m)", rp.PowerTower), ("Diamond as written (5m)", rp.Diamond)):
    show(name, b5, cls, 5 / 1440)
show("TDSequential (1h)", load_hist(3600), rp.TDSequentialStrategy, 1 / 24)
b1 = {p: minute_bars(p, 1) for p in coins[:6]}; n = min(len(x) for x in b1.values()); b1 = {p: x[-n:] for p, x in b1.items()}
show("ReinforcedSmoothScalp (1m,6)", b1, rp.ReinforcedSmoothScalp, 1 / 1440)
json.dump(out, open(os.path.join(BASE, "logs", "refports_benchmark8.json"), "w"), indent=1)
