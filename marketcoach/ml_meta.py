"""Learn from history whether a strategy's trade is worth taking ("meta-labeling").

The strategies stay in charge of WHEN a signal fires. This layer learns, from past trades only,
which market conditions at signal time preceded trades that ended net-positive after real fees,
and vetoes the ones that look like past losers. Pure python (no ML libraries installed): L2-regularised
logistic regression on standardised features, and a small boosted-stumps model.

Honest evaluation only: WALK-FORWARD. The model is trained on trades that had ALREADY CLOSED before each
test window began (no peeking), then scored on the following window; windows roll forward. Reported against
(a) the same trades unfiltered and (b) a random filter that keeps the same share (placebo).
"""
import math, random, time
from . import indicators as I


def coin_features(bars, btc_bars, f1_ts):
    """Per-bar feature vectors for one coin, using ONLY data up to and including that bar."""
    c = [b.close for b in bars]; h = [b.high for b in bars]; l = [b.low for b in bars]; v = [b.volume for b in bars]
    n = len(c)
    sma20, sma50, sma200 = I.sma(c, 20), I.sma(c, 50), I.sma(c, 200)
    rsi14, atr14, adx14 = I.rsi(c, 14), I.atr(h, l, c, 14), I.adx(h, l, c, 14)
    vr = I.vol_ratio(v, 20)
    btc_c = {b.ts: b.close for b in btc_bars}
    btc_ts = [b.ts for b in btc_bars]
    btc_i = {t: i for i, t in enumerate(btc_ts)}
    bc = [b.close for b in btc_bars]
    out = [None] * n
    for i in range(200, n):
        if None in (sma20[i], sma50[i], sma200[i], rsi14[i], atr14[i], adx14[i], vr[i]):
            continue
        r = lambda k: c[i] / c[i - k] - 1
        hi60 = max(h[i - 59:i + 1])
        j = btc_i.get(bars[i].ts)
        if j is None or j < 61:
            continue
        out[i] = [
            r(1), r(5), r(20), r(60),
            rsi14[i] / 100.0, c[i] / sma20[i] - 1, c[i] / sma50[i] - 1, c[i] / sma200[i] - 1,
            atr14[i] / c[i], adx14[i] / 100.0, min(vr[i], 5.0),
            c[i] / hi60 - 1,
            bc[j] / bc[j - 20] - 1, bc[j] / bc[j - 60] - 1,
            1.0 if bars[i].ts in f1_ts else 0.0,
            ((bars[i].ts // 86400) + 4) % 7 / 6.0,      # day of week
        ]
    return out


FEATURE_NAMES = ["ret1", "ret5", "ret20", "ret60", "rsi14", "d_sma20", "d_sma50", "d_sma200", "atr%", "adx", "vol_ratio",
                 "from_60d_high", "btc_ret20", "btc_ret60", "F1_on", "dow"]


# ------------------------------------------------------------------ models
class Standardizer:
    def fit(self, X):
        n, d = len(X), len(X[0])
        self.m = [sum(x[j] for x in X) / n for j in range(d)]
        self.s = [max(1e-9, math.sqrt(sum((x[j] - self.m[j]) ** 2 for x in X) / n)) for j in range(d)]
        return self
    def __call__(self, x):
        return [(x[j] - self.m[j]) / self.s[j] for j in range(len(x))]


class Logistic:
    def __init__(self, l2=0.05, epochs=200, lr=0.1):
        self.l2, self.epochs, self.lr = l2, epochs, lr
    def fit(self, X, y):
        self.sd = Standardizer().fit(X)
        Z = [self.sd(x) for x in X]
        d = len(Z[0]); self.w = [0.0] * d; self.b = math.log((sum(y) + 1) / (len(y) - sum(y) + 1))
        n = len(Z)
        for _ in range(self.epochs):
            gw, gb = [0.0] * d, 0.0
            for z, t in zip(Z, y):
                p = 1 / (1 + math.exp(-max(-30, min(30, self.b + sum(w * a for w, a in zip(self.w, z))))))
                e = p - t
                gb += e
                for j in range(d):
                    gw[j] += e * z[j]
            self.w = [w - self.lr * (g / n + self.l2 * w) for w, g in zip(self.w, gw)]
            self.b -= self.lr * gb / n
        return self
    def predict(self, x):
        z = self.sd(x)
        return 1 / (1 + math.exp(-max(-30, min(30, self.b + sum(w * a for w, a in zip(self.w, z))))))


class Stumps:
    """Gradient-boosted decision stumps (logistic loss), shrinkage 0.1."""
    def __init__(self, rounds=60, lr=0.1, bins=12):
        self.rounds, self.lr, self.bins = rounds, lr, bins
    def fit(self, X, y):
        n, d = len(X), len(X[0])
        self.base = math.log((sum(y) + 1) / (n - sum(y) + 1))
        F = [self.base] * n
        cuts = []
        for j in range(d):
            vals = sorted(x[j] for x in X)
            cuts.append(sorted({vals[int(k * (n - 1) / self.bins)] for k in range(1, self.bins)}))
        self.stumps = []
        for _ in range(self.rounds):
            g = [t - 1 / (1 + math.exp(-max(-30, min(30, f)))) for t, f in zip(y, F)]     # residuals
            best = None
            for j in range(d):
                for c in cuts[j]:
                    sl = sr = 0.0; nl = nr = 0
                    for x, r in zip(X, g):
                        if x[j] <= c: sl += r; nl += 1
                        else: sr += r; nr += 1
                    if nl < 10 or nr < 10:
                        continue
                    gain = sl * sl / nl + sr * sr / nr
                    if best is None or gain > best[0]:
                        best = (gain, j, c, sl / nl, sr / nr)
            if best is None:
                break
            _, j, c, vl, vr_ = best
            self.stumps.append((j, c, vl * 4 * self.lr, vr_ * 4 * self.lr))
            F = [f + (vl if x[j] <= c else vr_) * 4 * self.lr for f, x in zip(F, X)]
        return self
    def predict(self, x):
        f = self.base + sum((a if x[j] <= c else b) for j, c, a, b in self.stumps)
        return 1 / (1 + math.exp(-max(-30, min(30, f))))
