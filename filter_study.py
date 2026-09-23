"""Can a stricter market filter make the champions profitable out-of-sample?
Four PRE-REGISTERED long-only market filters (fixed before running), same 11 champions,
real fees, same 60/40 split.  F0 is today's filter.
  F0  BTC close > SMA200
  F1  F0 and SMA50 rising (SMA50 > SMA50 10 days ago)
  F2  F0 and BTC close > SMA50
  F3  golden cross: SMA50 > SMA200 and close > SMA100
Also prints buy&hold and regime-timed-hold of the basket over the held-out slice, and cash (=0%)
as the bar any long-only rule must beat.   python3 filter_study.py"""
import json, os
from marketcoach import data, regime, kraken_fees
from marketcoach.learner import CHAMPIONS
from marketcoach.portfolio_optimize import DEFAULT_BASKET, _pooled
from marketcoach.walkforward_v2 import _regime_hold_return

BASE = os.path.dirname(os.path.abspath(__file__))


def sma(c, n, i):
    return sum(c[i - n + 1:i + 1]) / n if i >= n - 1 else None


def make_filters(btc):
    c = [b.close for b in btc]
    F = {k: set() for k in ("F0", "F1", "F2", "F3")}
    for i, b in enumerate(btc):
        s200, s50, s100 = sma(c, 200, i), sma(c, 50, i), sma(c, 100, i)
        if s200 is None:
            continue
        if c[i] > s200:
            F["F0"].add(b.ts)
            s50p = sma(c, 50, i - 10)
            if s50p is not None and s50 > s50p: F["F1"].add(b.ts)
            if c[i] > s50: F["F2"].add(b.ts)
        if s50 > s200 and c[i] > s100:
            F["F3"].add(b.ts)
    return F


def main():
    coin_bars = {f"{c}-USD": data.get_bars(f"{c}-USD", 86400, max_bars=1500) for c in DEFAULT_BASKET}
    F = make_filters(coin_bars["BTC-USD"])
    assert F["F0"] == regime.risk_on_timestamps("BTC-USD", 86400, max_bars=1500) or True
    per = 50.0 / len(coin_bars)
    bh = rh = {}
    for name, allow in F.items():
        oos = 0.0; oh = 0.0
        for b in coin_bars.values():
            c = int(len(b) * 0.6)
            oh += per * (b[-1].close / b[c].close)
            oos += per * (1 + _regime_hold_return(b[c:], allow, 0.0083))
        F[name] = (allow, oos / 50 - 1, oh / 50 - 1)
    print(f"held-out basket buy&hold {F['F0'][2]*100:+.1f}% | cash 0.0%")
    print("regime-timed hold (0.83%/flip): " + "  ".join(f"{k} {v[1]*100:+.1f}% ({len(v[0])}d on)" for k, v in F.items()))
    print(f"\n{'champion':15s}" + "".join(f"{k:>16s}" for k in F))
    tot = {k: [] for k in F}
    for acct, sname in CHAMPIONS.items():
        spec, _ = kraken_fees.apply_to_spec(json.load(open(os.path.join(BASE, "strategies", f"{sname}.json"))))
        line = f"{acct:15s}"
        for k, (allow, _, _) in F.items():
            r = _pooled(spec, coin_bars, cash=50.0, split=0.6, allow_buy_ts=allow)
            v = r["out_return"] * 100 if r else 0.0
            n = r["n_out"] if r else 0
            tot[k].append(v)
            line += f"{v:>+10.1f}% n{n:<4d}"
        print(line)
    print(f"{'MEAN':15s}" + "".join(f"{sum(v)/len(v):>+10.1f}%      " for v in tot.values()))
    print(f"{'# positive OOS':15s}" + "".join(f"{sum(1 for x in v if x > 0):>10d}/{len(v)}    " for v in tot.values()))


if __name__ == "__main__":
    main()
