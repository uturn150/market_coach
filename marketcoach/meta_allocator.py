"""Meta-allocator: splits capital across a fleet of bots, updating weekly from PAST results only.

A method takes the trailing daily returns of every bot (dict name -> list, oldest first, all ending 'today')
and returns weights that sum to <= 1 (the remainder stays in cash). Same functions drive the historical
replay (meta_lab.py) and the live allocator. Methods are fixed in advance:

  equal        1/N to every bot (the baseline every other method must beat)
  momentum     top-K bots by trailing 90-day return (only those > 0), equal weight among them
  invvol       weight ~ 1 / trailing 60-day volatility (risk parity-ish), all bots
  bandit       Thompson-style: weight ~ probability that the bot's mean weekly return is > 0
               (t-stat of trailing 26 weeks -> normal CDF), zero below 0.5
  cutlosers    equal weight among bots whose current drawdown from their own 180-day high is shallower
               than 10%; cash if none
  regime       market-regime rule from BTC: uptrend -> trend bots; otherwise -> dip/grid/DCA bots
  blend        average of momentum, invvol and cutlosers weights
"""
import math

WEEK = 7


def _stdev(x):
    m = sum(x) / len(x)
    return math.sqrt(sum((v - m) ** 2 for v in x) / max(1, len(x) - 1))


def _norm(w, gross=1.0):
    s = sum(w.values())
    return {k: gross * v / s for k, v in w.items()} if s > 0 else {}


def _cum(r):
    e = 1.0
    for x in r:
        e *= 1 + x
    return e - 1


def equal(R, **_):
    return {k: 1.0 / len(R) for k in R}


def momentum(R, k=8, lookback=90, **_):
    sc = {n: _cum(r[-lookback:]) for n, r in R.items() if len(r) >= lookback}
    top = [n for n, v in sorted(sc.items(), key=lambda kv: -kv[1])[:k] if v > 0]
    return {n: 1.0 / len(top) for n in top} if top else {}


def invvol(R, lookback=60, **_):
    w = {}
    for n, r in R.items():
        if len(r) >= lookback:
            sd = _stdev(r[-lookback:])
            if sd > 1e-9:
                w[n] = 1.0 / sd
    return _norm(w)


def bandit(R, weeks=26, **_):
    w = {}
    for n, r in R.items():
        if len(r) < weeks * WEEK:
            continue
        wk = [_cum(r[-(i + 1) * WEEK: len(r) - i * WEEK]) for i in range(weeks)]
        sd = _stdev(wk)
        if sd < 1e-9:
            continue
        t = (sum(wk) / weeks) / (sd / math.sqrt(weeks))
        p = 0.5 * (1 + math.erf(t / math.sqrt(2)))
        if p > 0.5:
            w[n] = p - 0.5
    return _norm(w)


def cutlosers(R, lookback=180, dd_limit=0.10, **_):
    keep = []
    for n, r in R.items():
        if len(r) < 30:
            continue
        e, peak = 1.0, 1.0
        for x in r[-lookback:]:
            e *= 1 + x
            peak = max(peak, e)
        if e / peak - 1 > -dd_limit:
            keep.append(n)
    return {n: 1.0 / len(keep) for n in keep} if keep else {}


def regime(R, btc_up=None, families=None, **_):
    if btc_up is None or not families:
        return equal(R)
    want = "trend" if btc_up else "mean"
    names = [n for n in R if families.get(n) == want]
    return {n: 1.0 / len(names) for n in names} if names else {}


def blend(R, **kw):
    parts = [momentum(R, **kw), invvol(R, **kw), cutlosers(R, **kw)]
    out = {}
    for p in parts:
        for n, v in p.items():
            out[n] = out.get(n, 0.0) + v / len(parts)
    return out


METHODS = {"equal": equal, "momentum": momentum, "invvol": invvol, "bandit": bandit,
           "cutlosers": cutlosers, "regime": regime, "blend": blend}
