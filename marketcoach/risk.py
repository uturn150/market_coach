"""Portfolio risk tools: volatility-based sizing and correlation-aware selection.

Two things equal-weight-and-enter-everything ignores:
  1. VOLATILITY — a wild coin gets the same dollar allocation as a calm one, so it
     dominates actual risk without anyone deciding that on purpose.
  2. CORRELATION — crypto assets move together in a crash. Holding 12 "different"
     coins that are 80% correlated isn't diversification, it's one bet wearing 12
     costumes. Real diversification means avoiding redundant, correlated bets.

Pure stdlib, no numpy — every function works on plain lists/dicts of floats.
"""
import math


def daily_returns(bars):
    """Simple returns between consecutive closes."""
    closes = [b.close for b in bars]
    return [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes)) if closes[i - 1]]


def realized_vol(bars, n=20):
    """Stdev of the last n daily returns. None if there isn't enough data yet."""
    rets = daily_returns(bars)[-n:]
    if len(rets) < max(5, n // 2):
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / max(1, len(rets) - 1)
    return math.sqrt(var)


def vol_target_weights(coin_vols, min_mult=0.3, max_mult=1.0):
    """Inverse-volatility size multipliers, relative to the group's average vol.
    Default range (0.3-1.0) only ever SCALES DOWN wilder-than-average coins — it
    never scales a calm coin's allocation above its normal share, so no cross-sleeve
    cash movement is needed: unused allocation for a wild coin just sits as cash,
    a conservative, easy-to-reason-about form of risk reduction. Coins with unknown
    vol (not enough history yet) get the neutral multiplier 1.0."""
    known = {c: v for c, v in coin_vols.items() if v and v > 0}
    if not known:
        return {c: 1.0 for c in coin_vols}
    avg_vol = sum(known.values()) / len(known)
    weights = {}
    for c, v in coin_vols.items():
        if not v or v <= 0:
            weights[c] = 1.0
            continue
        raw = avg_vol / v  # calmer than average -> >1 (clipped to max_mult), wilder -> <1
        weights[c] = max(min_mult, min(max_mult, raw))
    return weights


def correlation(a, b):
    """Pearson correlation over the overlapping, most-recent tail of two return
    series. None if there's too little overlap to mean anything."""
    n = min(len(a), len(b))
    if n < 10:
        return None
    a, b = a[-n:], b[-n:]
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    if va <= 0 or vb <= 0:
        return None
    return cov / math.sqrt(va * vb)


def correlation_size_multiplier(max_corr, threshold=0.75, floor=0.3):
    """1.0 below `threshold`; shrinks linearly toward `floor` as max_corr climbs
    to 1.0. Used to scale DOWN a new position's size when it's highly correlated
    with something already held — reduces concentrated, redundant risk without
    an outright block (contrast with the funnel's hard skip, appropriate where a
    sleeve can reasonably hold partial correlated exposure instead of none)."""
    if max_corr <= threshold:
        return 1.0
    span = max(1e-9, 1.0 - threshold)
    return max(floor, 1.0 - (max_corr - threshold) / span * (1.0 - floor))


def max_correlation_to_held(candidate_returns, held_returns_list):
    """Highest correlation between a candidate's returns and any already-held
    position's returns. 0.0 if nothing is held or there's no comparable data —
    meaning 'not correlated with anything, safe to add' by default."""
    best = 0.0
    for r in held_returns_list:
        c = correlation(candidate_returns, r)
        if c is not None:
            best = max(best, c)
    return best
