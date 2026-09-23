"""Faithful TrendRiderStrategy port on hourly data, 24 coins x 3 years.   python3 trendrider_run.py"""
import json, os
from marketcoach import data, engine, refports as rp, indicators as I
from marketcoach.portfolio_optimize import DEFAULT_BASKET

BASE = os.path.dirname(os.path.abspath(__file__))

def main():
    coins = [f"{c}-USD" for c in DEFAULT_BASKET]
    bars = {p: data.fetch_coinbase_history(p, 3600, 26288, True, cache_tag="_deep") for p in coins}
    n = min(len(b) for b in bars.values()); bars = {p: b[-n:] for p, b in bars.items()}
    ts = [b.ts for b in bars["BTC-USD"]]
    btc_rsi = I.rsi([b.close for b in bars["BTC-USD"]], 14)
    btc_prev = [None] + btc_rsi[:-1]                     # merge_informative_pair on the same timeframe: the previous candle
    days = n / 24
    hold = (sum(b[-1].close / b[0].close for b in bars.values()) / len(bars) - 1) * 100
    res = {}
    for label, fee in (("our fees 0.40/0.80", None), ("typical 0.10", 10)):
        tot, trips, wins = None, 0, 0
        for p, b in bars.items():
            st = rp.TrendRider(fee_bps=fee); st.ts, st.btc_rsi = ts, btc_prev
            r = engine.run(st, b, cash=1.0, ft=st.ft())
            tot = r.equity if tot is None else [x + y for x, y in zip(tot, r.equity)]
            trips += sum(1 for f in r.fills if f.side == "sell")
        peak, dd = tot[0], 0.0
        for v in tot: peak = max(peak, v); dd = min(dd, v / peak - 1)
        res[label] = ((tot[-1] / tot[0] - 1) * 100, dd * 100, trips / days)
        print(f"TrendRider @ {label:20s}: {res[label][0]:+8.1f}%  dd {res[label][1]:6.1f}%  {res[label][2]:.2f} trades/day  (hold {hold:+.1f}%)", flush=True)
    json.dump(res, open(os.path.join(BASE, "logs", "trendrider_run.json"), "w"), indent=1)

if __name__ == "__main__":
    main()
