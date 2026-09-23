"""Market data layer: OHLCV bars from Coinbase (cached) or a synthetic generator.

Design mirrors deckcoach: pure stdlib, offline-first. Real data is fetched once
and cached to CSV so backtests are reproducible and don't hammer the API.

A Bar is (ts, open, high, low, close, volume). ts is unix seconds, ascending.
"""
import os, csv, json, math, random, time, datetime, urllib.request, urllib.error
from collections import namedtuple

Bar = namedtuple("Bar", "ts open high low close volume")

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cache")

# Coinbase granularity in seconds -> label
_GRAN = {60: "1m", 300: "5m", 900: "15m", 3600: "1h", 14400: "4h", 21600: "6h", 86400: "1d"}


def _cache_path(product, granularity):
    return os.path.join(CACHE_DIR, f"{product}_{_GRAN.get(granularity, granularity)}.csv")


def save_bars(path, bars):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(Bar._fields)
        for b in bars:
            w.writerow(b)


def load_bars(path):
    out = []
    with open(path) as f:
        r = csv.reader(f)
        next(r, None)  # header
        for row in r:
            out.append(Bar(int(float(row[0])), *[float(x) for x in row[1:]]))
    return out


def fetch_coinbase(product="BTC-USD", granularity=86400, use_cache=True):
    """Fetch OHLCV daily bars. Coinbase returns max ~300 candles/request as
    [time, low, high, open, close, volume]; we normalize to Bar order + ascending."""
    path = _cache_path(product, granularity)
    if use_cache and os.path.exists(path):
        return load_bars(path)
    url = f"https://api.exchange.coinbase.com/products/{product}/candles?granularity={granularity}"
    req = urllib.request.Request(url, headers={"User-Agent": "marketcoach/0.1"})
    raw = json.load(urllib.request.urlopen(req, timeout=15))
    bars = [Bar(int(c[0]), c[3], c[2], c[1], c[4], c[5]) for c in raw]  # -> o,h,l,c,v
    bars.sort(key=lambda b: b.ts)
    save_bars(path, bars)
    return bars


def fetch_recent(product="BTC-USD", granularity=86400):
    """Fresh single-page pull (<=300 bars) for live use. Does NOT touch the cache,
    so the paper loop always sees the newest candles. The last bar may be the
    still-forming current bar; callers must filter to closed bars themselves."""
    url = f"https://api.exchange.coinbase.com/products/{product}/candles?granularity={granularity}"
    req = urllib.request.Request(url, headers={"User-Agent": "marketcoach/0.1"})
    raw = json.load(urllib.request.urlopen(req, timeout=15))
    bars = [Bar(int(c[0]), c[3], c[2], c[1], c[4], c[5]) for c in raw]
    bars.sort(key=lambda b: b.ts)
    return bars


def fetch_coinbase_history(product="BTC-USD", granularity=86400, max_bars=1500, use_cache=True,
                           cache_tag="_hist"):
    """Paginate Coinbase candles backward from now to assemble long history.

    Coinbase returns <=300 candles per request, so we walk backward in windows of
    300*granularity using start/end ISO timestamps until we have max_bars (or the
    API stops returning data). Cached to a distinct '<product>_<label>_hist.csv'.
    """
    label = _GRAN.get(granularity, granularity)
    path = os.path.join(CACHE_DIR, f"{product}_{label}{cache_tag}.csv")
    if use_cache and os.path.exists(path):
        return load_bars(path)

    step = 300 * granularity
    end = int(time.time())
    seen = {}  # ts -> Bar, dedup across windows
    while len(seen) < max_bars:
        start = end - step
        utc = datetime.timezone.utc
        s_iso = datetime.datetime.fromtimestamp(start, utc).isoformat()
        e_iso = datetime.datetime.fromtimestamp(end, utc).isoformat()
        url = (f"https://api.exchange.coinbase.com/products/{product}/candles"
               f"?granularity={granularity}&start={s_iso}&end={e_iso}")
        req = urllib.request.Request(url, headers={"User-Agent": "marketcoach/0.1"})
        try:
            raw = json.load(urllib.request.urlopen(req, timeout=15))
        except urllib.error.HTTPError as e:
            if e.code == 429:  # rate limited: back off and retry same window
                time.sleep(1.0); continue
            raise
        if not raw:
            break
        for c in raw:
            seen[int(c[0])] = Bar(int(c[0]), c[3], c[2], c[1], c[4], c[5])
        end = start
        time.sleep(0.34)  # ~3 req/s, under Coinbase's public limit

    bars = sorted(seen.values(), key=lambda b: b.ts)[-max_bars:]
    save_bars(path, bars)
    return bars


def fetch_many(products, granularity=86400, max_bars=1500, use_cache=True):
    """Fetch a basket: {product: bars}. Skips products the API rejects."""
    out = {}
    for p in products:
        try:
            out[p] = fetch_coinbase_history(p, granularity, max_bars, use_cache)
        except Exception as e:
            print(f"  skip {p}: {type(e).__name__} {str(e)[:60]}")
    return out


def synthetic(n=800, seed=7, start=100.0, drift=0.0003, vol=0.03):
    """Geometric-random-walk price series for offline testing. Deterministic by seed.
    drift/vol per bar. Produces a Bar list so the engine runs with zero network."""
    rng = random.Random(seed)
    bars, price, ts = [], start, 1_600_000_000
    for _ in range(n):
        ret = drift + rng.gauss(0, vol)
        o = price
        c = max(0.01, o * math.exp(ret))
        hi = max(o, c) * (1 + abs(rng.gauss(0, vol / 3)))
        lo = min(o, c) * (1 - abs(rng.gauss(0, vol / 3)))
        v = rng.uniform(1000, 5000)
        bars.append(Bar(ts, round(o, 2), round(hi, 2), round(lo, 2), round(c, 2), round(v, 2)))
        price, ts = c, ts + 86400
    return bars


def resample(bars, seconds):
    """Aggregate ascending finer bars into `seconds`-wide bars aligned to UTC
    multiples of `seconds` (so a 4h bar always starts at 00/04/08/12/16/20 UTC —
    the same alignment Kraken's native 4h candles use). Buckets missing any
    constituent bar are DROPPED rather than filled, so a data gap never
    produces a distorted candle. O/H/L/C/V are first/max/min/last/sum."""
    if not bars:
        return []
    base = bars[1].ts - bars[0].ts if len(bars) > 1 else 3600
    per = seconds // base
    out, cur, bucket = [], [], None
    def flush():
        if len(cur) == per:
            out.append(Bar(bucket, cur[0].open, max(b.high for b in cur),
                           min(b.low for b in cur), cur[-1].close, sum(b.volume for b in cur)))
    for b in bars:
        t = (b.ts // seconds) * seconds
        if t != bucket:
            if bucket is not None:
                flush()
            bucket, cur = t, []
        cur.append(b)
    flush()
    return out


def get_bars(source="BTC-USD", granularity=86400, use_cache=True, max_bars=1500):
    """Convenience: 'synthetic' -> generator, else long paginated history for a
    Coinbase product. Falls back to the single-request fetch if pagination fails."""
    if source == "synthetic":
        return synthetic()
    if granularity == 14400:
        # Coinbase has no 4h candles: build them from DEEP hourly history
        # (own cache file, separate from the shallow 1500-bar hourly cache).
        hourly = fetch_coinbase_history(source, 3600, max_bars * 4 + 8, use_cache, cache_tag="_deep")
        return resample(hourly, 14400)[-max_bars:]
    try:
        return fetch_coinbase_history(source, granularity, max_bars, use_cache)
    except Exception as e:
        print(f"  history fetch failed ({type(e).__name__}); falling back to single page")
        return fetch_coinbase(source, granularity, use_cache)
