"""Long-only market filters (pre-registered, see filter_study.py).

F0  BTC close > SMA200                       (the original risk-on filter, regime.py)
F1  F0 AND SMA50 rising (SMA50 > its value 10 days ago)
Only F1 is used by accounts (state["market_filter"] == "F1"); it is strictly TIGHTER than F0,
so it can only remove entries, never add them. Every bar's state uses only data through that
bar's own close (no lookahead)."""
import time

SMA_SLOW, SMA_MID, SLOPE_LAG = 200, 50, 10


def _sma(c, n, i):
    return sum(c[i - n + 1:i + 1]) / n if i >= n - 1 else None


def _f1_at(c, i):
    s200, s50, s50p = _sma(c, SMA_SLOW, i), _sma(c, SMA_MID, i), _sma(c, SMA_MID, i - SLOPE_LAG)
    return bool(s200 is not None and s50p is not None and c[i] > s200 and s50 > s50p)


def f1_timestamps(btc_bars):
    c = [b.close for b in btc_bars]
    return {b.ts for i, b in enumerate(btc_bars) if _f1_at(c, i)}


def f1_timestamps_4h(daily_btc_bars, bars_4h):
    """F1 for 4h bars: each bar takes the F1 state of the most recent CLOSED daily bar
    before it (same mapping regime.risk_on_timestamps uses for 4h)."""
    daily = f1_timestamps(daily_btc_bars)
    return {b.ts for b in bars_4h if ((b.ts // 86400) * 86400 - 86400) in daily}


_CACHE = {"at": 0, "val": None}


def f1_on_now():
    """Live F1 state from Kraken BTC daily bars (closed bars only), cached 10 min."""
    if _CACHE["val"] is not None and time.time() - _CACHE["at"] < 600:
        return _CACHE["val"]
    from . import kraken_data
    bars = [b for b in kraken_data.fetch_recent("BTC-USD", 86400) if b.ts + 86400 <= time.time()]
    val = _f1_at([b.close for b in bars], len(bars) - 1)
    _CACHE.update(at=time.time(), val=val)
    return val
