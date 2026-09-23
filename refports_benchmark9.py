"""Last ports: SmoothOperator (5m), VolatilitySystem/FSample/FOtt/FSupertrend (1h), FReinforced (5m), Hummingbot bollinger_v1 / macd_bb_v1 / supertrend_v1 (3m)."""
import glob, json, os, time
from marketcoach import refports as rp, minute_data as md
from refports_benchmark import load as load_hist
from refports_benchmark2 import minute_bars
from refports_benchmark6 import run_ts
BASE = os.path.dirname(os.path.abspath(__file__))
coins = [p for p in md.universe() if len(glob.glob(os.path.join(md.DIR, p, "*.bin.z"))) >= 3]
out = {}
def show(name, bars, cls, dpb):
    n = len(next(iter(bars.values()))); days = n * dpb
    hold = (sum(x[-1].close / x[0].close for x in bars.values()) / len(bars) - 1) * 100
    t0 = time.time(); a = run_ts(cls, bars); b = run_ts(cls, bars, 10)
    out[name] = {"ours": a, "typical": b, "hold": hold}
    print(f"{name:30s} {len(bars)} coins x {days:.0f}d | @ our fees {a[0]:+8.1f}% dd {a[1]:6.1f}% {a[2]/days:5.2f}/day | @0.10% {b[0]:+8.1f}% dd {b[1]:6.1f}% | hold {hold:+.1f}%  [{time.time()-t0:.0f}s]", flush=True)
h1 = load_hist(3600)
for name, cls in (("VolatilitySystem long (1h)", rp.VolatilitySystem), ("FSample long (1h)", rp.FSampleStrategy), ("FOtt long (1h)", rp.FOttStrategy), ("FSupertrend long (1h)", rp.FSupertrendStrategy)):
    show(name, h1, cls, 1 / 24)
for name, cls, tf in (("SmoothOperator (5m)", rp.SmoothOperator, 5), ("FReinforced long (5m)", rp.FReinforcedStrategy, 5), ("HB bollinger_v1 (3m)", rp.HBBollingerV1, 3), ("HB macd_bb_v1 (3m)", rp.HBMacdBBV1, 3), ("HB supertrend_v1 (3m)", rp.HBSupertrendV1, 3)):
    bars = {p: minute_bars(p, tf) for p in coins}; n = min(len(x) for x in bars.values()); bars = {p: x[-n:] for p, x in bars.items()}
    show(name, bars, cls, tf / 1440)
json.dump(out, open(os.path.join(BASE, "logs", "refports_benchmark9.json"), "w"), indent=1)
