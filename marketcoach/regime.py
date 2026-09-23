"""Market-regime filter: risk-on only when the market leader (BTC) is in an
uptrend (above its long moving average). Crypto is heavily correlated — when BTC
is bleaking, almost everything bleeds — so gating new entries by BTC's trend keeps
the whole book out of broad bear markets, which is where our strategies lost.
"""
import json, os
from . import data, indicators, kraken_data

STATE_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "paper_state", "regime_state.json")


def risk_on_timestamps(asset="BTC-USD", granularity=86400, period=200, max_bars=1500):
    """Set of bar timestamps where `asset` close is above its `period` SMA.

    For 4h bars the regime is the DAILY 200-day trend (a 200-bar SMA on 4h
    bars would only be ~33 days — a different, twitchier filter than the one
    the live accounts use). Each 4h bar gets the state of the most recent
    CLOSED daily bar before it, so no future information leaks in."""
    if granularity == 14400:
        daily = risk_on_timestamps(asset, 86400, period, 1500)
        bars = data.get_bars(asset, granularity=granularity, max_bars=max_bars)
        return {b.ts for b in bars if ((b.ts // 86400) * 86400 - 86400) in daily}
    bars = data.get_bars(asset, granularity=granularity, max_bars=max_bars)
    closes = [b.close for b in bars]
    sma = indicators.sma(closes, period)
    return {b.ts for b, s in zip(bars, sma) if s is not None and b.close > s}


def is_risk_on(asset="BTC-USD", granularity=86400, period=200):
    """Live check: is the market risk-on right now (latest closed bar)? Uses
    Kraken's own price data — same source as live signals/orders/balance —
    unlike risk_on_timestamps() above, which needs Coinbase's deeper history
    for backtesting and stays on that source."""
    bars = kraken_data.fetch_recent(asset, granularity)
    closes = [b.close for b in bars]
    sma = indicators.sma(closes, period)
    for b, s in zip(reversed(bars), reversed(sma)):
        if s is not None:
            return b.close > s
    return True  # not enough history -> don't block


def check_and_alert(asset="BTC-USD", granularity=86400, period=200):
    """Same as is_risk_on(), but fires a push notification the moment the regime
    actually FLIPS direction (not on every tick — only on a genuine change since
    the last check). Shared across all regime-filtered accounts via one state
    file, since the regime is a market-wide fact, not a per-account one."""
    from . import alerts
    now_on = is_risk_on(asset, granularity, period)
    prev = None
    if os.path.exists(STATE_FILE):
        try:
            prev = json.load(open(STATE_FILE)).get("risk_on")
        except Exception:
            pass
    if prev is not None and prev != now_on:
        if now_on:
            msg = ("Market flipped to RISK-ON: BTC is back above its 200-day average. "
                  "New buys are allowed again across regime-filtered accounts.")
            tags = ["chart_with_upwards_trend"]
        else:
            msg = ("Market flipped to RISK-OFF: BTC dropped below its 200-day average. "
                  "New buys are now BLOCKED across regime-filtered accounts (existing "
                  "positions still exit normally).")
            tags = ["warning"]
        alerts.notify("market_coach: regime flip", msg, priority="high", tags=tags)
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    json.dump({"risk_on": now_on}, open(STATE_FILE, "w"))
    return now_on
