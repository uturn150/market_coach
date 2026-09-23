"""Chameleon: ONE bot that changes which strategies it runs as the market regime changes, using a mapping it
LEARNED from history (never hand-set).

Regime = the BTC classifier in regimes.py (TRENDING_UP / TRENDING_DOWN / SIDEWAYS / HIGH_VOLATILITY), read from the
PREVIOUS day's close (no lookahead). For each regime it learns, from days already lived through, which bots earned
the best risk-adjusted return in that regime (mean/stdev of daily return, needs >= MIN_DAYS such days), keeps the top K,
and holds them equal-weight while that regime lasts. If no bot has a positive score in a regime it sits in CASH.
The mapping is re-learned weekly from all data up to that day, so it keeps improving as history grows.
"""
import math

MIN_DAYS = 40


def learn_mapping(R, labels, upto, K=5):
    """R: {bot: [daily return]} aligned; labels: [regime label or None] aligned (label at day i is known at close of i);
    only days < upto are used, and a day's return is attributed to the PREVIOUS day's label. Returns {regime: [bots]}."""
    regs = {}
    for name, r in R.items():
        by = {}
        for d in range(2, upto):
            lab = labels[d - 1]
            if lab is None:
                continue
            by.setdefault(lab, []).append(r[d])
        for lab, v in by.items():
            if len(v) >= MIN_DAYS:
                m = sum(v) / len(v)
                sd = math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1)) or 1e-9
                regs.setdefault(lab, []).append((m / sd, name))
    out = {}
    for lab, lst in regs.items():
        best = [n for s, n in sorted(lst, reverse=True)[:K] if s > 0]
        out[lab] = best
    return out


def weights_for(mapping, label):
    bots = mapping.get(label, [])
    return {b: 1.0 / len(bots) for b in bots} if bots else {}
