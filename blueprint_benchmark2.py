"""More sophisticated public blueprints (Jesse IFR2 = RSI2 + Ichimoku + trend filter; Jesse Dual Thrust; SuperTrend+RSI+ADX combo)
vs ours, DAILY bars, 9 coins 2020-2026, at our real fees and at 0.10%, plus a held-out split.   python3 blueprint_benchmark2.py"""
import json, os
from marketcoach import market_sim as ms, engine, blueprints as bp, kraken_fees, metrics, community_bots as cb, bot_engine as be
from marketcoach.strategy import Strategy
from marketcoach.learner import CHAMPIONS
from blueprint_benchmark import pooled
BASE = os.path.dirname(os.path.abspath(__file__))

def run(fn, bars, allow=None):
    tot = None
    for b in bars.values():
        r = engine.run(fn(), b, cash=1.0, allow_buy_ts=allow); tot = r.equity if tot is None else [x + y for x, y in zip(tot, r.equity)]
    n = len(tot); cut = int(n * 0.6)
    peak, dd = tot[0], 0.0
    for v in tot: peak = max(peak, v); dd = min(dd, v / peak - 1)
    return (tot[-1] / tot[0] - 1) * 100, (tot[-1] / tot[cut] - 1) * 100, dd * 100

def main():
    bars = ms.load_long(); f0, f1 = ms.filters_for(bars)
    days = len(next(iter(bars.values())))
    print(f"DAILY {len(bars)} coins x {days} days (2020-2026). Return over all history / held-out last 40% / max drawdown\n")
    print(f"{'blueprint':46s} {'@ our fees 0.40/0.80':>32s} | {'@ typical 0.10':>32s}")
    for name, mk in (("Jesse IFR2 (RSI2 + Ichimoku + trend)", lambda fee=None: bp.JesseIFR2(fee_bps=fee, take_profit=None, stop=0.15)),
                     ("Jesse Dual Thrust (long side)", lambda fee=None: bp.JesseDualThrust(fee_bps=fee, take_profit=None, stop=0.15)),
                     ("SuperTrend + RSI>50 + ADX>20 (Pine combo)", lambda fee=None: bp.SupertrendRSIADX(fee_bps=fee, take_profit=None, stop=0.15)),
                     ("Freqtrade Strategy001 on DAILY bars", lambda fee=None: bp.FreqtradeStrategy001(fee_bps=fee, take_profit=None, stop=0.15))):
        a = run(lambda: mk(None), bars); b = run(lambda: mk(10), bars)
        print(f"{name:46s} {a[0]:>+8.1f}% {a[1]:>+7.1f}% dd {a[2]:>6.1f}% | {b[0]:>+8.1f}% {b[1]:>+7.1f}% dd {b[2]:>6.1f}%", flush=True)
    print("\nOURS on the same data (real fees), for comparison:")
    for acct in ("krakenturtle", "krakenmulti", "krakenkeltner"):
        spec, _ = kraken_fees.apply_to_spec(json.load(open(f"strategies/{CHAMPIONS[acct]}.json")))
        a = run(lambda: Strategy(spec), bars, f1)
        print(f"{acct + ' + F1':46s} {a[0]:>+8.1f}% {a[1]:>+7.1f}% dd {a[2]:>6.1f}%")

if __name__ == "__main__":
    main()
