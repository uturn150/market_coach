"""Moon monitor: detect coins breaking out ('mooning') and rank them, so the
portfolio can funnel into strength instead of missing it.

The TRIGGERS (all must fire for is_mooning, each tunable):
  1. BREAKOUT  — close makes a new high vs the prior `donch` bars (Donchian).
  2. MOMENTUM  — return over the last `roc_bars` >= `roc_min` (real thrust up).
  3. VOLUME    — latest volume >= `vol_mult` x its recent average (real interest,
                 not a thin-liquidity wick — the kind of fakeout that traps you).
  4. NOT BLOW-OFF — RSI <= `rsi_max`, so we don't buy the vertical exhaustion top.

moon_score ranks *how* strong a breakout is (for funneling priority) even when not
all triggers fire, so the monitor still shows what's heating up.
"""
from . import indicators

DEFAULT = {"donch": 20, "roc_bars": 7, "roc_min": 0.20,
           "vol_mult": 2.0, "vol_avg": 20, "rsi_max": 90}


def _avg(xs):
    return sum(xs) / len(xs) if xs else 0.0


def evaluate(bars, cfg=None):
    """Return a dict of the trigger readings + composite score for the latest bar."""
    cfg = {**DEFAULT, **(cfg or {})}
    closes = [b.close for b in bars]
    vols = [b.volume for b in bars]
    n = len(bars)
    if n < max(cfg["donch"], cfg["roc_bars"], cfg["vol_avg"]) + 2:
        return None

    dhigh = indicators.donchian_high(closes, cfg["donch"])
    roc = indicators.roc(closes, cfg["roc_bars"])
    rsi = indicators.rsi(closes, 14)

    i = n - 1
    prev_high = dhigh[i]
    breakout = prev_high is not None and closes[i] > prev_high
    momentum = roc[i] if roc[i] is not None else 0.0
    vol_ref = _avg(vols[i - cfg["vol_avg"]:i]) or 1e-9
    vol_ratio = vols[i] / vol_ref
    rsi_now = rsi[i] if rsi[i] is not None else 50.0

    triggers = {
        "breakout": bool(breakout),
        "momentum": momentum >= cfg["roc_min"],
        "volume": vol_ratio >= cfg["vol_mult"],
        "not_blowoff": rsi_now <= cfg["rsi_max"],
    }
    is_mooning = all(triggers.values())
    # score: momentum is the backbone; breakout and volume amplify; blow-off damps.
    score = (max(momentum, 0) * 100
             + (15 if breakout else 0)
             + min(vol_ratio, 5) * 4
             - max(rsi_now - cfg["rsi_max"], 0))
    return {"price": closes[i], "roc": momentum, "vol_ratio": vol_ratio, "rsi": rsi_now,
            "breakout": breakout, "triggers": triggers, "is_mooning": is_mooning,
            "score": round(score, 1)}


def rank(coin_bars, cfg=None):
    """coin_bars: {product: bars} -> list of (product, evaluation) sorted by score."""
    out = []
    for product, bars in coin_bars.items():
        ev = evaluate(bars, cfg)
        if ev:
            out.append((product, ev))
    out.sort(key=lambda t: t[1]["score"], reverse=True)
    return out
