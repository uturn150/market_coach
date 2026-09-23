"""Faithful ports of real Freqtrade strategy files (reference_code/freqtrade_freqtrade-strategies) to our engine.
Each class keeps the file's OWN parameters, indicator formulas, entry/exit rules, timeframe, ROI table, stoploss and trailing
settings (the intra-bar exit model is engine.run(ft=...)). Deviations are listed per class. Private study port of GPL-3 code:
not for redistribution. Indicators are re-implemented in pure python (the originals use pandas / ta / TA-Lib)."""
import math
from . import indicators as I

FEES = {"order_type": "taker", "taker_fee_bps": 80, "half_spread_bps": 5, "slippage_bps": 3, "maker_fee_bps": 40}


def _sma(x, n):
    out = [None] * len(x); s = 0.0
    for i, v in enumerate(x):
        s += v
        if i >= n: s -= x[i - n]
        if i >= n - 1: out[i] = s / n
    return out


def _shift(x, k):
    return [None] * k + list(x[:len(x) - k]) if k > 0 else list(x)


def _tema(x, n):
    e1 = I.ema(x, n)
    e1c = [v if v is not None else x[0] for v in e1]
    e2 = I.ema(e1c, n); e2c = [v if v is not None else e1c[0] for v in e2]
    e3 = I.ema(e2c, n)
    return [3 * a - 3 * b + c if None not in (a, b, c) else None for a, b, c in zip(e1, e2, e3)]


class _Port:
    tf_minutes = 240
    def __init__(self, fee_bps=None):
        c = dict(FEES)
        if fee_bps is not None:
            c.update(taker_fee_bps=fee_bps, half_spread_bps=0, slippage_bps=0)
        self.costs = c
        self.side_rate = (c["taker_fee_bps"] + c["half_spread_bps"] + c["slippage_bps"]) / 1e4
        self.risk = {}
        self._c = {}

    def ft(self):
        cfg = dict(self.FT)
        cfg["roi"] = [(math.ceil(int(m) / self.tf_minutes), p) for m, p in sorted(self.ROI.items(), key=lambda kv: int(kv[0]))]
        return cfg

    def _memo(self, key, fn):
        if key not in self._c:
            self._c[key] = fn()
        return self._c[key]


class Heracles(_Port):
    """Heracles.py (4h): d = donchian_pband(10)[shift 15] / keltner_wband(20, original)[shift 9]; buy when 0.16 <= d <= 0.75.
    No exit signal (ROI table + stoploss only). ROI {0:.598, 644:.166, 3269:.115, 7289:0}, stoploss -25.6%.
    Deviation: none in logic."""
    tf_minutes = 240
    ROI = {"0": 0.598, "644": 0.166, "3269": 0.115, "7289": 0}
    FT = {"stoploss": -0.256}
    BUY = {"ind_shift": 15, "cross_shift": 9, "div_min": 0.16, "div_max": 0.75}

    def _feat(self, highs, lows, closes):
        n = len(closes)
        tp = [(h + l + c) / 3 for h, l, c in zip(highs, lows, closes)]
        tph = [(4 * h - 2 * l + c) / 3 for h, l, c in zip(highs, lows, closes)]
        tpl = [(-2 * h + 4 * l + c) / 3 for h, l, c in zip(highs, lows, closes)]
        m, u, lo = _sma(tp, 20), _sma(tph, 20), _sma(tpl, 20)
        kcw = [((u[i] - lo[i]) / m[i]) * 100 if None not in (m[i], u[i], lo[i]) and m[i] else None for i in range(n)]
        dcp = [None] * n
        for i in range(9, n):
            hb, lb = max(highs[i - 9:i + 1]), min(lows[i - 9:i + 1])
            dcp[i] = (closes[i] - lb) / (hb - lb) if hb > lb else None
        return dcp, kcw

    def signals_at(self, i, prices, volumes, highs, lows):
        dcp, kcw = self._memo("f", lambda: self._feat(highs, lows, prices))
        a, b = i - self.BUY["ind_shift"], i - self.BUY["cross_shift"]
        if a < 0 or b < 0 or dcp[a] is None or kcw[b] is None or kcw[b] == 0:
            return []
        d = dcp[a] / kcw[b]
        if self.BUY["div_min"] <= d <= self.BUY["div_max"]:
            return [{"action": "buy", "size": "100%", "why": "heracles"}]
        return []


class Supertrend(_Port):
    """Supertrend.py (1h): three supertrend(period, multiplier) 'up' -> buy; three others all 'down' -> sell.
    buy (m,p) = (4,8),(7,9),(1,8); sell (m,p) = (1,16),(3,18),(6,18). ROI {0:.087, 372:.058, 861:.029, 2221:0}, stoploss -26.5%,
    trailing +5% after +14.4% profit (only_offset False). Supertrend uses the freqtrade 'technical' definition (ATR = SMA of true range).
    Deviation: that definition is written from memory of the library, not read from its source."""
    tf_minutes = 60
    ROI = {"0": 0.087, "372": 0.058, "861": 0.029, "2221": 0}
    FT = {"stoploss": -0.265, "trail_pos": 0.05, "trail_off": 0.144, "trail_only_off": False}
    BUY = [(4, 8), (7, 9), (1, 8)]
    SELL = [(1, 16), (3, 18), (6, 18)]

    @staticmethod
    def _supertrend(highs, lows, closes, period, mult):
        n = len(closes)
        tr = [highs[0] - lows[0]] + [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])) for i in range(1, n)]
        atr = _sma(tr, period)
        fub, flb, st, dirn = [0.0] * n, [0.0] * n, [0.0] * n, [None] * n
        for i in range(period, n):
            hl2 = (highs[i] + lows[i]) / 2
            bub, blb = hl2 + mult * atr[i], hl2 - mult * atr[i]
            fub[i] = bub if (bub < fub[i - 1] or closes[i - 1] > fub[i - 1]) else fub[i - 1]
            flb[i] = blb if (blb > flb[i - 1] or closes[i - 1] < flb[i - 1]) else flb[i - 1]
            if st[i - 1] == fub[i - 1]:
                st[i] = fub[i] if closes[i] <= fub[i] else flb[i]
            elif st[i - 1] == flb[i - 1]:
                st[i] = flb[i] if closes[i] >= flb[i] else fub[i]
            else:
                st[i] = 0.0
            dirn[i] = "down" if closes[i] < st[i] else "up"
        return dirn

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 200:
            return []
        b = [self._memo(("b", m, p), lambda m=m, p=p: self._supertrend(highs, lows, prices, p, m)) for m, p in self.BUY]
        s = [self._memo(("s", m, p), lambda m=m, p=p: self._supertrend(highs, lows, prices, p, m)) for m, p in self.SELL]
        if all(x[i] == "up" for x in b):
            return [{"action": "buy", "size": "100%", "why": "supertrend"}]
        if all(x[i] == "down" for x in s):
            return [{"action": "sell", "size": "all", "why": "supertrend"}]
        return []


class MultiMa(_Port):
    """MultiMa.py (4h): buy when TEMA ladder aligns: for c in 0..3 with gap 15 (keys c*15, past (c-1)*15, past>1): TEMA(key) < TEMA(past).
    Sell when ANY of 12 TEMA pairs (gap 68) has TEMA(key) > TEMA(past). ROI {0:.523,1553:.123,2332:.076,3169:0}, stoploss -34.5%."""
    tf_minutes = 240
    ROI = {"0": 0.523, "1553": 0.123, "2332": 0.076, "3169": 0}
    FT = {"stoploss": -0.345}
    BUY_COUNT, BUY_GAP, SELL_COUNT, SELL_GAP = 4, 15, 12, 68

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 300:
            return []
        def T(n): return self._memo(("tema", n), lambda: _tema(prices, n))
        conds = []
        for c in range(self.BUY_COUNT):
            key, past = c * self.BUY_GAP, (c - 1) * self.BUY_GAP
            if past > 1 and key > 1:
                a, b = T(key)[i], T(past)[i]
                if a is None or b is None: return []
                conds.append(a < b)
        if conds and all(conds):
            return [{"action": "buy", "size": "100%", "why": "multima"}]
        ex = []
        for c in range(self.SELL_COUNT):
            key, past = c * self.SELL_GAP, (c - 1) * self.SELL_GAP
            if past > 1 and key > 1:
                a, b = T(key)[i], T(past)[i]
                if a is not None and b is not None:
                    ex.append(a > b)
        if ex and any(ex):
            return [{"action": "sell", "size": "all", "why": "multima"}]
        return []


# ------------------------------------------------------------------ extra indicators (pure python, TA-Lib definitions)
def _stochf(highs, lows, closes, kp, dp=3):
    n = len(closes); k = [None] * n
    for i in range(kp - 1, n):
        hh, ll = max(highs[i - kp + 1:i + 1]), min(lows[i - kp + 1:i + 1])
        k[i] = 100 * (closes[i] - ll) / (hh - ll) if hh > ll else 0.0
    d = [None] * n
    for i in range(kp - 1 + dp - 1, n):
        d[i] = sum(k[i - dp + 1:i + 1]) / dp
    return k, d


def _mfi(highs, lows, closes, vols, n=14):
    tp = [(h + l + c) / 3 for h, l, c in zip(highs, lows, closes)]
    out = [None] * len(closes)
    for i in range(n, len(closes)):
        pos = neg = 0.0
        for j in range(i - n + 1, i + 1):
            f = tp[j] * vols[j]
            if tp[j] > tp[j - 1]: pos += f
            elif tp[j] < tp[j - 1]: neg += f
        out[i] = 100.0 if neg == 0 else 100 - 100 / (1 + pos / neg)
    return out


def _minus_di(highs, lows, closes, n=14):
    m = len(closes); out = [None] * m
    tr = [highs[0] - lows[0]] + [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])) for i in range(1, m)]
    mdm = [0.0] + [(lows[i - 1] - lows[i]) if (lows[i - 1] - lows[i] > highs[i] - highs[i - 1] and lows[i - 1] - lows[i] > 0) else 0.0 for i in range(1, m)]
    if m <= n: return out
    st, sm = sum(tr[1:n + 1]), sum(mdm[1:n + 1])
    out[n] = 100 * sm / st if st else 0.0
    for i in range(n + 1, m):
        st = st - st / n + tr[i]; sm = sm - sm / n + mdm[i]
        out[i] = 100 * sm / st if st else 0.0
    return out


def _sar(highs, lows, af0=0.02, afmax=0.2):
    n = len(highs); sar = [None] * n
    if n < 3: return sar
    up, ep, af, s = True, highs[0], af0, lows[0]
    for i in range(1, n):
        s = s + af * (ep - s)
        if up:
            s = min(s, lows[i - 1], lows[i - 2] if i > 1 else lows[i - 1])
            if lows[i] < s: up, s, ep, af = False, ep, lows[i], af0
            elif highs[i] > ep: ep, af = highs[i], min(af + af0, afmax)
        else:
            s = max(s, highs[i - 1], highs[i - 2] if i > 1 else highs[i - 1])
            if highs[i] > s: up, s, ep, af = True, ep, highs[i], af0
            elif lows[i] < ep: ep, af = lows[i], min(af + af0, afmax)
        sar[i] = s
    return sar


def _rolling_mean(x, n):
    return _sma(x, n)


def _bb_typical(highs, lows, closes, n=20, stds=2.0):
    tp = [(h + l + c) / 3 for h, l, c in zip(highs, lows, closes)]
    mid = _sma(tp, n)
    lower, upper = [None] * len(tp), [None] * len(tp)
    for i in range(n - 1, len(tp)):
        w = tp[i - n + 1:i + 1]; m = mid[i]
        sd = math.sqrt(sum((v - m) ** 2 for v in w) / (n - 1))       # pandas rolling std default (ddof=1)
        lower[i], upper[i] = m - stds * sd, m + stds * sd
    return lower, mid, upper


class Strategy004(_Port):
    """Strategy004.py (5m): (ADX>50 or slowADX(35)>26) & CCI(14)<-100 & fastK/fastD(5) previous <20 & slow fast-stoch(50) previous <30 &
    fastK crossed above fastD & mean volume(12) > 0.75 -> buy;  slowADX<25 & (fastK>70 or fastD>70) & prior fastK<fastD & close>EMA5 -> sell
    (exit_profit_only). ROI {0:5%,20:4%,30:3%,60:1%}, stoploss -10%."""
    tf_minutes = 5
    ROI = {"0": 0.05, "20": 0.04, "30": 0.03, "60": 0.01}
    FT = {"stoploss": -0.10, "exit_profit_only": True}

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 80: return []
        adx = self._memo("adx", lambda: I.adx(highs, lows, prices, 14)); sadx = self._memo("sadx", lambda: I.adx(highs, lows, prices, 35))
        cci = self._memo("cci", lambda: I.cci(highs, lows, prices, 14))
        fk, fd = self._memo("st5", lambda: _stochf(highs, lows, prices, 5))
        sk, sd = self._memo("st50", lambda: _stochf(highs, lows, prices, 50))
        ema5 = self._memo("ema5", lambda: I.ema(prices, 5)); mv = self._memo("mv", lambda: _sma(volumes, 12))
        if None in (adx[i], sadx[i], cci[i], fk[i], fd[i], fk[i - 1], fd[i - 1], sk[i - 1], sd[i - 1], ema5[i], mv[i]): return []
        if ((adx[i] > 50) or (sadx[i] > 26)) and cci[i] < -100 and fk[i - 1] < 20 and fd[i - 1] < 20 and sk[i - 1] < 30 and sd[i - 1] < 30 \
                and fk[i - 1] < fd[i - 1] and fk[i] > fd[i] and mv[i] > 0.75:
            return [{"action": "buy", "size": "100%", "why": "s004"}]
        if sadx[i] < 25 and (fk[i] > 70 or fd[i] > 70) and fk[i - 1] < fd[i - 1] and prices[i] > ema5[i]:
            return [{"action": "sell", "size": "all", "why": "s004"}]
        return []


class Strategy005(_Port):
    """Strategy005.py (5m): volume > 4 x rolling(150) mean & close < SMA40 & fastD > fastK & RSI>26 & fastD>1 & fisherRSI_norma<5 -> buy;
    sell trigger 'rsi-macd-minusdi': RSI crosses above 74 & MACD<0 & -DI>4 (exit_profit_only). ROI {0:5%,20:4%,40:3%,80:2%,1440:1%}, stoploss -10%."""
    tf_minutes = 5
    ROI = {"0": 0.05, "20": 0.04, "40": 0.03, "80": 0.02, "1440": 0.01}
    FT = {"stoploss": -0.10, "exit_profit_only": True}

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 160: return []
        rsi = self._memo("rsi", lambda: I.rsi(prices, 14)); fk, fd = self._memo("st", lambda: _stochf(highs, lows, prices, 5))
        sma40 = self._memo("sma40", lambda: _sma(prices, 40)); vavg = self._memo("va", lambda: _sma(volumes, 150))
        md = self._memo("md", lambda: I.macd_line(prices, 12, 26)); mdi = self._memo("mdi", lambda: _minus_di(highs, lows, prices, 14))
        if None in (rsi[i], rsi[i - 1], fk[i], fd[i], sma40[i], vavg[i], md[i], mdi[i]): return []
        x = 0.1 * (rsi[i] - 50); fnorm = 50 * ((math.exp(2 * x) - 1) / (math.exp(2 * x) + 1) + 1)
        if volumes[i] > vavg[i] * 4 and prices[i] < sma40[i] and fd[i] > fk[i] and rsi[i] > 26 and fd[i] > 1 and fnorm < 5:
            return [{"action": "buy", "size": "100%", "why": "s005"}]
        if rsi[i - 1] <= 74 < rsi[i] and md[i] < 0 and mdi[i] > 4:
            return [{"action": "sell", "size": "all", "why": "s005"}]
        return []


class Bandtastic(_Port):
    """Bandtastic.py (15m), DEFAULT parameters as in the file: buy when close < lower Bollinger(20, 1 sigma) of the TYPICAL price (rsi/mfi/ema
    filters disabled); sell when MFI > 46 AND close > upper Bollinger(20, 2 sigma). ROI {0:.162, 69:.097, 229:.061, 566:0}, stoploss -34.5%,
    trailing +1% once +5.8% profit."""
    tf_minutes = 15
    ROI = {"0": 0.162, "69": 0.097, "229": 0.061, "566": 0}
    FT = {"stoploss": -0.345, "trail_pos": 0.01, "trail_off": 0.058, "trail_only_off": False}

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 60: return []
        bl1 = self._memo("bl1", lambda: _bb_typical(highs, lows, prices, 20, 1.0)[0]); bu2 = self._memo("bu2", lambda: _bb_typical(highs, lows, prices, 20, 2.0)[2])
        mfi = self._memo("mfi", lambda: _mfi(highs, lows, prices, volumes, 14))
        if None in (bl1[i], bu2[i], mfi[i]): return []
        if prices[i] < bl1[i]:
            return [{"action": "buy", "size": "100%", "why": "bandtastic"}]
        if mfi[i] > 46 and prices[i] > bu2[i]:
            return [{"action": "sell", "size": "all", "why": "bandtastic"}]
        return []


class SwingHighToSky(_Port):
    """SwingHighToSky.py (15m): buy when CCI(72) < -175 AND RSI(36) < 90; sell when CCI(66) > -106 AND RSI(45) > 88. ROI {0:.27058, 33:.0853, 64:.04093, 244:0}, stoploss -34.3%."""
    tf_minutes = 15
    ROI = {"0": 0.27058, "33": 0.0853, "64": 0.04093, "244": 0}
    FT = {"stoploss": -0.34338}

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 100: return []
        cb = self._memo("cb", lambda: I.cci(highs, lows, prices, 72)); rb = self._memo("rb", lambda: I.rsi(prices, 36))
        cs = self._memo("cs", lambda: I.cci(highs, lows, prices, 66)); rs = self._memo("rs", lambda: I.rsi(prices, 45))
        if None in (cb[i], rb[i], cs[i], rs[i]): return []
        if cb[i] < -175 and rb[i] < 90:
            return [{"action": "buy", "size": "100%", "why": "shts"}]
        if cs[i] > -106 and rs[i] > 88:
            return [{"action": "sell", "size": "all", "why": "shts"}]
        return []


class MabStra(_Port):
    """mabStra.py (4h), DEFAULT params: buy when mojoSMA(7)/fastSMA(14) and fastSMA(14)/slowSMA(28) both in (0.29497, 2.25446);
    sell when fast/mojo and slow/fast both in (2.81436, 1.54593)  [min > max in the file, so this exit can never fire]. ROI as Heracles, stoploss -12.8%."""
    tf_minutes = 240
    ROI = {"0": 0.598, "644": 0.166, "3269": 0.115, "7289": 0}
    FT = {"stoploss": -0.128}

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 40: return []
        m, f, s = (self._memo(k, lambda n=n: _sma(prices, n)) for k, n in (("m", 7), ("f", 14), ("s", 28)))
        if None in (m[i], f[i], s[i]): return []
        if 0.29497 < m[i] / f[i] < 2.25446 and 0.29497 < f[i] / s[i] < 2.25446:
            return [{"action": "buy", "size": "100%", "why": "mab"}]
        return []


def _plus_minus_di(highs, lows, closes, n=14):
    m = len(closes); pdi, mdi = [None] * m, [None] * m
    tr = [highs[0] - lows[0]] + [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])) for i in range(1, m)]
    pdm = [0.0] + [(highs[i] - highs[i - 1]) if (highs[i] - highs[i - 1] > lows[i - 1] - lows[i] and highs[i] - highs[i - 1] > 0) else 0.0 for i in range(1, m)]
    mdm = [0.0] + [(lows[i - 1] - lows[i]) if (lows[i - 1] - lows[i] > highs[i] - highs[i - 1] and lows[i - 1] - lows[i] > 0) else 0.0 for i in range(1, m)]
    if m <= n: return pdi, mdi
    st, sp, sm = sum(tr[1:n + 1]), sum(pdm[1:n + 1]), sum(mdm[1:n + 1])
    pdi[n], mdi[n] = (100 * sp / st, 100 * sm / st) if st else (0.0, 0.0)
    for i in range(n + 1, m):
        st = st - st / n + tr[i]; sp = sp - sp / n + pdm[i]; sm = sm - sm / n + mdm[i]
        pdi[i], mdi[i] = (100 * sp / st, 100 * sm / st) if st else (0.0, 0.0)
    return pdi, mdi


def _macd_hist(closes, f=12, s=26, sig=9):
    ml = I.macd_line(closes, f, s); sg = I.macd_signal(closes, f, s, sig)
    return [a - b if a is not None and b is not None else None for a, b in zip(ml, sg)]


def _aggregate(ts, o, h, l, c, v, secs):
    out, cur, key = [], None, None
    for t, oo, hh, ll, cc, vv in zip(ts, o, h, l, c, v):
        k = t - t % secs
        if k != key:
            if cur: out.append(cur)
            cur, key = [k, oo, hh, ll, cc, vv], k
        else:
            cur[2] = max(cur[2], hh); cur[3] = min(cur[3], ll); cur[4] = cc; cur[5] += vv
    if cur: out.append(cur)
    return out


class TrendRider(_Port):
    """TrendRiderStrategy.py (1h): six entry modes (trend pullback, EMA50 bounce, RSI bounce, EMA cross, BB bounce, MACD reversal), each
    guarded by ADX/DI/OBV/volume/BTC-RSI filters and the daily EMA200; then confirm_trade_entry requires a confidence score >= 5 (>= 6 in a
    bear regime); exits: RSI>78, bearish EMA cross + MACD, trend-broken, early-warning, plus custom_exit cascade (2h -1.5%, 4h <0, 8h <+0.5%,
    16h <+1%, 24h time exit). ROI {0:.229,124:.136,290:.044,764:0}, stoploss -6%, trailing 3% after +5% (only_offset), CooldownPeriod 20.
    Hyperopt params as in the file: ema 9/16, rsi period 16, pullback 30-65, bounce 35, ADX 18, volume factor 0.7, rsi_exit 78.
    Needs .ts (candle open times) and .btc_rsi (BTC 1h RSI14 aligned, already delayed one candle). 4h / 1d informative series are built from the
    coin's own hourly candles and merged only once closed (Freqtrade merge_informative_pair semantics).
    Deviations: protections StoplossGuard / MaxDrawdown are cross-pair (not modelled); fear&greed and funding are the file's static defaults."""
    tf_minutes = 60
    ROI = {"0": 0.229, "124": 0.136, "290": 0.044, "764": 0}
    FT = {"stoploss": -0.06, "trail_pos": 0.03, "trail_off": 0.05, "trail_only_off": True, "cooldown_bars": 20}
    P = dict(ema_fast=9, ema_slow=16, rsi_period=16, rsi_pullback_low=30, rsi_pullback_high=65, rsi_bounce=35, adx_thr=18, vol_factor=0.7, rsi_exit=78)

    @staticmethod
    def custom_exit(age, profit):
        if age >= 2 and profit < -0.015: return True
        if age >= 4 and profit < 0: return True
        if age >= 8 and profit < 0.005: return True
        if age >= 16 and profit < 0.01: return True
        return age >= 24

    def ft(self):
        cfg = super().ft(); cfg["custom_exit"] = self.custom_exit
        return cfg

    def _prep(self, prices, volumes, highs, lows):
        n = len(prices); P = self.P
        opens = [prices[0]] + prices[:-1]
        d = {"open": opens}
        for k in (P["ema_fast"], P["ema_slow"], 50, 200): d[f"ema{k}"] = I.ema(prices, k)
        d["rsi"] = I.rsi(prices, P["rsi_period"]); d["adx"] = I.adx(highs, lows, prices, 14)
        d["pdi"], d["mdi"] = _plus_minus_di(highs, lows, prices, 14)
        d["mh"] = _macd_hist(prices)
        d["bbl"] = I.bollinger_lower(prices, 20, 2.0); d["bbu"] = I.bollinger_upper(prices, 20, 2.0); d["bbm"] = I.bollinger_mid(prices, 20)
        vema = I.ema(volumes, 20); d["vr"] = [v / (e + 1e-10) if e is not None else None for v, e in zip(volumes, vema)]
        obv, run = [], 0.0
        for i in range(n):
            if i > 0: run += volumes[i] if prices[i] > prices[i - 1] else (-volumes[i] if prices[i] < prices[i - 1] else 0.0)
            obv.append(run)
        d["obv"], d["obv_ema"] = obv, I.ema(obv, 20)
        wid = [((u - l_) / (m + 1e-10)) if None not in (u, l_, m) else None for u, l_, m in zip(d["bbu"], d["bbl"], d["bbm"])]
        d["bbw"], d["bbw_sma"] = wid, _sma([w if w is not None else 0 for w in wid], 50)
        # 4h and 1d informative (merged only when closed)
        ts = self.ts
        a4 = _aggregate(ts, opens, highs, lows, prices, volumes, 14400)
        c4 = [x[4] for x in a4]; h4 = [x[2] for x in a4]; l4 = [x[3] for x in a4]
        e50, e200, adx4 = I.ema(c4, 50), I.ema(c4, 200), I.adx(h4, l4, c4, 14)
        idx4 = {x[0]: k for k, x in enumerate(a4)}
        ad = _aggregate(ts, opens, highs, lows, prices, volumes, 86400)
        cd = [x[4] for x in ad]; e200d = I.ema(cd, 200); idxd = {x[0]: k for k, x in enumerate(ad)}
        bull4, adx4h, e200_1d = [], [], []
        for t in ts:
            k = idx4.get((t // 14400) * 14400 - 14400)
            if k is not None and e50[k] is not None and e200[k] is not None:
                bull4.append(1 if (c4[k] > e200[k] and e50[k] > e200[k]) else 0); adx4h.append(adx4[k] or 0)
            else:
                bull4.append(0); adx4h.append(0)
            kd = idxd.get((t // 86400) * 86400 - 86400)
            e200_1d.append(e200d[kd] if kd is not None else None)
        d["bull4"], d["adx4"], d["e200d"] = bull4, adx4h, e200_1d
        return d

    def _confidence(self, d, i):
        s = 0.0; r = d["rsi"][i]
        if 35 < r < 60: s += 1.5
        a = d["adx"][i] or 0
        s += 2.5 if a > 30 else (1.5 if a > self.P["adx_thr"] else 0)
        v = d["vr"][i] or 0
        s += 2.5 if v > 1.5 else (1.5 if v > 1.0 else 0)
        if d["mh"][i] is not None and d["mh"][i] > 0:
            s += 1.5
            if d["mh"][i - 1] is not None and d["mh"][i] > d["mh"][i - 1]: s += 0.5
        if d["obv"][i] > (d["obv_ema"][i] or 0): s += 1.5
        br = self.btc_rsi[i]
        if br is not None and 40 < br < 70: s += 1.5
        if d["bull4"][i] == 1 and d["adx4"][i] > 20: s += 1.5
        bl, bu = d["bbl"][i], d["bbu"][i]
        if bl and bu and bu > bl and (self._c["close"][i] - bl) / (bu - bl) < 0.35: s += 1.0
        if (d["pdi"][i] or 0) - (d["mdi"][i] or 0) > 10: s += 1.0
        s += 1.0 + 1.0                                   # static fear&greed = 50 (neutral) and funding = 0 (healthy) from the file's defaults
        num = max(1, min(10, round(s * 10 / 17.5)))
        return num

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 260: return []
        self._c["close"] = prices
        d = self._memo("d", lambda: self._prep(prices, volumes, highs, lows))
        P = self.P; rs = d["rsi"]; ef, es = d[f"ema{P['ema_fast']}"], d[f"ema{P['ema_slow']}"]
        need = (rs[i], rs[i - 1], d["adx"][i], d["pdi"][i], d["mdi"][i], d["mh"][i], d["mh"][i - 1], d["bbl"][i], d["vr"][i], d["ema50"][i], d["ema200"][i], ef[i], es[i], d["obv_ema"][i], ef[i - 1], es[i - 1])
        if None in need or self.btc_rsi[i] is None: return []
        c, o, lo = prices[i], d["open"][i], lows[i]
        bull = c > d["ema200"][i] and d["ema50"][i] > d["ema200"][i]
        btc_ok = self.btc_rsi[i] > 35
        entries = []
        pull = lo <= es[i] * 1.02 and c > es[i] and c > o
        if bull and pull and P["rsi_pullback_low"] < rs[i] < P["rsi_pullback_high"] and d["adx"][i] > P["adx_thr"] and d["vr"][i] > P["vol_factor"] \
                and d["pdi"][i] > d["mdi"][i] and d["obv"][i] > d["obv_ema"][i] and btc_ok and rs[i] < 70 and (d["e200d"][i] is None or c > d["e200d"][i]):
            entries.append("trend_pullback")
        if bull and lo <= d["ema50"][i] * 1.01 and c > d["ema50"][i] and c > o and 30 < rs[i] < 50 and d["adx"][i] > 20 and d["vr"][i] > 1.0 \
                and d["mh"][i] > d["mh"][i - 1] and btc_ok and rs[i] < 70:
            entries.append("ema50_bounce")
        if c > d["ema200"][i] and rs[i - 1] < P["rsi_bounce"] < rs[i] and c > d["bbl"][i] and c > o and d["vr"][i] > 0.8 and d["obv"][i] > d["obv_ema"][i] and btc_ok:
            entries.append("rsi_bounce")
        if ef[i] > es[i] and ef[i - 1] <= es[i - 1] and 40 < rs[i] < 75 and c > d["ema200"][i] and d["vr"][i] > 0.5 and btc_ok:
            entries.append("ema_crossover")
        if c <= d["bbl"][i] * 1.005 and c > o and rs[i] < 45 and d["vr"][i] > 0.7 and d["adx"][i] > 18 and btc_ok:
            entries.append("bb_bounce")
        if d["mh"][i] > 0 >= d["mh"][i - 1] and c > d["ema50"][i] and c > d["ema200"][i] and 40 < rs[i] < 60 and d["adx"][i] > 15 and d["vr"][i] > 0.8 and btc_ok:
            entries.append("macd_reversal")
        if entries:
            conf = self._confidence(d, i)
            bear = c < d["ema200"][i] and d["ema50"][i] < d["ema200"][i]
            a = d["adx"][i]; hv = d["bbw"][i] is not None and d["bbw_sma"][i] and d["bbw"][i] > d["bbw_sma"][i] * 1.5
            regime_bear = not (a < 20) and not (bull and c > d["ema200"][i])           # "Trending Bear" branch of _get_market_regime
            if conf >= (6 if regime_bear else 5):
                return [{"action": "buy", "size": "100%", "why": entries[-1]}]
            return []
        e = ef[i]; sl = es[i]
        if rs[i] > P["rsi_exit"]: return [{"action": "sell", "size": "all", "why": "rsi_overbought"}]
        if e < sl and ef[i - 1] >= es[i - 1] and d["mh"][i] < 0 and rs[i] > 50: return [{"action": "sell", "size": "all", "why": "ema_bearish"}]
        if c < d["ema200"][i] * 0.99 and prices[i - 1] >= d["ema200"][i - 1]: return [{"action": "sell", "size": "all", "why": "trend_broken"}]
        if c < d["ema200"][i] * 0.995 and rs[i] > 72 and d["mh"][i] < d["mh"][i - 1]: return [{"action": "sell", "size": "all", "why": "early_warning"}]
        return []


def _bb_close(closes, n, stds):
    mid = _sma(closes, n); lower = [None] * len(closes)
    for i in range(n - 1, len(closes)):
        w = closes[i - n + 1:i + 1]; m = mid[i]
        lower[i] = m - stds * math.sqrt(sum((v - m) ** 2 for v in w) / (n - 1))
    return mid, lower


class BinHV45(_Port):
    """berlinguyinca/BinHV45.py (1m): BB(40,2) on close; buy when lower[-1]>0, |mid-lower| > close*7/1000, |close-close[-1]| > close*17/1000,
    |close-low| < |mid-lower|*25/1000, close < lower[-1] and close <= close[-1]. No exit signal. ROI {0:1.25%}, stoploss -5%."""
    tf_minutes = 1
    ROI = {"0": 0.0125}
    FT = {"stoploss": -0.05}
    K = (7, 17, 25)

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 45: return []
        mid, lower = self._memo("bb", lambda: _bb_close(prices, 40, 2))
        if lower[i - 1] is None or mid[i] is None or lower[i] is None: return []
        bbd = abs(mid[i] - lower[i]); cd = abs(prices[i] - prices[i - 1]); tail = abs(prices[i] - lows[i])
        if lower[i - 1] > 0 and bbd > prices[i] * self.K[0] / 1000 and cd > prices[i] * self.K[1] / 1000 and tail < bbd * self.K[2] / 1000 \
                and prices[i] < lower[i - 1] and prices[i] <= prices[i - 1]:
            return [{"action": "buy", "size": "100%", "why": "binhv45"}]
        return []


class CombinedBinHAndCluc(_Port):
    """berlinguyinca/CombinedBinHAndCluc.py (5m): BinHV45 (constants 0.008, 0.0175, 0.25) OR ClucMay2018 (close < EMA50, close < 0.985 x typical-price
    BB(20,2) lower, volume < 20 x mean30[-1]) -> buy; close > typical BB middle -> sell (exit_profit_only). ROI {0:5%}, stoploss -5%."""
    tf_minutes = 5
    ROI = {"0": 0.05}
    FT = {"stoploss": -0.05, "exit_profit_only": True}

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 60: return []
        mid, lower = self._memo("bb40", lambda: _bb_close(prices, 40, 2))
        bl, bm, _ = self._memo("bbt", lambda: _bb_typical(highs, lows, prices, 20, 2.0))
        ema50 = self._memo("ema50", lambda: I.ema(prices, 50)); vm = self._memo("vm", lambda: _sma(volumes, 30))
        if None in (lower[i - 1], mid[i], lower[i], bl[i], bm[i], ema50[i], vm[i - 1]): return []
        bbd = abs(mid[i] - lower[i]); cd = abs(prices[i] - prices[i - 1]); tail = abs(prices[i] - lows[i])
        binh = lower[i - 1] > 0 and bbd > prices[i] * 0.008 and cd > prices[i] * 0.0175 and tail < bbd * 0.25 and prices[i] < lower[i - 1] and prices[i] <= prices[i - 1]
        cluc = prices[i] < ema50[i] and prices[i] < 0.985 * bl[i] and volumes[i] < vm[i - 1] * 20
        if binh or cluc:
            return [{"action": "buy", "size": "100%", "why": "binh_cluc"}]
        if prices[i] > bm[i]:
            return [{"action": "sell", "size": "all", "why": "binh_cluc"}]
        return []


class ClucMay72018(_Port):
    """berlinguyinca/ClucMay72018.py (5m): close < EMA50 & close < 0.985 x typical-price BB(20,2) lower & volume < 20 x mean30[-1] -> buy;
    close > BB middle -> sell. ROI {0:1%}, stoploss -5%."""
    tf_minutes = 5
    ROI = {"0": 0.01}
    FT = {"stoploss": -0.05}

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 60: return []
        bl, bm, _ = self._memo("bbt", lambda: _bb_typical(highs, lows, prices, 20, 2.0))
        ema50 = self._memo("ema50", lambda: I.ema(prices, 50)); vm = self._memo("vm", lambda: _sma(volumes, 30))
        if None in (bl[i], bm[i], ema50[i], vm[i - 1]): return []
        if prices[i] < ema50[i] and prices[i] < 0.985 * bl[i] and volumes[i] < vm[i - 1] * 20:
            return [{"action": "buy", "size": "100%", "why": "cluc"}]
        if prices[i] > bm[i]:
            return [{"action": "sell", "size": "all", "why": "cluc"}]
        return []


class BbandRsi(_Port):
    """berlinguyinca/BbandRsi.py (1h): RSI14 < 30 and close < typical-price BB(20,2) lower -> buy; RSI14 > 70 -> sell. ROI {0:10%}, stoploss -25%."""
    tf_minutes = 60
    ROI = {"0": 0.10}
    FT = {"stoploss": -0.25}

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 40: return []
        bl, _, _ = self._memo("bbt", lambda: _bb_typical(highs, lows, prices, 20, 2.0)); rsi = self._memo("rsi", lambda: I.rsi(prices, 14))
        if None in (bl[i], rsi[i]): return []
        if rsi[i] < 30 and prices[i] < bl[i]: return [{"action": "buy", "size": "100%", "why": "bbandrsi"}]
        if rsi[i] > 70: return [{"action": "sell", "size": "all", "why": "bbandrsi"}]
        return []


def _ema_of(x, n):
    return I.ema(x, n)


class ADXMomentum(_Port):
    """ADXMomentum.py (1h): ADX14>25 & MOM14>0 & +DI(25)>25 & +DI>-DI -> buy; ADX>25 & MOM<0 & -DI(25)>25 & +DI<-DI -> sell. ROI {0:1%}, stoploss -25%."""
    tf_minutes = 60; ROI = {"0": 0.01}; FT = {"stoploss": -0.25}

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 40: return []
        adx = self._memo("adx", lambda: I.adx(highs, lows, prices, 14)); pdi, mdi = self._memo("di", lambda: _plus_minus_di(highs, lows, prices, 25))
        if None in (adx[i], pdi[i], mdi[i]): return []
        mom = prices[i] - prices[i - 14]
        if adx[i] > 25 and mom > 0 and pdi[i] > 25 and pdi[i] > mdi[i]: return [{"action": "buy", "size": "100%", "why": "adxmom"}]
        if adx[i] > 25 and mom < 0 and mdi[i] > 25 and pdi[i] < mdi[i]: return [{"action": "sell", "size": "all", "why": "adxmom"}]
        return []


class AverageStrategy(_Port):
    """AverageStrategy.py (4h): EMA8 crosses above EMA21 -> buy; EMA21 crosses above EMA8 -> sell. ROI {0:50%}, stoploss -20%."""
    tf_minutes = 240; ROI = {"0": 0.5}; FT = {"stoploss": -0.20}

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 30: return []
        a, b = self._memo("e8", lambda: I.ema(prices, 8)), self._memo("e21", lambda: I.ema(prices, 21))
        if None in (a[i], b[i], a[i - 1], b[i - 1]): return []
        if a[i - 1] <= b[i - 1] and a[i] > b[i]: return [{"action": "buy", "size": "100%", "why": "avg"}]
        if b[i - 1] <= a[i - 1] and b[i] > a[i]: return [{"action": "sell", "size": "all", "why": "avg"}]
        return []


class AwesomeMacd(_Port):
    """AwesomeMacd.py (1h): MACD>0 & AwesomeOscillator>0 & AO[-1]<0 -> buy; MACD<0 & AO<0 & AO[-1]>0 -> sell. ROI {0:10%}, stoploss -25%."""
    tf_minutes = 60; ROI = {"0": 0.10}; FT = {"stoploss": -0.25}

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 50: return []
        med = self._memo("med", lambda: [(h + l) / 2 for h, l in zip(highs, lows)])
        s5, s34 = self._memo("s5", lambda: _sma(med, 5)), self._memo("s34", lambda: _sma(med, 34)); macd = self._memo("macd", lambda: I.macd_line(prices, 12, 26))
        if None in (s5[i], s34[i], s5[i - 1], s34[i - 1], macd[i]): return []
        ao, ao1 = s5[i] - s34[i], s5[i - 1] - s34[i - 1]
        if macd[i] > 0 and ao > 0 and ao1 < 0: return [{"action": "buy", "size": "100%", "why": "awesome"}]
        if macd[i] < 0 and ao < 0 and ao1 > 0: return [{"action": "sell", "size": "all", "why": "awesome"}]
        return []


class CofiBitStrategy(_Port):
    """CofiBitStrategy.py (5m): open < EMA5(low) & fastK crosses above fastD & fastK<25 & fastD<25 & ADX>25 -> buy; open >= EMA5(high) OR fastK/fastD crosses above 75 -> sell.
    ROI {0:10%,20:7%,30:6%,40:5%}, stoploss -25%."""
    tf_minutes = 5; ROI = {"0": 0.10, "20": 0.07, "30": 0.06, "40": 0.05}; FT = {"stoploss": -0.25}

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 50: return []
        fk, fd = self._memo("st", lambda: _stochf(highs, lows, prices, 5, 3)); adx = self._memo("adx", lambda: I.adx(highs, lows, prices, 14))
        el, eh = self._memo("el", lambda: I.ema(lows, 5)), self._memo("eh", lambda: I.ema(highs, 5)); o = self._memo("open", lambda: [prices[0]] + list(prices[:-1]))
        if None in (fk[i], fd[i], fk[i - 1], fd[i - 1], adx[i], el[i], eh[i]): return []
        if o[i] < el[i] and fk[i - 1] <= fd[i - 1] and fk[i] > fd[i] and fk[i] < 25 and fd[i] < 25 and adx[i] > 25:
            return [{"action": "buy", "size": "100%", "why": "cofibit"}]
        if o[i] >= eh[i] or (fk[i - 1] <= 75 < fk[i]) or (fd[i - 1] <= 75 < fd[i]):
            return [{"action": "sell", "size": "all", "why": "cofibit"}]
        return []


class EMASkipPump(_Port):
    """EMASkipPump.py (5m): volume < 20 x mean30[-1] & close < EMA5 & close < EMA12 & close == 12-bar low & close <= typical-price BB lower -> buy;
    close > EMA5 & > EMA12 & >= 12-bar high & >= BB upper -> sell. ROI {0:10%}, stoploss -5%."""
    tf_minutes = 5; ROI = {"0": 0.10}; FT = {"stoploss": -0.05}

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 40: return []
        e5, e12 = self._memo("e5", lambda: I.ema(prices, 5)), self._memo("e12", lambda: I.ema(prices, 12))
        bl, _, bu = self._memo("bb", lambda: _bb_typical(highs, lows, prices, 20, 2.0)); vm = self._memo("vm", lambda: _sma(volumes, 30))
        if None in (e5[i], e12[i], bl[i], bu[i], vm[i - 1]): return []
        lo12, hi12 = min(prices[i - 11:i + 1]), max(prices[i - 11:i + 1])
        if volumes[i] < vm[i - 1] * 20 and prices[i] < e5[i] and prices[i] < e12[i] and prices[i] == lo12 and prices[i] <= bl[i]:
            return [{"action": "buy", "size": "100%", "why": "skippump"}]
        if prices[i] > e5[i] and prices[i] > e12[i] and prices[i] >= hi12 and prices[i] >= bu[i]:
            return [{"action": "sell", "size": "all", "why": "skippump"}]
        return []


class LowBB(_Port):
    """Low_BB.py (1m): close <= 0.98 x BB(20,2) lower (close-based bands: the file overwrites the typical-price bands) -> buy; no exit signal.
    ROI {0:90%,1:5%,10:4%,15:50%}, stoploss -1.5%."""
    tf_minutes = 1; ROI = {"0": 0.9, "1": 0.05, "10": 0.04, "15": 0.5}; FT = {"stoploss": -0.015}

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 25: return []
        _, lower = self._memo("bb", lambda: _bb_close(prices, 20, 2))
        if lower[i] is not None and prices[i] <= 0.98 * lower[i]: return [{"action": "buy", "size": "100%", "why": "lowbb"}]
        return []


def _macd_pair(closes):
    return I.macd_line(closes, 12, 26), I.macd_signal(closes, 12, 26, 9)


class MACDStrategy(_Port):
    """MACDStrategy.py (5m): MACD > signal & CCI(14) <= -48 -> buy; MACD < signal & CCI >= 687 -> sell. ROI {0:5%,20:4%,30:3%,60:1%}, stoploss -30%."""
    tf_minutes = 5; ROI = {"0": 0.05, "20": 0.04, "30": 0.03, "60": 0.01}; FT = {"stoploss": -0.30}

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 50: return []
        m, sg = self._memo("m", lambda: _macd_pair(prices)); cci = self._memo("cci", lambda: I.cci(highs, lows, prices, 14))
        if None in (m[i], sg[i], cci[i]): return []
        if m[i] > sg[i] and cci[i] <= -48: return [{"action": "buy", "size": "100%", "why": "macd"}]
        if m[i] < sg[i] and cci[i] >= 687: return [{"action": "sell", "size": "all", "why": "macd"}]
        return []


class MACDStrategyCrossed(_Port):
    """MACDStrategy_crossed.py (5m): MACD crosses above signal & CCI <= -50 -> buy; crosses below & CCI >= 100 -> sell. Same ROI/stoploss as MACDStrategy."""
    tf_minutes = 5; ROI = {"0": 0.05, "20": 0.04, "30": 0.03, "60": 0.01}; FT = {"stoploss": -0.30}

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 50: return []
        m, sg = self._memo("m", lambda: _macd_pair(prices)); cci = self._memo("cci", lambda: I.cci(highs, lows, prices, 14))
        if None in (m[i], sg[i], m[i - 1], sg[i - 1], cci[i]): return []
        if m[i - 1] <= sg[i - 1] and m[i] > sg[i] and cci[i] <= -50: return [{"action": "buy", "size": "100%", "why": "macdx"}]
        if m[i - 1] >= sg[i - 1] and m[i] < sg[i] and cci[i] >= 100: return [{"action": "sell", "size": "all", "why": "macdx"}]
        return []


class Quickie(_Port):
    """Quickie.py (5m): ADX>30 & TEMA9 < BB(20) middle & TEMA rising & SMA200 > close -> buy; ADX>70 & TEMA > middle & TEMA falling -> sell.
    ROI {0:15% (after 10min):.. i.e. {10:15%,15:6%,30:3%,100:1%}}, stoploss -25%. (The file computes 'sma_50' with period 200; kept.)"""
    tf_minutes = 5; ROI = {"10": 0.15, "15": 0.06, "30": 0.03, "100": 0.01}; FT = {"stoploss": -0.25}

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 210: return []
        adx = self._memo("adx", lambda: I.adx(highs, lows, prices, 14)); tema = self._memo("tema", lambda: _tema(prices, 9))
        mid, _ = self._memo("bb", lambda: _bb_close(prices, 20, 2)); s200 = self._memo("s200", lambda: _sma(prices, 200))
        if None in (adx[i], tema[i], tema[i - 1], mid[i], s200[i]): return []
        if adx[i] > 30 and tema[i] < mid[i] and tema[i] > tema[i - 1] and s200[i] > prices[i]: return [{"action": "buy", "size": "100%", "why": "quickie"}]
        if adx[i] > 70 and tema[i] > mid[i] and tema[i] < tema[i - 1]: return [{"action": "sell", "size": "all", "why": "quickie"}]
        return []


class Scalp(_Port):
    """Scalp.py (1m): open < EMA5(low) & ADX>30 & fastK<30 & fastD<30 & fastK crosses above fastD -> buy; open >= EMA5(high) OR fastK/fastD crosses above 70 -> sell.
    ROI {0:1%}, stoploss -4%."""
    tf_minutes = 1; ROI = {"0": 0.01}; FT = {"stoploss": -0.04}

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 50: return []
        fk, fd = self._memo("st", lambda: _stochf(highs, lows, prices, 5, 3)); adx = self._memo("adx", lambda: I.adx(highs, lows, prices, 14))
        el, eh = self._memo("el", lambda: I.ema(lows, 5)), self._memo("eh", lambda: I.ema(highs, 5)); o = self._memo("open", lambda: [prices[0]] + list(prices[:-1]))
        if None in (fk[i], fd[i], fk[i - 1], fd[i - 1], adx[i], el[i], eh[i]): return []
        if o[i] < el[i] and adx[i] > 30 and fk[i] < 30 and fd[i] < 30 and fk[i - 1] <= fd[i - 1] and fk[i] > fd[i]:
            return [{"action": "buy", "size": "100%", "why": "scalp"}]
        if o[i] >= eh[i] or (fk[i - 1] <= 70 < fk[i]) or (fd[i - 1] <= 70 < fd[i]):
            return [{"action": "sell", "size": "all", "why": "scalp"}]
        return []


class Simple(_Port):
    """Simple.py (5m): MACD>0 & MACD>signal & BB(12,2) upper rising & RSI7>70 -> buy; RSI7>80 -> sell. ROI {0:1%}, stoploss -25%."""
    tf_minutes = 5; ROI = {"0": 0.01}; FT = {"stoploss": -0.25}

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 40: return []
        m, sg = self._memo("m", lambda: _macd_pair(prices)); rsi = self._memo("rsi", lambda: I.rsi(prices, 7))
        up = self._memo("up", lambda: [(mm + 2 * math.sqrt(sum((v - mm) ** 2 for v in prices[k - 11:k + 1]) / 11)) if (mm := _sma(prices, 12)[k]) is not None else None for k in range(len(prices))]) if False else self._memo("up", lambda: self._upper12(prices))
        if None in (m[i], sg[i], rsi[i], up[i], up[i - 1]): return []
        if m[i] > 0 and m[i] > sg[i] and up[i] > up[i - 1] and rsi[i] > 70: return [{"action": "buy", "size": "100%", "why": "simple"}]
        if rsi[i] > 80: return [{"action": "sell", "size": "all", "why": "simple"}]
        return []

    @staticmethod
    def _upper12(prices):
        mid = _sma(prices, 12); out = [None] * len(prices)
        for k in range(11, len(prices)):
            m = mid[k]; out[k] = m + 2 * math.sqrt(sum((v - m) ** 2 for v in prices[k - 11:k + 1]) / 11)
        return out


class SmoothScalp(_Port):
    """SmoothScalp.py (1m): open < EMA5(low) & ADX>30 & MFI<30 & fastK<30 & fastD<30 & fastK crosses above fastD & CCI(14)<-150 -> buy;
    (open >= EMA5(high) OR fastK/fastD crosses above 70) AND CCI>150 -> sell. ROI {0:1%}, stoploss -50%."""
    tf_minutes = 1; ROI = {"0": 0.01}; FT = {"stoploss": -0.50}

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 50: return []
        fk, fd = self._memo("st", lambda: _stochf(highs, lows, prices, 5, 3)); adx = self._memo("adx", lambda: I.adx(highs, lows, prices, 14))
        mfi = self._memo("mfi", lambda: _mfi(highs, lows, prices, volumes, 14)); cci = self._memo("cci", lambda: I.cci(highs, lows, prices, 14))
        el, eh = self._memo("el", lambda: I.ema(lows, 5)), self._memo("eh", lambda: I.ema(highs, 5)); o = self._memo("open", lambda: [prices[0]] + list(prices[:-1]))
        if None in (fk[i], fd[i], fk[i - 1], fd[i - 1], adx[i], mfi[i], cci[i], el[i], eh[i]): return []
        if o[i] < el[i] and adx[i] > 30 and mfi[i] < 30 and fk[i] < 30 and fd[i] < 30 and fk[i - 1] <= fd[i - 1] and fk[i] > fd[i] and cci[i] < -150:
            return [{"action": "buy", "size": "100%", "why": "smoothscalp"}]
        if (o[i] >= eh[i] or (fk[i - 1] <= 70 < fk[i]) or (fd[i - 1] <= 70 < fd[i])) and cci[i] > 150:
            return [{"action": "sell", "size": "all", "why": "smoothscalp"}]
        return []


class CMCWinner(_Port):
    """CMCWinner.py (15m): previous candle CCI(14) < -100 & MFI(14) < 20 & CMO(14) < -50 -> buy; previous CCI > 100 & MFI > 80 & CMO > 50 -> sell.
    ROI {0:5%,20:3%,30:2%,40:0}, stoploss -5%. CMO computed as 2*RSI-100 (Wilder), the TA-Lib definition."""
    tf_minutes = 15; ROI = {"0": 0.05, "20": 0.03, "30": 0.02, "40": 0.0}; FT = {"stoploss": -0.05}

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 40: return []
        cci = self._memo("cci", lambda: I.cci(highs, lows, prices, 14)); mfi = self._memo("mfi", lambda: _mfi(highs, lows, prices, volumes, 14)); rsi = self._memo("rsi", lambda: I.rsi(prices, 14))
        if None in (cci[i - 1], mfi[i - 1], rsi[i - 1]): return []
        cmo = 2 * rsi[i - 1] - 100
        if cci[i - 1] < -100 and mfi[i - 1] < 20 and cmo < -50: return [{"action": "buy", "size": "100%", "why": "cmc"}]
        if cci[i - 1] > 100 and mfi[i - 1] > 80 and cmo > 50: return [{"action": "sell", "size": "all", "why": "cmc"}]
        return []


class ReinforcedAverageStrategy(_Port):
    """ReinforcedAverageStrategy.py (4h): EMA8 crosses above EMA21 AND close > SMA50 of 48h candles (12 x timeframe, the last CLOSED candle) -> buy;
    EMA21 crosses above EMA8 -> sell. ROI {0:50%}, stoploss -20%. Needs .ts."""
    tf_minutes = 240; ROI = {"0": 0.5}; FT = {"stoploss": -0.20}

    def _long_sma(self, prices, highs, lows, volumes):
        secs = self.tf_minutes * 60 * 12
        agg = _aggregate(self.ts, prices, highs, lows, prices, volumes, secs)
        cl = [x[4] for x in agg]; sm = _sma(cl, 50); idx = {x[0]: k for k, x in enumerate(agg)}
        out = []
        for t in self.ts:
            k = idx.get((t // secs) * secs - secs)
            out.append(sm[k] if k is not None else None)
        return out

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 30: return []
        a, b = self._memo("e8", lambda: I.ema(prices, 8)), self._memo("e21", lambda: I.ema(prices, 21))
        ls = self._memo("ls", lambda: self._long_sma(prices, highs, lows, volumes))
        if None in (a[i], b[i], a[i - 1], b[i - 1]): return []
        if a[i - 1] <= b[i - 1] and a[i] > b[i] and ls[i] is not None and prices[i] > ls[i]: return [{"action": "buy", "size": "100%", "why": "reinforced_avg"}]
        if b[i - 1] <= a[i - 1] and b[i] > a[i]: return [{"action": "sell", "size": "all", "why": "reinforced_avg"}]
        return []


class FAdxSmaStrategy(_Port):
    """futures/FAdxSmaStrategy.py (1h) LONG SIDE ONLY (the file also shorts): ADX14 > 30 & SMA12 crosses above SMA48 -> buy; ADX < 30 -> exit.
    ROI {0:5%,30:10%,60:7.5%}, stoploss -5%."""
    tf_minutes = 60; ROI = {"0": 0.05, "30": 0.10, "60": 0.075}; FT = {"stoploss": -0.05}

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 60: return []
        adx = self._memo("adx", lambda: I.adx(highs, lows, prices, 14)); s, l = self._memo("s", lambda: _sma(prices, 12)), self._memo("l", lambda: _sma(prices, 48))
        if None in (adx[i], s[i], l[i], s[i - 1], l[i - 1]): return []
        if adx[i] > 30 and s[i - 1] <= l[i - 1] and s[i] > l[i]: return [{"action": "buy", "size": "100%", "why": "fadxsma"}]
        if adx[i] < 30: return [{"action": "sell", "size": "all", "why": "fadxsma"}]
        return []


class TrendFollowingStrategy(_Port):
    """futures/TrendFollowingStrategy.py (5m) LONG SIDE ONLY: close crosses above EMA20 & OBV rising -> buy; close crosses below EMA20 & OBV rising -> exit.
    ROI {0:15%,30:10%,60:5%}, stoploss -26.5%, trailing 5% after +10%."""
    tf_minutes = 5; ROI = {"0": 0.15, "30": 0.10, "60": 0.05}; FT = {"stoploss": -0.265, "trail_pos": 0.05, "trail_off": 0.10, "trail_only_off": False}

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 30: return []
        ema = self._memo("ema", lambda: I.ema(prices, 20))
        def _obv():
            out, run = [0.0], 0.0
            for k in range(1, len(prices)):
                run += volumes[k] if prices[k] > prices[k - 1] else (-volumes[k] if prices[k] < prices[k - 1] else 0.0); out.append(run)
            return out
        obv = self._memo("obv", _obv)
        if ema[i] is None or ema[i - 1] is None: return []
        if prices[i] > ema[i] and prices[i - 1] <= ema[i - 1] and obv[i] > obv[i - 1]: return [{"action": "buy", "size": "100%", "why": "trendf"}]
        if prices[i] < ema[i] and prices[i - 1] >= ema[i - 1] and obv[i] > obv[i - 1]: return [{"action": "sell", "size": "all", "why": "trendf"}]
        return []


class TechnicalExampleStrategy(_Port):
    """TechnicalExampleStrategy.py (5m): Chaikin money flow(21) < 0 -> buy; > 0 -> sell. ROI {0:1%}, stoploss -5%."""
    tf_minutes = 5; ROI = {"0": 0.01}; FT = {"stoploss": -0.05}

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 30: return []
        def cmf():
            mfv = [(((c - l) - (h - c)) / (h - l) if h != l else 0.0) * v for c, l, h, v in zip(prices, lows, highs, volumes)]
            out = [None] * len(prices); sm, sv = 0.0, 0.0
            for k in range(len(prices)):
                sm += mfv[k]; sv += volumes[k]
                if k >= 21: sm -= mfv[k - 21]; sv -= volumes[k - 21]
                if k >= 20: out[k] = sm / sv if sv else 0.0
            return out
        c = self._memo("cmf", cmf)
        if c[i] is None: return []
        if c[i] < 0: return [{"action": "buy", "size": "100%", "why": "cmf"}]
        if c[i] > 0: return [{"action": "sell", "size": "all", "why": "cmf"}]
        return []


class ASDTSRockwellTrading(_Port):
    """ASDTSRockwellTrading.py (5m): MACD > 0 & MACD > signal -> buy; MACD < signal -> sell. ROI {0:5%,20:4%,30:3%,60:1%}, stoploss -30%."""
    tf_minutes = 5; ROI = {"0": 0.05, "20": 0.04, "30": 0.03, "60": 0.01}; FT = {"stoploss": -0.30}

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 50: return []
        m, sg = self._memo("m", lambda: _macd_pair(prices))
        if m[i] is None or sg[i] is None: return []
        if m[i] > 0 and m[i] > sg[i]: return [{"action": "buy", "size": "100%", "why": "asdts"}]
        if m[i] < sg[i]: return [{"action": "sell", "size": "all", "why": "asdts"}]
        return []


class TDSequentialStrategy(_Port):
    """TDSequentialStrategy.py (1h): 9 consecutive closes below the close 4 bars earlier with a bar-8/9 low undercutting the lows of bars 6/7 -> buy
    (exceed_low & seq_buy>8); exit on exceed_high OR 9 consecutive closes above (seq_sell>8). ROI {0:500%} (never), stoploss -5%."""
    tf_minutes = 60; ROI = {"0": 5.0}; FT = {"stoploss": -0.05}

    def _td(self, prices, highs, lows):
        n = len(prices); sb, ss = [0] * n, [0] * n
        for k in range(4, n):
            sb[k] = sb[k - 1] + 1 if prices[k] < prices[k - 4] else 0
            ss[k] = ss[k - 1] + 1 if prices[k] > prices[k - 4] else 0
        el, eh = [False] * n, [False] * n
        for k in range(n):
            b = sb[k]
            if b == 8: el[k] = lows[k] < lows[k - 2] or lows[k] < lows[k - 1]
            if b > 8:
                el[k] = lows[k] < lows[k - 3 - (b - 9)] or lows[k] < lows[k - 2 - (b - 9)]
                if b == 9: el[k] = el[k] or el[k - 1]
            s = ss[k]
            if s == 8: eh[k] = highs[k] > highs[k - 2] or highs[k] > highs[k - 1]
            if s > 8:
                eh[k] = highs[k] > highs[k - 3 - (s - 9)] or highs[k] > highs[k - 2 - (s - 9)]
                if s == 9: eh[k] = eh[k] or eh[k - 1]
        return sb, ss, el, eh

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 30: return []
        sb, ss, el, eh = self._memo("td", lambda: self._td(prices, highs, lows))
        if el[i] and sb[i] > 8: return [{"action": "buy", "size": "100%", "why": "td"}]
        if eh[i] or ss[i] > 8: return [{"action": "sell", "size": "all", "why": "td"}]
        return []


class ReinforcedSmoothScalp(_Port):
    """ReinforcedSmoothScalp.py (1m), file defaults: MFI<22 & fastD<30 & ADX>32 & fastK crosses above fastD & close > SMA50 of 5-minute candles -> buy;
    open > EMA5(high) & CCI20>183 & fastD>79 & fastK>70 -> sell. ROI {0:2%}, stoploss -10%. Needs .ts."""
    tf_minutes = 1; ROI = {"0": 0.02}; FT = {"stoploss": -0.10}

    def _rsma(self, prices, highs, lows, volumes):
        opens = self._memo("open", lambda: [prices[0]] + list(prices[:-1]))
        agg = _aggregate(self.ts, opens, highs, lows, prices, volumes, 300); cl = [x[4] for x in agg]; sm = _sma(cl, 50); idx = {x[0]: k for k, x in enumerate(agg)}
        out = []
        for t in self.ts:
            k = idx.get((t // 300) * 300 - 300)
            out.append(sm[k] if k is not None else None)
        return out

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 60: return []
        fk, fd = self._memo("st", lambda: _stochf(highs, lows, prices, 5, 3)); adx = self._memo("adx", lambda: I.adx(highs, lows, prices, 14))
        mfi = self._memo("mfi", lambda: _mfi(highs, lows, prices, volumes, 14)); cci = self._memo("cci", lambda: I.cci(highs, lows, prices, 20))
        eh = self._memo("eh", lambda: I.ema(highs, 5)); o = self._memo("open", lambda: [prices[0]] + list(prices[:-1]))
        rs = self._memo("rs", lambda: self._rsma(prices, highs, lows, volumes))
        if None in (fk[i], fd[i], fk[i - 1], fd[i - 1], adx[i], mfi[i], cci[i], eh[i], rs[i]): return []
        if mfi[i] < 22 and fd[i] < 30 and adx[i] > 32 and fk[i - 1] <= fd[i - 1] and fk[i] > fd[i] and rs[i] < prices[i]:
            return [{"action": "buy", "size": "100%", "why": "rss"}]
        if o[i] > eh[i] and cci[i] > 183 and fd[i] > 79 and fk[i] > 70: return [{"action": "sell", "size": "all", "why": "rss"}]
        return []


class MultiRSI(_Port):
    """MultiRSI.py (5m): SMA5 >= SMA200 & RSI14 < RSI14 of 40-minute candles - 20 -> buy; RSI > RSI of 10-minute candles AND > RSI of 40-minute candles -> sell.
    Higher-timeframe RSI comes from the last CLOSED resampled candle. ROI {0:1%}, stoploss -5%. Needs .ts."""
    tf_minutes = 5; ROI = {"0": 0.01}; FT = {"stoploss": -0.05}

    def _htf_rsi(self, prices, highs, lows, volumes, mult):
        secs = 300 * mult; opens = self._memo("open", lambda: [prices[0]] + list(prices[:-1]))
        agg = _aggregate(self.ts, opens, highs, lows, prices, volumes, secs); r = I.rsi([x[4] for x in agg], 14); idx = {x[0]: k for k, x in enumerate(agg)}
        out = []
        for t in self.ts:
            k = idx.get((t // secs) * secs - secs)
            out.append(r[k] if k is not None else None)
        return out

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 210: return []
        s5, s200 = self._memo("s5", lambda: _sma(prices, 5)), self._memo("s200", lambda: _sma(prices, 200)); rsi = self._memo("rsi", lambda: I.rsi(prices, 14))
        r2 = self._memo("r2", lambda: self._htf_rsi(prices, highs, lows, volumes, 2)); r8 = self._memo("r8", lambda: self._htf_rsi(prices, highs, lows, volumes, 8))
        if None in (s5[i], s200[i], rsi[i], r2[i], r8[i]): return []
        if s5[i] >= s200[i] and rsi[i] < r8[i] - 20: return [{"action": "buy", "size": "100%", "why": "multirsi"}]
        if rsi[i] > r2[i] and rsi[i] > r8[i]: return [{"action": "sell", "size": "all", "why": "multirsi"}]
        return []


class UniversalMACD(_Port):
    """UniversalMACD.py (5m): umacd = EMA12/EMA26 - 1; buy when -0.01416 <= umacd <= -0.01176; the sell band is (-0.00707 .. -0.02323) written min>max in the
    file, so pandas between() is never true and there is NO exit signal. ROI {0:21.3%,27:9.9%,60:3%,164:0}, stoploss -31.8%."""
    tf_minutes = 5; ROI = {"0": 0.213, "27": 0.099, "60": 0.03, "164": 0}; FT = {"stoploss": -0.318}

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 40: return []
        a, b = self._memo("e12", lambda: I.ema(prices, 12)), self._memo("e26", lambda: I.ema(prices, 26))
        if a[i] is None or b[i] is None: return []
        u = a[i] / b[i] - 1
        if -0.01416 <= u <= -0.01176: return [{"action": "buy", "size": "100%", "why": "umacd"}]
        return []


class PowerTower(_Port):
    """PowerTower.py (5m) AS WRITTEN: close[0] > close[2]**3.849 & close[1] > close[3]**3.849 & close[2] > close[4]**3.849 -> buy; any close[k] < close[k+2]**3.798 -> sell.
    The comparison only makes sense at the price scale it was fitted on (prices below 1); on higher-priced coins the buy never fires. ROI {0:21.3%,39:4.8%,56:2.9%,159:0}, stoploss -28.8%."""
    tf_minutes = 5; ROI = {"0": 0.213, "39": 0.048, "56": 0.029, "159": 0}; FT = {"stoploss": -0.288}

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 6: return []
        p = prices
        try:
            if p[i] > p[i - 2] ** 3.849 and p[i - 1] > p[i - 3] ** 3.849 and p[i - 2] > p[i - 4] ** 3.849: return [{"action": "buy", "size": "100%", "why": "pt"}]
            if p[i] < p[i - 2] ** 3.798 or p[i - 1] < p[i - 3] ** 3.798 or p[i - 2] < p[i - 4] ** 3.798: return [{"action": "sell", "size": "all", "why": "pt"}]
        except OverflowError:
            return []
        return []


class Diamond(_Port):
    """Diamond.py (5m) AS WRITTEN: high[-7] crosses above volume x 0.942 -> buy; high[-10] crosses below low x 1.184 -> sell (raw price vs raw volume: a hyperopt artifact).
    ROI {0:24.2%,13:4.4%,51:2%,170:0}, stoploss -27.1%, trailing 1.1% after +5.4%."""
    tf_minutes = 5; ROI = {"0": 0.242, "13": 0.044, "51": 0.02, "170": 0}; FT = {"stoploss": -0.271, "trail_pos": 0.011, "trail_off": 0.054, "trail_only_off": False}

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 15: return []
        if highs[i - 7] > volumes[i] * 0.942 and highs[i - 8] <= volumes[i - 1] * 0.942: return [{"action": "buy", "size": "100%", "why": "diamond"}]
        if highs[i - 10] < lows[i] * 1.184 and highs[i - 11] >= lows[i - 1] * 1.184: return [{"action": "sell", "size": "all", "why": "diamond"}]
        return []


class FReinforcedStrategy(_Port):
    """futures/FReinforcedStrategy.py (5m) LONG SIDE ONLY: close > SMA50 of 60-minute candles (last closed) & EMA8 crosses above EMA21 -> buy; ADX14 < 30 -> exit.
    ROI {0:5%,30:10%,60:7.5%}, stoploss -5%. Needs .ts."""
    tf_minutes = 5; ROI = {"0": 0.05, "30": 0.10, "60": 0.075}; FT = {"stoploss": -0.05}

    def _sma60(self, prices, highs, lows, volumes):
        secs = 3600; opens = self._memo("open", lambda: [prices[0]] + list(prices[:-1]))
        agg = _aggregate(self.ts, opens, highs, lows, prices, volumes, secs); sm = _sma([x[4] for x in agg], 50); idx = {x[0]: k for k, x in enumerate(agg)}
        out = []
        for t in self.ts:
            k = idx.get((t // secs) * secs - secs)
            out.append(sm[k] if k is not None else None)
        return out

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 40: return []
        a, b = self._memo("e8", lambda: I.ema(prices, 8)), self._memo("e21", lambda: I.ema(prices, 21)); adx = self._memo("adx", lambda: I.adx(highs, lows, prices, 14))
        ls = self._memo("ls", lambda: self._sma60(prices, highs, lows, volumes))
        if None in (a[i], b[i], a[i - 1], b[i - 1], adx[i]): return []
        if ls[i] is not None and prices[i] > ls[i] and a[i - 1] <= b[i - 1] and a[i] > b[i]: return [{"action": "buy", "size": "100%", "why": "freinf"}]
        if adx[i] < 30: return [{"action": "sell", "size": "all", "why": "freinf"}]
        return []


class FSampleStrategy(_Port):
    """futures/FSampleStrategy.py (1h) LONG SIDE ONLY: RSI14 crosses above 30 & TEMA9 <= BB(typical,20,2) middle & TEMA rising -> buy;
    RSI crosses above 70 & TEMA > middle & TEMA falling -> exit. ROI {0:100%}, stoploss -5%."""
    tf_minutes = 60; ROI = {"0": 1.0}; FT = {"stoploss": -0.05}

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 40: return []
        rsi = self._memo("rsi", lambda: I.rsi(prices, 14)); tema = self._memo("tema", lambda: _tema(prices, 9)); _, mid, _u = self._memo("bb", lambda: _bb_typical(highs, lows, prices, 20, 2.0))
        if None in (rsi[i], rsi[i - 1], tema[i], tema[i - 1], mid[i]): return []
        if rsi[i - 1] <= 30 < rsi[i] and tema[i] <= mid[i] and tema[i] > tema[i - 1]: return [{"action": "buy", "size": "100%", "why": "fsample"}]
        if rsi[i - 1] <= 70 < rsi[i] and tema[i] > mid[i] and tema[i] < tema[i - 1]: return [{"action": "sell", "size": "all", "why": "fsample"}]
        return []


class VolatilitySystem(_Port):
    """futures/VolatilitySystem.py (1h) LONG SIDE ONLY, 1x (the file uses 2x leverage and adds a second entry): the change of the last CLOSED 3-hour close
    exceeding 2 x ATR14 of 3h candles (previous value) -> buy; the opposite move exceeding it -> exit. ROI 100x, stoploss -100% (i.e. none). Needs .ts."""
    tf_minutes = 60; ROI = {"0": 100.0}; FT = {"stoploss": -1.0}

    def _feat(self, prices, highs, lows, volumes):
        secs = 10800; opens = self._memo("open", lambda: [prices[0]] + list(prices[:-1]))
        agg = _aggregate(self.ts, opens, highs, lows, prices, volumes, secs)
        c, h, l = [x[4] for x in agg], [x[2] for x in agg], [x[3] for x in agg]
        atr = [(a * 2.0 if a is not None else None) for a in I.atr(h, l, c, 14)]
        chg = [None] + [c[k] - c[k - 1] for k in range(1, len(c))]
        idx = {x[0]: k for k, x in enumerate(agg)}
        A, C = [], []
        for t in self.ts:
            k = idx.get((t // secs) * secs - secs)
            A.append(atr[k] if k is not None else None); C.append(chg[k] if k is not None else None)
        return A, C

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 60: return []
        A, C = self._memo("f", lambda: self._feat(prices, highs, lows, volumes))
        if C[i] is None or A[i - 1] is None: return []
        if C[i] > A[i - 1]: return [{"action": "buy", "size": "100%", "why": "volsys"}]
        if -C[i] > A[i - 1]: return [{"action": "sell", "size": "all", "why": "volsys"}]
        return []


class SmoothOperator(_Port):
    """berlinguyinca/SmoothOperator.py (5m): three OR'd dip patterns [5-bar OHLC4 slide then uptick & low<BB mid & CCI[-1]<-100 & RSI[-1]<30] |
    [low<BB mid & CCI<-200 & RSI<30 & MFI<30] | [MFI<10 & CCI<-150 & RSI<MFI], all needing close > close[-1] -> buy; exits: smoothed MFI/RSI/CCI composite
    (TEMA21) >100 and just turned down, OR 9 green candles in a row, OR CCI>200 & RSI>70. ROI {0:10%}, stoploss -5%."""
    tf_minutes = 5; ROI = {"0": 0.10}; FT = {"stoploss": -0.05}

    def _prep(self, prices, volumes, highs, lows):
        o = self._memo("open", lambda: [prices[0]] + list(prices[:-1])); n = len(prices)
        cci, rsi, mfi = I.cci(highs, lows, prices, 20), I.rsi(prices, 14), _mfi(highs, lows, prices, volumes, 14)
        fill = lambda x: [v if v is not None else 0.0 for v in x]
        ms, cs, rs = I.ema(fill(mfi), 11), I.ema(fill(cci), 11), I.ema(fill(rsi), 11)
        comp = [((r or 0) * 1.125 + (m or 0) * 1.125 + (c or 0)) / 3 for r, m, c in zip(rs, ms, cs)]
        comp = _tema(comp, 21)
        avg = [(a + b + c + d) / 4 for a, b, c, d in zip(o, highs, lows, prices)]
        mid, _ = _bb_close(prices, 20, 2)
        return {"cci": cci, "rsi": rsi, "mfi": mfi, "comp": comp, "avg": avg, "mid": mid, "open": o}

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 90: return []
        d = self._memo("d", lambda: self._prep(prices, volumes, highs, lows)); c, r, m, cp, av, mid, o = d["cci"], d["rsi"], d["mfi"], d["comp"], d["avg"], d["mid"], d["open"]
        if None in (c[i], c[i - 1], r[i], r[i - 1], m[i], mid[i], mid[i - 1], cp[i], cp[i - 1], cp[i - 2], cp[i - 3]): return []
        p1 = av[i - 5] > av[i - 4] > av[i - 3] > av[i - 2] > av[i - 1] < av[i] and lows[i - 1] < mid[i - 1] and c[i - 1] < -100 and r[i - 1] < 30
        p2 = lows[i] < mid[i] and c[i] < -200 and r[i] < 30 and m[i] < 30
        p3 = m[i] < 10 and c[i] < -150 and r[i] < m[i]
        if (p1 or p2 or p3) and prices[i] > prices[i - 1]: return [{"action": "buy", "size": "100%", "why": "smoothop"}]
        e1 = cp[i] > 100 and cp[i - 1] > cp[i] and cp[i - 2] < cp[i - 1] and cp[i - 3] < cp[i - 2]
        e2 = all(o[i - k] < prices[i - k] for k in range(9))
        e3 = c[i] > 200 and r[i] > 70
        if e1 or e2 or e3: return [{"action": "sell", "size": "all", "why": "smoothop"}]
        return []


class FSupertrendStrategy(Supertrend):
    """futures/FSupertrendStrategy.py (1h) LONG SIDE ONLY: same three-up / three-down supertrend rules and parameters as Supertrend.py; ROI {0:10%,30:75%,60:5%,120:2.5%},
    stoploss -26.5%, trailing 5% after +10%."""
    ROI = {"0": 0.10, "30": 0.75, "60": 0.05, "120": 0.025}
    FT = {"stoploss": -0.265, "trail_pos": 0.05, "trail_off": 0.10, "trail_only_off": False}


class FOttStrategy(_Port):
    """futures/FOttStrategy.py (1h) LONG SIDE ONLY: Optimized Trend Tracker (VAR = CMO-adaptive average, alpha 2/3, 1.4% bands, OTT shifted 2 bars);
    VAR crosses above OTT -> buy; ADX14 > 60 -> exit. ROI {0:10%,30:75%,60:5%,120:2.5%}, stoploss -26.5%, trailing 5% after +10%.
    Deviation: the file's vectorised repeated passes are implemented as the single sequential update they converge to."""
    tf_minutes = 60; ROI = {"0": 0.10, "30": 0.75, "60": 0.05, "120": 0.025}
    FT = {"stoploss": -0.265, "trail_pos": 0.05, "trail_off": 0.10, "trail_only_off": False}

    def _ott(self, prices):
        n = len(prices); pct = 1.4; alpha = 2 / 3
        ud = [0.0] + [max(prices[k] - prices[k - 1], 0.0) for k in range(1, n)]; dd = [0.0] + [max(prices[k - 1] - prices[k], 0.0) for k in range(1, n)]
        var = [0.0] * n; cmo = [0.0] * n
        for k in range(9, n):
            u, d = sum(ud[k - 8:k + 1]), sum(dd[k - 8:k + 1])
            cmo[k] = abs((u - d) / (u + d)) if (u + d) else 0.0
        for k in range(2, n):
            var[k] = alpha * cmo[k] * prices[k] + (1 - alpha * cmo[k]) * var[k - 1]
        ls, ss, dr = [0.0] * n, [1e18] * n, [1] * n; mt = [0.0] * n; ott = [None] * n
        for k in range(1, n):
            fark = var[k] * pct * 0.01; nl, ns = var[k] - fark, var[k] + fark
            ls[k] = max(nl, ls[k - 1]) if var[k] > ls[k - 1] else nl
            ss[k] = min(ns, ss[k - 1]) if var[k] < ss[k - 1] else ns
            xl = var[k - 1] > ls[k - 1] and var[k] < ls[k - 1]; xs = var[k - 1] < ss[k - 1] and var[k] > ss[k - 1]
            dr[k] = 1 if xs else (-1 if xl else dr[k - 1])
            mt[k] = ls[k] if dr[k] == 1 else ss[k]
        raw = [(mt[k] * (200 + pct) / 200) if var[k] > mt[k] else (mt[k] * (200 - pct) / 200) for k in range(n)]
        for k in range(2, n): ott[k] = raw[k - 2]
        return var, ott

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 30: return []
        var, ott = self._memo("ott", lambda: self._ott(prices)); adx = self._memo("adx", lambda: I.adx(highs, lows, prices, 14))
        if ott[i] is None or ott[i - 1] is None or adx[i] is None: return []
        if var[i - 1] <= ott[i - 1] and var[i] > ott[i]: return [{"action": "buy", "size": "100%", "why": "ott"}]
        if adx[i] > 60: return [{"action": "sell", "size": "all", "why": "ott"}]
        return []


# ---------------------------------------------------------------- Hummingbot directional controllers (3-minute candles, LONG side)
def _bbp(closes, n=100, std=2.0):
    mid = _sma(closes, n); out = [None] * len(closes)
    for k in range(n - 1, len(closes)):
        w = closes[k - n + 1:k + 1]; m = mid[k]; sd = math.sqrt(sum((v - m) ** 2 for v in w) / n)      # pandas_ta bbands: population std
        lo, up = m - std * sd, m + std * sd
        out[k] = (closes[k] - lo) / (up - lo) if up > lo else None
    return out


class _HB(_Port):
    """Hummingbot DirectionalTradingControllerBase defaults on 3-minute candles: triple barrier = stop loss 3%, take profit 2% (limit-maker in the original),
    time limit 45 minutes; cooldown 5 minutes. Long side only. The take-profit exit is charged the taker rate here (conservative)."""
    tf_minutes = 3; ROI = {"0": 0.02}
    FT = {"stoploss": -0.03, "cooldown_bars": 2}

    def ft(self):
        cfg = super().ft(); cfg["custom_exit"] = lambda age, profit: age >= 15
        return cfg


class HBBollingerV1(_HB):
    """controllers/directional_trading/bollinger_v1.py: BBP(100, 2 std) < 0 -> long."""
    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 110: return []
        b = self._memo("bbp", lambda: _bbp(prices, 100, 2.0))
        return [{"action": "buy", "size": "100%", "why": "hb_bb"}] if b[i] is not None and b[i] < 0 else []


class HBMacdBBV1(_HB):
    """controllers/directional_trading/macd_bb_v1.py: BBP(100,2) < 0 & MACD hist > 0 & MACD < 0 with MACD(21,42,9) -> long."""
    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 110: return []
        b = self._memo("bbp", lambda: _bbp(prices, 100, 2.0)); ml = self._memo("ml", lambda: I.macd_line(prices, 21, 42)); sg = self._memo("sg", lambda: I.macd_signal(prices, 21, 42, 9))
        if b[i] is None or ml[i] is None or sg[i] is None: return []
        return [{"action": "buy", "size": "100%", "why": "hb_macdbb"}] if b[i] < 0 and (ml[i] - sg[i]) > 0 and ml[i] < 0 else []


class HBSupertrendV1(_HB):
    """controllers/directional_trading/supertrend_v1.py: pandas_ta SuperTrend(length 20, multiplier 4) direction up AND |close - supertrend|/close < 1% -> long."""
    @staticmethod
    def _st(highs, lows, closes, n=20, mult=4.0):
        m = len(closes); tr = [highs[0] - lows[0]] + [max(highs[k] - lows[k], abs(highs[k] - closes[k - 1]), abs(lows[k] - closes[k - 1])) for k in range(1, m)]
        atr = [None] * m
        if m > n:
            a = sum(tr[1:n + 1]) / n; atr[n] = a
            for k in range(n + 1, m): a = (a * (n - 1) + tr[k]) / n; atr[k] = a
        up, lo, dr, st = [None] * m, [None] * m, [1] * m, [None] * m
        for k in range(n, m):
            hl2 = (highs[k] + lows[k]) / 2; bu, bl = hl2 + mult * atr[k], hl2 - mult * atr[k]
            if k > n and closes[k] > up[k - 1]: dr[k] = 1
            elif k > n and closes[k] < lo[k - 1]: dr[k] = -1
            elif k > n: dr[k] = dr[k - 1]
            if k > n and dr[k] == 1 and bl < lo[k - 1]: bl = lo[k - 1]
            if k > n and dr[k] == -1 and bu > up[k - 1]: bu = up[k - 1]
            up[k], lo[k] = bu, bl; st[k] = bl if dr[k] == 1 else bu
        return dr, st

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 40: return []
        dr, st = self._memo("st", lambda: self._st(highs, lows, prices))
        if st[i] is None: return []
        return [{"action": "buy", "size": "100%", "why": "hb_st"}] if dr[i] == 1 and abs(prices[i] - st[i]) / prices[i] < 0.01 else []


def _crossed_above(a, b, i):
    """qtpylib.crossed_above(a, b) at bar i; b may be a number."""
    bi = b if isinstance(b, (int, float)) else b[i]; bp = b if isinstance(b, (int, float)) else b[i - 1]
    return None not in (a[i], a[i - 1], bi, bp) and a[i] > bi and a[i - 1] <= bp


def _crossed_below(a, b, i):
    bi = b if isinstance(b, (int, float)) else b[i]; bp = b if isinstance(b, (int, float)) else b[i - 1]
    return None not in (a[i], a[i - 1], bi, bp) and a[i] < bi and a[i - 1] >= bp


class HourBased(_Port):
    """HourBasedStrategy.py (1h): buy when UTC hour in [4, 24]; sell when hour in [22, 21] (an empty range: never). Pure ROI table + stoploss.
    ROI {0:.528, 169:.113, 528:.089, 1837:0}, stoploss -10%. Needs self.ts (set by the benchmark runner). Deviation: none."""
    tf_minutes = 60
    ROI = {"0": 0.528, "169": 0.113, "528": 0.089, "1837": 0}
    FT = {"stoploss": -0.10}

    def signals_at(self, i, prices, volumes, highs, lows):
        hr = (self.ts[i] // 3600) % 24
        return [{"action": "buy", "size": "100%", "why": "hour"}] if 4 <= hr <= 24 else []


class Hlhb(_Port):
    """hlhb.py (4h, long only as in the file): RSI(10) of (open+close)/2 crosses above 50 AND EMA5 crosses above EMA10 AND ADX>25 -> buy;
    the mirror crossing below -> sell. ROI {0:.6225, 703:.2187, 2849:.0363, 5520:0}, stoploss -32.11%, trailing +1.17% after +1.86%
    (only after offset), exit signal on, ignore_roi_if_entry_signal. Deviation: (open+close)/2 uses previous close as open; the ROI/entry-signal
    override and the same-bar double crossing are kept exactly."""
    tf_minutes = 240
    ROI = {"0": 0.6225, "703": 0.2187, "2849": 0.0363, "5520": 0}
    FT = {"stoploss": -0.3211, "trail_pos": 0.0117, "trail_off": 0.0186, "trail_only_off": True}

    def _feat(self, prices, highs, lows):
        o = [prices[0]] + list(prices[:-1]); hl2 = [(a + b) / 2 for a, b in zip(o, prices)]
        return I.rsi(hl2, 10), I.ema(prices, 5), I.ema(prices, 10), I.adx(highs, lows, prices, 14)

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 40: return []
        rsi, e5, e10, adx = self._memo("f", lambda: self._feat(prices, highs, lows))
        if adx[i] is None or adx[i] <= 25: return []
        if _crossed_above(rsi, 50, i) and _crossed_above(e5, e10, i):
            return [{"action": "buy", "size": "100%", "why": "hlhb"}]
        if _crossed_below(rsi, 50, i) and _crossed_below(e5, e10, i):
            return [{"action": "sell", "size": "all", "why": "hlhb"}]
        return []


class Strategy001CustomExit(_Port):
    """Strategy001_custom_exit.py (5m): EMA20 crosses above EMA50 & Heikin-Ashi close > EMA20 & green HA bar -> buy; EMA50 crosses above EMA100 &
    HA close < EMA20 & red HA bar -> sell (as written in the file); custom_exit: RSI(14) > 70 and profit > 0 -> sell. ROI {0:.05, 20:.04, 30:.03, 60:.01},
    stoploss -10%, exit_profit_only. Deviation: HA open seeded from (open+close)/2 with open ~ previous close; custom_exit checked on the closing bar
    via the signal path (RSI needs the bar's own value), so it is evaluated at signal time, not per tick."""
    tf_minutes = 5
    ROI = {"0": 0.05, "20": 0.04, "30": 0.03, "60": 0.01}
    FT = {"stoploss": -0.10, "exit_profit_only": True}

    def _feat(self, prices, highs, lows):
        o = [prices[0]] + list(prices[:-1])
        hac = [(a + h + l + c) / 4 for a, h, l, c in zip(o, highs, lows, prices)]
        hao = [(o[0] + prices[0]) / 2]
        for k in range(1, len(prices)): hao.append((hao[k - 1] + hac[k - 1]) / 2)
        return I.ema(prices, 20), I.ema(prices, 50), I.ema(prices, 100), hac, hao, I.rsi(prices, 14)

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 110: return []
        e20, e50, e100, hac, hao, rsi = self._memo("f", lambda: self._feat(prices, highs, lows))
        if _crossed_above(e20, e50, i) and hac[i] > e20[i] and hao[i] < hac[i]:
            return [{"action": "buy", "size": "100%", "why": "s001ce"}]
        if _crossed_above(e50, e100, i) and hac[i] < e20[i] and hao[i] > hac[i]:
            return [{"action": "sell", "size": "all", "why": "s001ce"}]
        return []

    def ft(self):
        return super().ft()


class PsarStop(_Port):
    """CustomStoplossWithPSAR.py (1h): entry when SAR < previous SAR (falling SAR, i.e. the file's admitted-nonsensical placeholder); the real content is a
    trailing stop AT the SAR value (custom_stoploss). ROI none, hard stoploss -20%. Deviation: the SAR stop is evaluated on bar closes as a sell signal
    (close < SAR of the same bar) because the engine's intra-bar stop has no per-bar custom level."""
    tf_minutes = 60
    ROI = {}
    FT = {"stoploss": -0.20}

    def ft(self):
        cfg = dict(self.FT); cfg["roi"] = []; return cfg

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 30: return []
        sar = self._memo("sar", lambda: _sar(highs, lows))
        if sar[i] is None or sar[i - 1] is None: return []
        if prices[i] < sar[i]: return [{"action": "sell", "size": "all", "why": "sar_stop"}]
        if sar[i] < sar[i - 1]: return [{"action": "buy", "size": "100%", "why": "sar"}]
        return []
