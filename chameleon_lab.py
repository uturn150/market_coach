"""Chameleon replay: does a bot that switches strategies by learned regime beat the alternatives?
Weekly re-learning from PAST data only; 0.10% cost on weight turned over; 39-bot fleet, 2020-26 real history.
Tests: learned Chameleon (K=3,5,8), a placebo that routes on SHUFFLED regime labels (if the label information were
useless this would do as well), equal split, inverse-vol, and hindsight-static best K bots.   python3 chameleon_lab.py"""
import json, os, random, time
from marketcoach import market_sim as ms, chameleon as ch, regimes, meta_allocator as ma
from meta_lab import fleet_series, COST, WARM
from green_study import stats, week_key

BASE = os.path.dirname(os.path.abspath(__file__))


def run(ts, R, labels, weights_fn):
    n = len(ts); names = list(R)
    eq, cur, mapping = [1.0], {}, None
    turn = 0.0
    for i in range(1, n):
        new_week = week_key(ts[i - 1]) != week_key(ts[i - 2]) if i >= 2 else True
        w = None
        if i >= WARM:
            if new_week or mapping is None:
                mapping = weights_fn("learn", i)
            w = weights_fn("weights", i, mapping)
        if w is not None:
            tv = sum(abs(w.get(k, 0.0) - cur.get(k, 0.0)) for k in set(w) | set(cur))
            turn += tv; cur = w
            eq.append(eq[-1] * (1 - COST * tv) * (1 + sum(cur.get(k, 0.0) * R[k][i] for k in names)))
        else:
            eq.append(eq[-1])
    return eq, turn


def main():
    ts, R, fam, up = fleet_series()
    bars = ms.load_long()
    lab_map = regimes.label_map(bars["BTC-USD"])
    labels = [lab_map.get(t) for t in ts]
    print(f"{len(R)} bots, {len(ts)} days; regime days: " + str({l: labels.count(l) for l in set(labels) if l}))
    start = WARM + 5
    out = {}

    def report(name, eq, tv):
        ser = {t: e for t, e in zip(ts[start:], eq[start:])}
        s = stats(ser); y = ms.yearly(ser); yrs = len(ser) / 365
        cagr = (eq[-1] / eq[start]) ** (1 / yrs) - 1
        out[name] = {"stats": s, "yearly": y, "cagr": round(cagr * 100, 1), "turnover": round(tv / yrs, 1)}
        print(f"{name:34s} {s['total_return_pct']:>+8.1f}% {cagr*100:>+6.1f}% {s['max_dd_pct']:>6.1f}% {s['green_week_pct']:>7.1f}% {s['worst_week_pct']:>7.1f}% {y['worst_pct']:>+7.1f}% {y['green_year_pct']:>8.0f}%   " +
              " ".join(f"{k}:{v:+.0f}%" for k, v in sorted(y["calendar"].items())), flush=True)

    print(f"\n{'policy':34s} {'return':>9s} {'CAGR':>7s} {'maxDD':>6s} {'grn wk%':>7s} {'worst wk':>8s} {'worst yr':>8s} {'grn yrs':>8s}   calendar")
    for K in (3, 5, 8):
        def fn(mode, i, mapping=None, K=K):
            if mode == "learn":
                return ch.learn_mapping(R, labels, i - 1, K=K)
            lab = labels[i - 1]
            return ch.weights_for(mapping, lab)
        eq, tv = run(ts, R, labels, fn); report(f"Chameleon (learned, K={K})", eq, tv)
    for seed in range(3):                                          # placebo: same learning machinery, labels shuffled in time
        idx = [l for l in labels if l]; rng = random.Random(seed); sh = idx[:]; rng.shuffle(sh)
        it = iter(sh); fake = [next(it) if l else None for l in labels]
        def fn(mode, i, mapping=None, fake=fake):
            if mode == "learn":
                return ch.learn_mapping(R, fake, i - 1, K=5)
            return ch.weights_for(mapping, fake[i - 1])
        eq, tv = run(ts, R, fake, fn); report(f"PLACEBO shuffled labels #{seed}", eq, tv)
    for m in ("equal", "invvol", "regime"):
        def fn(mode, i, mapping=None, m=m):
            if mode == "learn": return None
            hist = {k: R[k][:i] for k in R}
            return ma.METHODS[m](hist, btc_up=up[i - 1], families=fam)
        eq, tv = run(ts, R, labels, fn); report(f"{m} (meta-allocator)", eq, tv)
    # what did it learn (mapping from ALL data) — for reading, not for trading
    full = ch.learn_mapping(R, labels, len(ts), K=5)
    print("\nMapping learned from all 6.7 years (top bots per regime):")
    for lab, bots in full.items():
        print(f"  {lab:16s} -> {', '.join(bots) if bots else 'CASH'}")
    json.dump({"results": out, "mapping": full}, open(os.path.join(BASE, "logs", "chameleon_lab.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
