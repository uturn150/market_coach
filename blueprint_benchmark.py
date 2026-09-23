"""Blueprint benchmark: public bot designs vs ours, same simulator, same real fees, same coins, same period.
Signal blueprints run on HOURLY bars (their world is 5-minute/1-hour candles); retail grid/DCA defaults on daily bars.
Each is shown at OUR fee tier (0.40/0.80) and at the exchange fee they usually assume (0.10/0.10).   python3 blueprint_benchmark.py"""
import json, os, time
from marketcoach import data, engine, market_sim as ms, bot_engine as be, community_bots as cb, market_filters as mf, regime
from marketcoach import blueprints as bp
from marketcoach.factory import UNIVERSES

BASE = os.path.dirname(os.path.abspath(__file__))


def pooled(strategy_fn, bars, allow=None, cash=1.0):
    tot, trips, wins = None, 0, 0
    for b in bars.values():
        st = strategy_fn()
        r = engine.run(st, b, cash=cash, allow_buy_ts=allow)
        tot = r.equity if tot is None else [x + y for x, y in zip(tot, r.equity)]
        sells = [f for f in r.fills if f.side == "sell"]
        trips += len(sells)
    peak, dd = tot[0], 0.0
    for v in tot:
        peak = max(peak, v); dd = min(dd, v / peak - 1)
    return (tot[-1] / tot[0] - 1) * 100, dd * 100, trips


def main():
    coins = [f"{c}-USD" for c in UNIVERSES["large"]]
    hourly = {p: data.fetch_coinbase_history(p, 3600, 26288, True, cache_tag="_deep") for p in coins}
    n = min(len(b) for b in hourly.values()); hourly = {p: b[-n:] for p, b in hourly.items()}
    days = n / 24
    print(f"HOURLY: {len(coins)} coins x {n} bars ({days:.0f} days, {time.strftime('%Y-%m', time.gmtime(hourly['BTC-USD'][0].ts))} -> now)\n")
    rows = []
    print(f"{'blueprint (hourly bars)':44s} {'@ our fees 0.40/0.80':>34s} | {'@ typical 0.10/0.10':>34s}")
    for name, cls in (("Freqtrade Strategy001 (EMA + Heikin-Ashi)", bp.FreqtradeStrategy001), ("Freqtrade Strategy002 (RSI+stoch+BB+hammer)", bp.FreqtradeStrategy002),
                      ("Freqtrade docs sample (RSI 30/70)", bp.FreqtradeSampleRSI)):
        a = pooled(lambda: cls(take_profit=0.03, stop=0.10), hourly)
        b = pooled(lambda: cls(fee_bps=10, take_profit=0.03, stop=0.10), hourly)
        rows.append({"name": name, "ours_fee": a, "their_fee": b})
        print(f"{name:44s} {a[0]:>+9.1f}% dd {a[1]:>6.1f}% trades/day {a[2]/days:>5.1f} | {b[0]:>+9.1f}% dd {b[1]:>6.1f}% trades/day {b[2]/days:>5.1f}", flush=True)
    # retail grid / DCA defaults vs ours on the SAME daily window
    dbars = {p: data.fetch_coinbase_history(p, 86400, 3300, True, cache_tag="_long")[-int(days):] for p in coins}
    nd = min(len(b) for b in dbars.values()); dbars = {p: b[-nd:] for p, b in dbars.items()}
    ts = [b.ts for b in dbars["BTC-USD"]]
    long_btc = ms.load_long()["BTC-USD"]
    f0, f1 = ms.filters_for({"BTC-USD": long_btc})
    ok = {"none": None, "F1": (lambda i, s=f1, t=ts: t[i] in s)}
    print(f"\nDAILY, same coins, same {nd} days (grid/DCA bots):")
    print(f"{'bot':52s} {'return':>8s} {'maxDD':>7s} {'trades/day':>10s}")
    for name, mk in (("RETAIL DCA default: 5 SO, 2% step, x1.5, 1.5% TP, no filter", lambda: cb.DCACycleBot(safety=5, dev=0.02, mult=1.5, tp=0.015, stop=None, market_ok=None)),
                     ("RETAIL grid default: 10 levels x 2%, no filter", lambda: cb.GridBot(levels=10, step=0.02, market_ok=None)),
                     ("RETAIL grid default: 10 levels x 1%, no filter", lambda: cb.GridBot(levels=10, step=0.01, market_ok=None)),
                     ("OURS DCA 5% steps x2, 3% TP, F1 + breaker", lambda: cb.Breaker(cb.DCACycleBot(safety=5, dev=0.05, mult=2.0, tp=0.03, stop=0.2, market_ok=ok["F1"]), 0.12)),
                     ("OURS grid 8 levels x 6%, F1 + breaker", lambda: cb.Breaker(cb.GridBot(levels=8, step=0.06, market_ok=ok["F1"]), 0.12))):
        r = be.run(mk(), dbars, cash=100.0)
        peak, dd = r.equity[0], 0.0
        for v in r.equity:
            peak = max(peak, v); dd = min(dd, v / peak - 1)
        print(f"{name:52s} {(r.equity[-1]/r.equity[0]-1)*100:>+7.1f}% {dd*100:>6.1f}% {len(r.trips)/nd:>10.2f}")
    hold = sum(b[-1].close / b[0].close for b in dbars.values()) / len(dbars) - 1
    print(f"{'buy & hold the same coins':52s} {hold*100:>+7.1f}%")
    json.dump(rows, open(os.path.join(BASE, "logs", "blueprint_benchmark.json"), "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
