"""Historical market simulator: run any bot through years of real (and resampled) market history, fast,
and read the results as YEARS, not weeks.

  load_long()          ~9 years of daily bars for the coins that existed then (2017/18 ->), tail-aligned
  bot_equity()         pooled daily equity of one strategy on a bar set (the same engine the live code is
                       parity-tested against; real fees; the bot's own market filter)
  yearly()             calendar-year returns + rolling 365-day windows: how often is a year GREEN
  synth_markets()      block-bootstrapped alternative markets: cross-sectional (open/high/low/close/volume)
                       day-bundles resampled in blocks, so coin correlation and daily shape survive but the
                       ORDER of events is new; lets us run hundreds of 'years' quickly
Nothing here trades or touches accounts.
"""
import json, os, random
from . import data, engine, kraken_fees, market_filters as mf, regime
from .strategy import Strategy
from .data import Bar

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LONG_COINS = ["BTC", "ETH", "LTC", "XRP", "ADA", "LINK", "XLM", "ETC", "BCH", "DOGE", "ATOM", "ALGO"]
MIN_BARS = 2200


def load_long(coins=LONG_COINS, bars_wanted=3300):
    out = {}
    for c in coins:
        try:
            b = data.fetch_coinbase_history(f"{c}-USD", 86400, bars_wanted, True, cache_tag="_long")
            if len(b) >= MIN_BARS:
                out[f"{c}-USD"] = b
        except Exception:
            pass
    n = min(len(b) for b in out.values())
    return {p: b[-n:] for p, b in out.items()}


def filters_for(bars):
    btc = bars["BTC-USD"]
    c = [b.close for b in btc]
    f0 = {b.ts for i, b in enumerate(btc) if i >= 199 and c[i] > sum(c[i - 199:i + 1]) / 200}
    return f0, f0 & mf.f1_timestamps(btc)


def bot_equity(spec, bars, allow, cash_per_coin=1.0):
    """{ts: pooled equity} for one strategy over the bar set."""
    spec, _ = kraken_fees.apply_to_spec(spec) if "costs" in spec else (spec, None)
    n = len(next(iter(bars.values())))
    tot = [0.0] * n
    for b in bars.values():
        r = engine.run(Strategy(spec), b, cash=cash_per_coin, allow_buy_ts=allow)
        for i in range(n):
            tot[i] += r.equity[i]
    ts = [b.ts for b in next(iter(bars.values()))]
    return dict(zip(ts, tot))


def yearly(series):
    """Calendar-year returns and rolling-365-day statistics from a {ts: equity} series."""
    import time as _t
    days = sorted(series)
    by_year = {}
    for d in days:
        by_year.setdefault(_t.gmtime(d).tm_year, []).append(d)
    cal = {}
    for y, ds in by_year.items():
        prev = series[days[days.index(ds[0]) - 1]] if days.index(ds[0]) > 0 else series[ds[0]]
        cal[y] = round((series[ds[-1]] / prev - 1) * 100, 1)
    roll = [series[days[i]] / series[days[i - 365]] - 1 for i in range(365, len(days), 7)]
    srt = sorted(roll)
    return {"calendar": cal, "windows": len(roll),
            "green_year_pct": round(100 * sum(1 for r in roll if r > 0) / len(roll), 1) if roll else None,
            "median_pct": round(100 * srt[len(srt) // 2], 1) if roll else None,
            "p10_pct": round(100 * srt[len(srt) // 10], 1) if roll else None,
            "worst_pct": round(100 * srt[0], 1) if roll else None,
            "best_pct": round(100 * srt[-1], 1) if roll else None}


def synth_markets(bars, n_paths, days=565, block=20, seed=1):
    """Bootstrap alternative markets. A 'day bundle' = every coin's (open/prev-close gap, high/open, low/open,
    close/open, volume) for one real day. Blocks of consecutive bundles are resampled with replacement, so
    cross-coin correlation, volatility clustering inside a block and the daily candle shape are preserved.
    Yields dicts {product: [Bar,...]} of `days` bars (first 200 are warm-up for the 200-day filter)."""
    prods = list(bars)
    n = len(bars[prods[0]])
    bundles = []
    for i in range(1, n):
        bundles.append({p: (bars[p][i].open / bars[p][i - 1].close, bars[p][i].high / bars[p][i].open,
                            bars[p][i].low / bars[p][i].open, bars[p][i].close / bars[p][i].open, bars[p][i].volume)
                        for p in prods})
    rng = random.Random(seed)
    t0 = bars[prods[0]][0].ts
    for _ in range(n_paths):
        seq = []
        while len(seq) < days:
            s = rng.randrange(0, len(bundles) - block)
            seq.extend(bundles[s:s + block])
        seq = seq[:days]
        out = {}
        for p in prods:
            px, bs = bars[p][0].close, []
            for k, bd in enumerate(seq):
                g, hi, lo, cl, vol = bd[p]
                o = px * g
                bs.append(Bar(t0 + k * 86400, o, o * hi, o * lo, o * cl, vol))
                px = o * cl
            out[p] = bs
        yield out
