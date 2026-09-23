"""Four-state market regime classifier + per-strategy performance by regime.

States (market proxy = BTC daily bars, the same anchor the binary risk-on/off
filter in regime.py already uses):
  HIGH_VOLATILITY  20-day realized vol is in the top 15% of its own trailing
                   365-day distribution. Overrides the trend states — the
                   dangerous, hardest-to-trade condition wins the label.
  TRENDING_UP      not high-vol, 60-day return > +10% AND close above its 100d SMA.
  TRENDING_DOWN    not high-vol, 60-day return < -10% AND close below its 100d SMA.
  SIDEWAYS         everything else.

NO LOOKAHEAD, twice over:
  1. A day's label uses only data up to and including that day's close
     (trailing windows, and the vol percentile is taken against PAST vols only).
  2. Performance attribution applies the label from day t-1's close to the
     return earned on day t — you can only know the regime after it closed.

Thresholds are round, conventional numbers chosen BEFORE looking at results
and are not tuned here; they are constants below so a change is deliberate
and visible, not a silent knob turned to make a picture prettier.
"""
import json, math, os, time
from collections import Counter

LABELS = ["TRENDING_UP", "TRENDING_DOWN", "SIDEWAYS", "HIGH_VOLATILITY"]
TREND_LOOKBACK, SMA_N, VOL_N, VOL_WINDOW = 60, 100, 20, 365
TREND_THRESHOLD, VOL_PERCENTILE = 0.10, 0.85
MIN_VOL_HISTORY = 120

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_DIR = os.path.join(BASE, "logs", "regimes")
HISTORY_FILE = os.path.join(BASE, "logs", "regime_history.jsonl")


def classify_series(bars):
    """List of labels aligned to `bars` (None where there isn't enough history)."""
    closes = [b.close for b in bars]
    n = len(closes)
    rets = [None] + [closes[i] / closes[i - 1] - 1 for i in range(1, n)]
    vols = [None] * n
    for i in range(VOL_N, n):
        w = rets[i - VOL_N + 1:i + 1]
        m = sum(w) / VOL_N
        vols[i] = math.sqrt(sum((x - m) ** 2 for x in w) / (VOL_N - 1))
    labels = [None] * n
    for i in range(n):
        if i < max(SMA_N, TREND_LOOKBACK) or vols[i] is None:
            continue
        past = [v for v in vols[max(0, i - VOL_WINDOW + 1):i + 1] if v is not None]
        if len(past) < MIN_VOL_HISTORY:
            continue
        pct = sum(1 for v in past if v <= vols[i]) / len(past)
        if pct >= VOL_PERCENTILE:
            labels[i] = "HIGH_VOLATILITY"
            continue
        ret_n = closes[i] / closes[i - TREND_LOOKBACK] - 1
        sma = sum(closes[i - SMA_N + 1:i + 1]) / SMA_N
        if ret_n > TREND_THRESHOLD and closes[i] > sma:
            labels[i] = "TRENDING_UP"
        elif ret_n < -TREND_THRESHOLD and closes[i] < sma:
            labels[i] = "TRENDING_DOWN"
        else:
            labels[i] = "SIDEWAYS"
    return labels


def label_map(bars):
    return {b.ts: l for b, l in zip(bars, classify_series(bars)) if l}


_CACHE = {"at": 0, "val": None}


def current_regime():
    """Live label from Kraken's own BTC daily bars (closed bars only). Cached
    10 minutes: the dashboard rebuilds every few seconds, the label changes
    at most once a day."""
    if _CACHE["val"] and time.time() - _CACHE["at"] < 600:
        return _CACHE["val"]
    from . import kraken_data
    bars = [b for b in kraken_data.fetch_recent("BTC-USD", 86400) if b.ts + 86400 <= time.time()]
    labels = classify_series(bars)
    cur = labels[-1]
    run = 1
    while run < len(labels) and labels[-1 - run] == cur:
        run += 1
    _CACHE.update(at=time.time(), val={"label": cur, "days_in_regime": run, "as_of": bars[-1].ts})
    return _CACHE["val"]


def record_daily():
    """Append today's label once (idempotent per as_of day). The live history
    is what lets real paper P&L be attributed to regimes as it accumulates."""
    cur = current_regime()
    seen = set()
    if os.path.exists(HISTORY_FILE):
        for line in open(HISTORY_FILE):
            try:
                seen.add(json.loads(line)["as_of"])
            except Exception:
                pass
    if cur["as_of"] not in seen:
        os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
        with open(HISTORY_FILE, "a") as f:
            f.write(json.dumps({**cur, "iso": time.strftime("%Y-%m-%d %H:%M:%S")}) + "\n")
    return cur


def _compound(rs):
    e = 1.0
    for r in rs:
        e *= 1 + r
    return e - 1


def _corr(a, b):
    from . import risk
    return risk.correlation(a, b)


def analyze(verbose=True):
    from . import data
    from .portfolio_correlation import REAL_STRATEGIES, FOUR_H, _dated_daily_equity
    from . import regime as regime_filter
    from .portfolio_optimize import DEFAULT_BASKET

    products = [f"{c}-USD" for c in DEFAULT_BASKET]
    daily_bars = {p: data.get_bars(p, granularity=86400, max_bars=1500) for p in products}
    four_bars = {p: data.get_bars(p, granularity=14400, max_bars=6570) for p in products}
    labels = label_map(daily_bars["BTC-USD"])
    allow_d = regime_filter.risk_on_timestamps("BTC-USD", 86400, 200, 1500)
    allow_4 = regime_filter.risk_on_timestamps("BTC-USD", 14400, 200, 6570)
    per_coin = 50.0 / len(products)

    series = {}
    for acct, name in REAL_STRATEGIES.items():
        series[f"daily:{acct}"] = _dated_daily_equity(f"strategies/{name}.json", daily_bars, per_coin, allow_d, False)
    for fam in FOUR_H:
        path = f"strategies/c4h_{fam}.json"
        if os.path.exists(path):
            series[f"4h:{fam}"] = _dated_daily_equity(path, four_bars, per_coin, allow_4, True)
    # equal-weight buy-and-hold of the same basket, on the same dates
    n = min(len(b) for b in daily_bars.values())
    tails = [b[-n:] for b in daily_bars.values()]
    hold = {tails[0][i].ts: sum(per_coin * t[i].close / t[0].close for t in tails) for i in range(n)}
    series["HOLD (equal-weight basket)"] = hold

    days = sorted(set.intersection(*[set(s) for s in series.values()]) & set(labels))
    # return on day j attributed to the regime known at day j-1's close
    attrib = {name: {l: [] for l in LABELS} for name in series}
    day_regime = {}
    for j in range(1, len(days)):
        prev, cur = days[j - 1], days[j]
        reg = labels.get(prev)
        if not reg:
            continue
        day_regime[cur] = reg
        for name, s in series.items():
            if s[prev] > 0:
                attrib[name][reg].append(s[cur] / s[prev] - 1)

    counts = Counter(day_regime.values())
    total_days = sum(counts.values())
    table = {}
    for name in series:
        table[name] = {}
        for l in LABELS:
            rs = attrib[name][l]
            table[name][l] = {
                "days": len(rs),
                "return_pct": round(_compound(rs) * 100, 1),
                "avg_daily_bps": round(sum(rs) / len(rs) * 1e4, 1) if rs else 0.0,
                "worst_day_pct": round(min(rs) * 100, 1) if rs else 0.0,
                "up_days_pct": round(sum(1 for r in rs if r > 0) / len(rs) * 100, 0) if rs else 0.0,
            }
    hold_key = "HOLD (equal-weight basket)"
    for name in series:
        for l in LABELS:
            table[name][l]["excess_vs_hold_pct"] = round(
                table[name][l]["return_pct"] - table[hold_key][l]["return_pct"], 1)

    # complementarity: average pairwise correlation of the STRATEGIES within each regime
    strat_names = [k for k in series if k != hold_key]
    comp = {}
    for l in LABELS:
        vals = []
        for a in range(len(strat_names)):
            for b in range(a + 1, len(strat_names)):
                ra, rb = attrib[strat_names[a]][l], attrib[strat_names[b]][l]
                c = _corr(ra, rb) if len(ra) >= 20 else None
                if c is not None:
                    vals.append(c)
        comp[l] = round(sum(vals) / len(vals), 2) if vals else None

    result = {"regime_days": dict(counts), "regime_share_pct": {l: round(counts[l] / total_days * 100, 1) for l in LABELS},
              "table": table, "avg_pairwise_strategy_correlation_by_regime": comp,
              "period": [time.strftime("%Y-%m-%d", time.gmtime(days[0])), time.strftime("%Y-%m-%d", time.gmtime(days[-1]))]}
    os.makedirs(RESULTS_DIR, exist_ok=True)
    json.dump(result, open(os.path.join(RESULTS_DIR, f"analysis_{int(time.time())}.json"), "w"), indent=2)
    if verbose:
        print(f"Period {result['period'][0]} -> {result['period'][1]}   regime days: {dict(counts)}")
        print("share:", result["regime_share_pct"])
        hdr = f"{'strategy':<28}" + "".join(f"{l[:9]:>16}" for l in LABELS)
        print("\nCompounded return % while in each regime (excess vs hold in parentheses)\n" + hdr)
        for name in series:
            row = f"{name:<28}"
            for l in LABELS:
                t = table[name][l]
                row += f"{t['return_pct']:>9.1f}({t['excess_vs_hold_pct']:>+5.0f})"
            print(row)
        print("\nAvg pairwise correlation between strategies, by regime:", comp)
    return result
