"""Indicators computed on a close-price series (plus high/low/volume where the
real-world definition needs them). Every function returns a list the same
length as its input, with None for warmup bars where the value isn't defined
yet.

Discipline: indicator[i] uses only bar 0..i. No lookahead, ever. This is the
market equivalent of deckcoach never letting the sim peek at future draws.
"""


def sma(prices, n):
    out = [None] * len(prices)
    s = 0.0
    for i, p in enumerate(prices):
        s += p
        if i >= n:
            s -= prices[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def ema(prices, n):
    out = [None] * len(prices)
    k = 2 / (n + 1)
    e = None
    for i, p in enumerate(prices):
        if i == n - 1:
            e = sum(prices[:n]) / n  # seed with SMA
            out[i] = e
        elif i >= n:
            e = p * k + e * (1 - k)
            out[i] = e
    return out


def _ema_of(series, n):
    """EMA of a series that may itself start with None values (e.g. a MACD line
    built on top of two EMAs) — seeds once n valid values have accumulated."""
    out = [None] * len(series)
    k = 2 / (n + 1)
    e = None
    count = 0
    for i, v in enumerate(series):
        if v is None:
            continue
        count += 1
        if count < n:
            continue
        if count == n:
            valid = [x for x in series[:i + 1] if x is not None][-n:]
            e = sum(valid) / n
        else:
            e = v * k + e * (1 - k)
        out[i] = e
    return out


def _sma_of(series, n):
    """SMA of a series that may start with None values, skipping over them."""
    out = [None] * len(series)
    for i in range(len(series)):
        if i - n + 1 < 0:
            continue
        window = series[i - n + 1:i + 1]
        if any(v is None for v in window):
            continue
        out[i] = sum(window) / n
    return out


def rsi(prices, n=14):
    out = [None] * len(prices)
    if len(prices) <= n:
        return out
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = prices[i] - prices[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    ag, al = gains / n, losses / n
    out[n] = 100 - 100 / (1 + (ag / al if al else 1e9))
    for i in range(n + 1, len(prices)):
        d = prices[i] - prices[i - 1]
        ag = (ag * (n - 1) + max(d, 0)) / n
        al = (al * (n - 1) + max(-d, 0)) / n
        out[i] = 100 - 100 / (1 + (ag / al if al else 1e9))
    return out


def roc(prices, n):
    """Rate of change (momentum) over n bars, as a fraction."""
    out = [None] * len(prices)
    for i in range(n, len(prices)):
        if prices[i - n]:
            out[i] = prices[i] / prices[i - n] - 1
    return out


def donchian_high(prices, n):
    """Highest of the PRIOR n closes (excludes the current bar), so 'price crosses
    above donchian_high:20' means a genuine new-high breakout — the classic moon
    entry."""
    out = [None] * len(prices)
    for i in range(n, len(prices)):
        out[i] = max(prices[i - n:i])
    return out


def donchian_low(prices, n):
    """Lowest of the prior n closes — breakdown / exit reference."""
    out = [None] * len(prices)
    for i in range(n, len(prices)):
        out[i] = min(prices[i - n:i])
    return out


def vol_ratio(volumes, n):
    """Current volume / average of the prior n volumes. >1 means unusual interest —
    the confirmation that a move is real, not a thin wick. Needs volume, not price."""
    out = [None] * len(volumes)
    for i in range(n, len(volumes)):
        avg = sum(volumes[i - n:i]) / n
        out[i] = volumes[i] / avg if avg else None
    return out


# --- MACD (close-only) ---

def macd_line(prices, fast=12, slow=26):
    ef, es = ema(prices, fast), ema(prices, slow)
    return [(a - b) if (a is not None and b is not None) else None
            for a, b in zip(ef, es)]


def macd_signal(prices, fast=12, slow=26, signal=9):
    return _ema_of(macd_line(prices, fast, slow), signal)


# --- Bollinger Bands (close-only) ---

def _stdev_at(prices, n, i):
    window = prices[i - n + 1:i + 1]
    m = sum(window) / n
    return (sum((x - m) ** 2 for x in window) / n) ** 0.5


def bollinger_mid(prices, n=20):
    return sma(prices, n)


def bollinger_upper(prices, n=20, k=2):
    mid = sma(prices, n)
    out = [None] * len(prices)
    for i in range(n - 1, len(prices)):
        out[i] = mid[i] + k * _stdev_at(prices, n, i)
    return out


def bollinger_lower(prices, n=20, k=2):
    mid = sma(prices, n)
    out = [None] * len(prices)
    for i in range(n - 1, len(prices)):
        out[i] = mid[i] - k * _stdev_at(prices, n, i)
    return out


# --- High/low/close indicators (need real bar ranges, not just close) ---

def true_range(highs, lows, closes):
    out = [None] * len(closes)
    for i in range(len(closes)):
        if i == 0:
            out[i] = highs[i] - lows[i]
        else:
            out[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]),
                         abs(lows[i] - closes[i - 1]))
    return out


def atr(highs, lows, closes, n=14):
    """Wilder's ATR."""
    tr = true_range(highs, lows, closes)
    out = [None] * len(closes)
    if len(tr) <= n:
        return out
    a = sum(tr[1:n + 1]) / n
    out[n] = a
    for i in range(n + 1, len(closes)):
        a = (a * (n - 1) + tr[i]) / n
        out[i] = a
    return out


def adx(highs, lows, closes, n=14):
    """Wilder's Average Directional Index: trend STRENGTH (0-100), direction-blind.
    Below ~20 the market is ranging; above ~25 a trend is established."""
    m = len(closes)
    out = [None] * m
    if m < 2 * n + 1:
        return out
    tr = true_range(highs, lows, closes)
    pdm, mdm = [0.0] * m, [0.0] * m
    for i in range(1, m):
        up, dn = highs[i] - highs[i - 1], lows[i - 1] - lows[i]
        pdm[i] = up if (up > dn and up > 0) else 0.0
        mdm[i] = dn if (dn > up and dn > 0) else 0.0
    str_, sp, sm = sum(tr[1:n + 1]), sum(pdm[1:n + 1]), sum(mdm[1:n + 1])
    dx = [None] * m
    for i in range(n, m):
        if i > n:
            str_ = str_ - str_ / n + tr[i]
            sp = sp - sp / n + pdm[i]
            sm = sm - sm / n + mdm[i]
        if str_ <= 0:
            dx[i] = 0.0
            continue
        pdi, mdi = 100 * sp / str_, 100 * sm / str_
        dx[i] = 100 * abs(pdi - mdi) / (pdi + mdi) if (pdi + mdi) > 0 else 0.0
    first = 2 * n - 1
    a = sum(dx[n:2 * n]) / n
    out[first] = a
    for i in range(first + 1, m):
        a = (a * (n - 1) + dx[i]) / n
        out[i] = a
    return out


def stoch_k(highs, lows, closes, n=14):
    out = [None] * len(closes)
    for i in range(n - 1, len(closes)):
        hh = max(highs[i - n + 1:i + 1])
        ll = min(lows[i - n + 1:i + 1])
        out[i] = 100 * (closes[i] - ll) / (hh - ll) if hh != ll else 50.0
    return out


def stoch_d(highs, lows, closes, n=14, d=3):
    return _sma_of(stoch_k(highs, lows, closes, n), d)


def cci(highs, lows, closes, n=20):
    tp = [(h + l + c) / 3 for h, l, c in zip(highs, lows, closes)]
    out = [None] * len(closes)
    for i in range(n - 1, len(closes)):
        window = tp[i - n + 1:i + 1]
        m = sum(window) / n
        md = sum(abs(x - m) for x in window) / n
        out[i] = (tp[i] - m) / (0.015 * md) if md else 0.0
    return out


def willr(highs, lows, closes, n=14):
    """Williams %R: 0 (overbought) to -100 (oversold)."""
    out = [None] * len(closes)
    for i in range(n - 1, len(closes)):
        hh = max(highs[i - n + 1:i + 1])
        ll = min(lows[i - n + 1:i + 1])
        out[i] = -100 * (hh - closes[i]) / (hh - ll) if hh != ll else -50.0
    return out


def keltner_upper(closes, highs, lows, n=20, atr_n=10, mult=2):
    mid, a = ema(closes, n), atr(highs, lows, closes, atr_n)
    return [(m + mult * x) if (m is not None and x is not None) else None
            for m, x in zip(mid, a)]


def keltner_lower(closes, highs, lows, n=20, atr_n=10, mult=2):
    mid, a = ema(closes, n), atr(highs, lows, closes, atr_n)
    return [(m - mult * x) if (m is not None and x is not None) else None
            for m, x in zip(mid, a)]


# --- Ichimoku (high/low only; senkou spans use the CURRENT-bar formula, not the
# traditional forward-plotted cloud shift — a common backtest simplification so
# the signal stays a same-bar filter rather than needing lookahead-safe shifting) ---

def ichimoku_mid(highs, lows, n):
    out = [None] * len(highs)
    for i in range(n - 1, len(highs)):
        out[i] = (max(highs[i - n + 1:i + 1]) + min(lows[i - n + 1:i + 1])) / 2
    return out


def ichimoku_tenkan(highs, lows, n=9):
    return ichimoku_mid(highs, lows, n)


def ichimoku_kijun(highs, lows, n=26):
    return ichimoku_mid(highs, lows, n)


def ichimoku_senkou_b(highs, lows, n=52):
    return ichimoku_mid(highs, lows, n)


def ichimoku_senkou_a(highs, lows, tenkan_n=9, kijun_n=26):
    t, k = ichimoku_mid(highs, lows, tenkan_n), ichimoku_mid(highs, lows, kijun_n)
    return [(a + b) / 2 if (a is not None and b is not None) else None
            for a, b in zip(t, k)]


# name -> (fn, arg_kind). arg_kind says what resolve_series must feed it:
#   "close"    -> fn(prices, *params)
#   "hlc"      -> fn(highs, lows, prices, *params)
#   "hl"       -> fn(highs, lows, *params)
#   "close_hl" -> fn(prices, highs, lows, *params)
REGISTRY = {
    "sma": (sma, "close"), "ema": (ema, "close"), "rsi": (rsi, "close"),
    "roc": (roc, "close"),
    "donchian_high": (donchian_high, "close"), "donchian_low": (donchian_low, "close"),
    "macd_line": (macd_line, "close"), "macd_signal": (macd_signal, "close"),
    "bollinger_upper": (bollinger_upper, "close"),
    "bollinger_mid": (bollinger_mid, "close"),
    "bollinger_lower": (bollinger_lower, "close"),
    "atr": (atr, "hlc"), "adx": (adx, "hlc"), "stoch_k": (stoch_k, "hlc"), "stoch_d": (stoch_d, "hlc"),
    "cci": (cci, "hlc"), "willr": (willr, "hlc"),
    "keltner_upper": (keltner_upper, "close_hl"), "keltner_lower": (keltner_lower, "close_hl"),
    "ichimoku_tenkan": (ichimoku_tenkan, "hl"), "ichimoku_kijun": (ichimoku_kijun, "hl"),
    "ichimoku_senkou_a": (ichimoku_senkou_a, "hl"), "ichimoku_senkou_b": (ichimoku_senkou_b, "hl"),
}


def _num(x):
    try:
        return int(x)
    except ValueError:
        return float(x)


def resolve_series(ref, prices, volumes=None, highs=None, lows=None):
    """Turn a ref like 'sma:20', 'macd_line:12:26', 'bollinger_upper:20:2',
    'atr:14', 'price', or a bare number into a series. Numbers become a constant
    series so triggers can compare against thresholds. Extra ':'-separated
    params after the name are passed positionally to the indicator function.
    Volume/high/low-based refs need the matching array supplied by the caller."""
    if isinstance(ref, (int, float)):
        return [float(ref)] * len(prices)
    ref = str(ref)
    if ref == "price":
        return list(prices)
    try:  # numeric literal as string
        return [float(ref)] * len(prices)
    except ValueError:
        pass
    name, *param_strs = ref.split(":")
    params = [_num(p) for p in param_strs]
    if name == "vol_ratio":
        if volumes is None:
            raise ValueError("vol_ratio needs volume data — caller must pass volumes")
        return vol_ratio(volumes, *params)
    if name not in REGISTRY:
        raise ValueError(f"unknown indicator ref: {ref!r}")
    fn, kind = REGISTRY[name]
    if kind == "close":
        return fn(prices, *params)
    if highs is None or lows is None:
        raise ValueError(f"{ref!r} needs high/low data — caller must pass highs and lows")
    if kind == "hlc":
        return fn(highs, lows, prices, *params)
    if kind == "hl":
        return fn(highs, lows, *params)
    if kind == "close_hl":
        return fn(prices, highs, lows, *params)
    raise AssertionError(f"unhandled arg_kind {kind!r} for {name!r}")
