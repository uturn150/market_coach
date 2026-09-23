"""Walk-forward test: can a model trained on PAST trades pick which future trades are worth taking?
    python3 ml_meta_study.py"""
import json, os, random, time
from marketcoach import data, engine, kraken_fees, regime, market_filters as mf
from marketcoach.learner import CHAMPIONS
from marketcoach.portfolio_optimize import DEFAULT_BASKET
from marketcoach.strategy import Strategy
from marketcoach.ml_meta import coin_features, Logistic, Stumps

BASE = os.path.dirname(os.path.abspath(__file__))
TEST_DAYS, MIN_TRAIN = 90, 250


def build_samples():
    bars = {f"{c}-USD": data.get_bars(f"{c}-USD", 86400, max_bars=1500) for c in DEFAULT_BASKET}
    btc = bars["BTC-USD"]
    f0 = regime.risk_on_timestamps("BTC-USD", 86400, max_bars=1500)
    f1 = f0 & mf.f1_timestamps(btc)
    feats = {p: coin_features(b, btc, f1) for p, b in bars.items()}
    samples = []          # (entry_ts, exit_ts, x, net, strat)
    for acct, sname in CHAMPIONS.items():
        spec, _ = kraken_fees.apply_to_spec(json.load(open(os.path.join(BASE, "strategies", f"{sname}.json"))))
        for p, b in bars.items():
            st = Strategy(spec); rate = st.side_rate
            res = engine.run(st, b, cash=2.0, allow_buy_ts=f0)
            idx = {bb.ts: i for i, bb in enumerate(b)}
            lots = []
            for f in res.fills:
                if f.side == "buy":
                    lots.append([f.units, f.price, f.ts])
                else:
                    rem = f.units
                    while rem > 1e-12 and lots:
                        l = lots[0]; take = min(l[0], rem)
                        i = idx[l[2]] - 1                     # signal bar = the bar before the fill
                        x = feats[p][i] if i >= 0 else None
                        if x is not None:
                            net = (f.price * (1 - rate)) / (l[1] * (1 + rate)) - 1
                            samples.append((l[2], f.ts, x, net, acct))
                        l[0] -= take; rem -= take
                        if l[0] <= 1e-12: lots.pop(0)
    samples.sort(key=lambda s: s[0])
    return samples


def walk_forward(samples, model_cls, thr, seed=0):
    t0, t1 = samples[0][0], samples[-1][0]
    allw, kept, plac = [], [], []
    rng = random.Random(seed)
    w = t0 + MIN_TRAIN * 86400
    while w < t1:
        train = [s for s in samples if s[1] < w]                    # trades already CLOSED before the window
        test = [s for s in samples if w <= s[0] < w + TEST_DAYS * 86400]
        if len(train) >= 150 and test:
            m = model_cls().fit([s[2] for s in train], [1 if s[3] > 0 else 0 for s in train])
            keep = [m.predict(s[2]) >= thr for s in test]
            share = sum(keep) / len(test)
            for s, k in zip(test, keep):
                allw.append((s[0], s[3]))
                if k: kept.append((s[0], s[3]))
                if rng.random() < share: plac.append((s[0], s[3]))
        w += TEST_DAYS * 86400
    return allw, kept, plac


def summ(pairs, since=None):
    v = [n for t, n in pairs if since is None or t >= since]
    if not v: return "n=0"
    srt = sorted(v); med = srt[len(srt) // 2]
    w = sum(x for x in v if x > 0); l = -sum(x for x in v if x <= 0)
    trim = srt[int(len(srt) * .05): int(len(srt) * .95)] or srt        # 5% trimmed mean: outlier-robust
    return (f"n={len(v):4d} win {100*sum(1 for x in v if x>0)/len(v):4.1f}% median {100*med:+6.2f}% "
            f"trimmed-avg {100*sum(trim)/len(trim):+6.2f}% PF {w/l if l else 99:5.2f}")


def main():
    samples = build_samples()
    print(f"{len(samples)} historical trades ({time.strftime('%Y-%m', time.gmtime(samples[0][0]))} -> {time.strftime('%Y-%m', time.gmtime(samples[-1][0]))})", flush=True)
    bear = time.mktime((2025, 1, 28, 0, 0, 0, 0, 0, 0))
    for name, cls in (("logistic", Logistic), ("boosted stumps", lambda: Stumps(rounds=30, bins=8))):
        for thr in (0.45, 0.50, 0.55):
            allw, kept, plac = walk_forward(samples, cls, thr)
            print(f"{name} thr {thr:.2f}   [all forward trades | model-kept | random same-share]", flush=True)
            for lab, since in (("2023-11+ (all OOS)", None), ("2025-01-28+ (held-out bear)", bear)):
                print(f"   {lab:28s} ALL   {summ(allw, since)}\n   {'':28s} MODEL {summ(kept, since)}\n   {'':28s} RAND  {summ(plac, since)}", flush=True)


if __name__ == "__main__":
    main()
