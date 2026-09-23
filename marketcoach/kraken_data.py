"""Live market data sourced directly from Kraken's own public OHLC endpoint,
so a live-trading bot's PRICES, SIGNALS, orders, and balance all come from the
same exchange end to end — instead of computing signals from Coinbase candles
while only executing/checking balance on Kraken.

Deliberately used for LIVE ticks only (kraken_portfolio.py / kraken_moon.py /
regime.is_risk_on). Historical backtesting and optimization keep using
Coinbase's deeper paginated history (marketcoach/data.py) because Kraken's
public OHLC endpoint always returns just its most recent ~720 candles — no
`since` pagination further back is honored, confirmed by testing it directly.
That's plenty for every indicator warmup used here (nothing needs more than
~200 periods) but not enough for a genuine multi-year walk-forward validation.
Same real market either way — Kraken and Coinbase track each other tightly on
these major pairs — just two different lookback windows for two different jobs.
"""
import json, os, time, urllib.request
from .data import Bar
from .broker_kraken import to_kraken_pair

# Kraken's OHLC `interval` is in MINUTES, and only these values are valid.
_INTERVAL_MIN = {60: 1, 300: 5, 900: 15, 1800: 30, 3600: 60, 14400: 240,
                 86400: 1440, 604800: 10080, 1209600: 21600}


CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "paper_state", ".candle_cache")
MAX_AGE = 600   # seconds
last_was_live = True   # callers may skip their politeness sleep after a cache hit


def fetch_recent(product, granularity=86400):
    """Cross-process cached wrapper. Dozens of cron ticks ask for the same candles; one fetch is
    shared while it is fresh (<= MAX_AGE s) AND was taken inside the CURRENT bar period, so a
    bar that has just closed is never hidden behind a stale copy. Falls back to a live fetch on
    any cache problem."""
    path = os.path.join(CACHE_DIR, f"{product}_{granularity}.json")
    now = time.time()
    try:
        c = json.load(open(path))
        if now - c["t"] < MAX_AGE and int(c["t"] // granularity) == int(now // granularity):
            globals()["last_was_live"] = False
            return [Bar(*r) for r in c["bars"]]
    except Exception:
        pass
    globals()["last_was_live"] = True
    bars = _fetch_recent_live(product, granularity)
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = path + f".{os.getpid()}"
        json.dump({"t": now, "bars": [[b.ts, b.open, b.high, b.low, b.close, b.volume] for b in bars]}, open(tmp, "w"))
        os.replace(tmp, path)
    except Exception:
        pass
    return bars


def _fetch_recent_live(product, granularity=86400):
    """Fresh pull of Kraken's own recent OHLC candles for `product` (e.g.
    'BTC-USD'). Same shape/contract as data.fetch_recent (ascending Bar list;
    the last bar may still be forming — callers already filter to closed bars
    themselves), so it drops in as a straight replacement."""
    interval = _INTERVAL_MIN.get(granularity)
    if interval is None:
        raise ValueError(f"Kraken OHLC has no native interval for {granularity}s")
    pair = to_kraken_pair(product)
    if not pair:
        raise ValueError(f"no Kraken pair mapping for {product}")
    url = f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval={interval}"
    req = urllib.request.Request(url, headers={"User-Agent": "marketcoach/0.1"})
    raw = json.load(urllib.request.urlopen(req, timeout=15))
    if raw.get("error"):
        raise RuntimeError(f"Kraken OHLC error for {product}: {raw['error']}")
    result = raw.get("result", {})
    key = next((k for k in result if k != "last"), None)
    rows = result.get(key, []) if key else []
    bars = [Bar(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[6]))
            for r in rows]
    bars.sort(key=lambda b: b.ts)
    return bars
