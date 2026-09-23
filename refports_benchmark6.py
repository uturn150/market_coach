"""CMCWinner (15m, minute data) and ReinforcedAverageStrategy (4h, 3y).   python3 refports_benchmark6.py"""
import glob, json, os, time
from marketcoach import engine, refports as rp, minute_data as md
from refports_benchmark import load as load_hist
from refports_benchmark2 import minute_bars
BASE = os.path.dirname(os.path.abspath(__file__))

def run_ts(cls, bars, fee=None):
    tot, trips = None, 0
    for b in bars.values():
        st = cls(fee_bps=fee); st.ts = [x.ts for x in b]
        r = engine.run(st, b, cash=1.0, ft=st.ft())
        tot = r.equity if tot is None else [x + y for x, y in zip(tot, r.equity)]
        trips += sum(1 for f in r.fills if f.side == "sell")
    peak, dd = tot[0], 0.0
    for v in tot: peak = max(peak, v); dd = min(dd, v / peak - 1)
    return (tot[-1] / tot[0] - 1) * 100, dd * 100, trips

def main():
    out = {}
    coins = [p for p in md.universe() if len(glob.glob(os.path.join(md.DIR, p, "*.bin.z"))) >= 3]
    bars = {p: minute_bars(p, 15) for p in coins}; n = min(len(x) for x in bars.values()); bars = {p: x[-n:] for p, x in bars.items()}; days = n * 15 / 1440
    hold = (sum(x[-1].close / x[0].close for x in bars.values()) / len(bars) - 1) * 100
    a = run_ts(rp.CMCWinner, bars); b = run_ts(rp.CMCWinner, bars, 10)
    print(f"{'CMCWinner (15m)':28s} {len(bars)} coins x {days:.0f}d | @ our fees {a[0]:+8.1f}% dd {a[1]:6.1f}% {a[2]/days:5.2f}/day | @0.10% {b[0]:+8.1f}% dd {b[1]:6.1f}% | hold {hold:+.1f}%", flush=True)
    bars = load_hist(14400); n = len(next(iter(bars.values()))); days = n * 4 / 24
    hold = (sum(x[-1].close / x[0].close for x in bars.values()) / len(bars) - 1) * 100
    a = run_ts(rp.ReinforcedAverageStrategy, bars); b = run_ts(rp.ReinforcedAverageStrategy, bars, 10)
    print(f"{'ReinforcedAverage (4h)':28s} {len(bars)} coins x {days:.0f}d | @ our fees {a[0]:+8.1f}% dd {a[1]:6.1f}% {a[2]/days:5.2f}/day | @0.10% {b[0]:+8.1f}% dd {b[1]:6.1f}% | hold {hold:+.1f}%", flush=True)

if __name__ == "__main__":
    main()
