"""Public-blueprint strategies, re-implemented faithfully enough to benchmark against ours in the SAME simulator, with
the SAME (real) fees. Sources: freqtrade-strategies (Strategy001/002/003 and the docs' sample RSI strategy: indicators,
thresholds, -10% stop) and standard retail grid/DCA bot defaults. Approximations, stated: candle open ~= previous close
(crypto trades continuously); the time-decaying 'minimal_roi' table is replaced by one fixed take-profit; the hammer pattern
is a wick > 2x body test; no exchange-side order types.  Each class exposes what marketcoach.engine needs:
    .risk .side_rate .costs .signals_at(i, prices, volumes, highs, lows)
"""
import math
from . import indicators as I

FEES = {"order_type": "taker", "taker_fee_bps": 80, "half_spread_bps": 5, "slippage_bps": 3, "maker_fee_bps": 40}


class _Base:
    def __init__(self, fee_bps=None, take_profit=0.03, stop=0.10):
        c = dict(FEES)
        if fee_bps is not None:
            c["taker_fee_bps"] = fee_bps; c["half_spread_bps"] = 0; c["slippage_bps"] = 0
        self.costs = c
        self.side_rate = (c["taker_fee_bps"] + c["half_spread_bps"] + c["slippage_bps"]) / 1e4
        self.risk = {"stop_loss_pct": stop, "take_profit_pct": take_profit}
        self._c = {}

    def _series(self, key, fn):
        if key not in self._c:
            self._c[key] = fn()
        return self._c[key]

    @staticmethod
    def _opens(prices):
        return [prices[0]] + list(prices[:-1])


class FreqtradeStrategy001(_Base):
    """EMA20 crosses above EMA50, Heikin-Ashi close above EMA20, green candle -> buy;
    EMA50 crosses above EMA100, HA close below EMA20, red candle -> sell."""
    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 101:
            return []
        n = len(prices)
        e20, e50, e100 = (self._series(k, lambda p=p: I.ema(prices, p)) for k, p in (("e20", 20), ("e50", 50), ("e100", 100)))
        o = self._opens(prices)
        ha_c = (o[i] + highs[i] + lows[i] + prices[i]) / 4
        if e20[i - 1] <= e50[i - 1] and e20[i] > e50[i] and ha_c > e20[i] and o[i] < prices[i]:
            return [{"action": "buy", "size": "100%", "why": "ft001"}]
        if e50[i - 1] <= e100[i - 1] and e50[i] > e100[i] and ha_c < e20[i] and o[i] > prices[i]:
            return [{"action": "sell", "size": "all", "why": "ft001"}]
        return []


class FreqtradeStrategy002(_Base):
    """RSI<30, slow stochastic K<20, close below lower Bollinger(20,2), hammer candle -> buy;
    Parabolic SAR above close AND Fisher-RSI > 0.3 -> sell."""
    def _sar(self, highs, lows, af0=0.02, afmax=0.2):
        n = len(highs); sar = [None] * n
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

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 40:
            return []
        rsi = self._series("rsi", lambda: I.rsi(prices, 14))
        k = self._series("k", lambda: I.stoch_k(highs, lows, prices, 14))
        d = self._series("d", lambda: I.stoch_d(highs, lows, prices, 14, 3))
        bl = self._series("bl", lambda: I.bollinger_lower(prices, 20, 2))
        sar = self._series("sar", lambda: self._sar(highs, lows))
        o = self._opens(prices)
        body = abs(prices[i] - o[i]); lower_wick = min(o[i], prices[i]) - lows[i]
        hammer = body > 0 and lower_wick > 2 * body
        x = 0.1 * (rsi[i] - 50) if rsi[i] is not None else 0
        fisher = (math.exp(2 * x) - 1) / (math.exp(2 * x) + 1) if rsi[i] is not None else 0
        if None in (rsi[i], d[i], bl[i]):
            return []
        if rsi[i] < 30 and d[i] < 20 and prices[i] < bl[i] and hammer:
            return [{"action": "buy", "size": "100%", "why": "ft002"}]
        if sar[i] is not None and sar[i] > prices[i] and fisher > 0.3:
            return [{"action": "sell", "size": "all", "why": "ft002"}]
        return []


class FreqtradeSampleRSI(_Base):
    """The documentation's sample: RSI crosses above 30 (TEMA below Bollinger middle) -> buy; RSI crosses above 70 -> sell."""
    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 40:
            return []
        rsi = self._series("rsi", lambda: I.rsi(prices, 14))
        mid = self._series("mid", lambda: I.bollinger_mid(prices, 20))
        tema = self._series("tema", lambda: [3 * a - 3 * b + c if None not in (a, b, c) else None for a, b, c in
                                              zip(I.ema(prices, 9), I.ema([x or 0 for x in I.ema(prices, 9)], 9), I.ema([x or 0 for x in I.ema([x or 0 for x in I.ema(prices, 9)], 9)], 9))])
        if None in (rsi[i], rsi[i - 1], mid[i]):
            return []
        if rsi[i - 1] <= 30 < rsi[i] and tema[i] is not None and tema[i] < mid[i]:
            return [{"action": "buy", "size": "100%", "why": "ft_sample"}]
        if rsi[i - 1] <= 70 < rsi[i]:
            return [{"action": "sell", "size": "all", "why": "ft_sample"}]
        return []


class JesseIFR2(_Base):
    """Jesse example 'IFR2' (RSI-2 pullback inside an Ichimoku-cloud uptrend): buy when RSI(2) < 10 while the close is above
    both cloud spans (crypto-tuned Ichimoku 20/30/120, displacement 60) and a trend filter holds (Hilbert Trendmode approximated
    by close > SMA100); exit when the close exceeds the highest high of the previous two candles."""
    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 200:
            return []
        rsi2 = self._series("rsi2", lambda: I.rsi(prices, 2))
        ten = self._series("ten", lambda: I.ichimoku_mid(highs, lows, 20))
        kij = self._series("kij", lambda: I.ichimoku_mid(highs, lows, 30))
        spb = self._series("spb", lambda: I.ichimoku_mid(highs, lows, 120))
        sma = self._series("sma", lambda: I.sma(prices, 100))
        j = i - 60                                          # displaced cloud: values computed 60 bars ago
        if None in (rsi2[i], ten[j], kij[j], spb[j], sma[i]):
            return []
        span_a = (ten[j] + kij[j]) / 2
        if rsi2[i] < 10 and prices[i] > span_a and prices[i] > spb[j] and prices[i] > sma[i]:
            return [{"action": "buy", "size": "100%", "why": "ifr2"}]
        if prices[i] > max(highs[i - 1], highs[i - 2]):
            return [{"action": "sell", "size": "all", "why": "ifr2"}]
        return []


class JesseDualThrust(_Base):
    """Jesse example 'Dual Thrust' (long side): breakout above open + k1 x max(HH-LC, HC-LL) over 21 bars; exit on the opposite
    breakout (open - k2 x range); 2 x ATR stop distance."""
    def __init__(self, k1=0.71, k2=0.67, n=21, **kw):
        super().__init__(**kw)
        self.k1, self.k2, self.n = k1, k2, n

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < self.n + 2:
            return []
        o = self._opens(prices)
        hh, ll = max(highs[i - self.n:i]), min(lows[i - self.n:i])
        hc, lc = max(prices[i - self.n:i]), min(prices[i - self.n:i])
        rng = max(hh - lc, hc - ll)
        up, dn = o[i] + self.k1 * rng, o[i] - self.k2 * rng
        if prices[i] > up:
            return [{"action": "buy", "size": "100%", "why": "dual_thrust"}]
        if prices[i] < dn:
            return [{"action": "sell", "size": "all", "why": "dual_thrust"}]
        return []


class SupertrendRSIADX(_Base):
    """Popular TradingView combo: SuperTrend(10, 3) flips bullish AND RSI(14) > 50 AND ADX(14) > 20 -> buy; SuperTrend flips
    bearish -> sell. (The filters are what the community adds to stop whipsaws in sideways markets.)"""
    def _supertrend(self, highs, lows, closes, n=10, mult=3.0):
        atr = I.atr(highs, lows, closes, n)
        m = len(closes); st = [None] * m; up = [None] * m; dn = [None] * m; dirn = [1] * m
        for i in range(m):
            if atr[i] is None:
                continue
            hl2 = (highs[i] + lows[i]) / 2
            bu, bl = hl2 + mult * atr[i], hl2 - mult * atr[i]
            pu, pl = up[i - 1], dn[i - 1]
            up[i] = bu if (pu is None or bu < pu or closes[i - 1] > pu) else pu
            dn[i] = bl if (pl is None or bl > pl or closes[i - 1] < pl) else pl
            if i > 0 and dirn[i - 1] == -1 and closes[i] > up[i - 1] if up[i - 1] else False:
                dirn[i] = 1
            elif i > 0 and dirn[i - 1] == 1 and dn[i - 1] and closes[i] < dn[i - 1]:
                dirn[i] = -1
            else:
                dirn[i] = dirn[i - 1] if i > 0 else 1
            st[i] = dn[i] if dirn[i] == 1 else up[i]
        return dirn

    def signals_at(self, i, prices, volumes, highs, lows):
        if i < 60:
            return []
        d = self._series("st", lambda: self._supertrend(highs, lows, prices))
        rsi = self._series("rsi", lambda: I.rsi(prices, 14))
        adx = self._series("adx", lambda: I.adx(highs, lows, prices, 14))
        if None in (rsi[i], adx[i]):
            return []
        if d[i] == 1 and d[i - 1] == -1 and rsi[i] > 50 and adx[i] > 20:
            return [{"action": "buy", "size": "100%", "why": "supertrend"}]
        if d[i] == -1 and d[i - 1] == 1:
            return [{"action": "sell", "size": "all", "why": "supertrend"}]
        return []
