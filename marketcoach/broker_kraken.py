"""Minimal Kraken client (pure stdlib) for placing orders on the user's own
Kraken Pro account. Unlike Alpaca, Kraken has no separate paper-trading endpoint
— there's only one API, and it's real money. That's fine: OUR OWN engine.run()
simulation has been the actual "paper" stage all along (the smart/multi/moon
accounts never depended on a broker's paper sandbox), so this module's only job
is the FINAL graduation step, gated by graduation_check.py like everything else.

SAFETY, by construction, not just by promise:
  - No withdrawal endpoint is implemented ANYWHERE in this file — not stubbed,
    not disabled, simply doesn't exist in the code. The only private calls are
    Balance (read) and AddOrder (buy/sell). Even a compromised key used through
    this module physically cannot move money out of the account.
  - DRY-RUN by default: with no API key/secret configured, every "order" is
    logged, not sent. Exactly the same pattern as broker_alpaca.py.
  - You additionally create the Kraken API key itself with "Query Funds" and
    "Create & Modify Orders" permission ONLY — leave "Withdraw Funds" OFF. Two
    independent locks: Kraken's own permission system, and this code never
    calling that endpoint even if the key somehow had the permission.
"""
import base64, hashlib, hmac, json, os, time, urllib.parse, urllib.request, urllib.error

API_URL = "https://api.kraken.com"
KEYS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "paper_state", "kraken.keys.json")

# Kraken's pair codes don't follow one consistent pattern (legacy X/Z prefixes on
# some assets, plain names on others) — map the coins we actually trade rather
# than trying to derive it algorithmically.
PAIR_MAP = {
    "BTC-USD": "XXBTZUSD", "ETH-USD": "XETHZUSD", "SOL-USD": "SOLUSD",
    "ADA-USD": "ADAUSD", "AVAX-USD": "AVAXUSD", "LINK-USD": "LINKUSD",
    "LTC-USD": "XLTCZUSD", "XRP-USD": "XXRPZUSD", "DOT-USD": "DOTUSD",
    "DOGE-USD": "XDGUSD", "ATOM-USD": "ATOMUSD", "UNI-USD": "UNIUSD",
    # Expansion batch (2026-09-18): more coins to watch = more chances for a
    # validated strategy to fire, without touching per-trade frequency/costs.
    # Verified against Kraken's public AssetPairs before adding.
    "BCH-USD": "BCHUSD", "ALGO-USD": "ALGOUSD", "FIL-USD": "FILUSD",
    "NEAR-USD": "NEARUSD", "APT-USD": "APTUSD", "ARB-USD": "ARBUSD",
    "OP-USD": "OPUSD", "ICP-USD": "ICPUSD", "ETC-USD": "XETCZUSD",
    "XLM-USD": "XXLMZUSD", "AAVE-USD": "AAVEUSD", "SHIB-USD": "SHIBUSD",
    "SUI-USD": "SUIUSD", "INJ-USD": "INJUSD", "RENDER-USD": "RENDERUSD",
    "TIA-USD": "TIAUSD", "SEI-USD": "SEIUSD", "WLD-USD": "WLDUSD",
    "HBAR-USD": "HBARUSD", "VET-USD": "VETUSD", "CRV-USD": "CRVUSD",
    "GRT-USD": "GRTUSD", "SAND-USD": "SANDUSD", "MANA-USD": "MANAUSD",
    "XTZ-USD": "XTZUSD", "KAVA-USD": "KAVAUSD", "ZEC-USD": "XZECZUSD",
    "DASH-USD": "DASHUSD", "COMP-USD": "COMPUSD", "SNX-USD": "SNXUSD",
    "BONK-USD": "BONKUSD", "POL-USD": "POLUSD",
}


def to_kraken_pair(product):
    return PAIR_MAP.get(product)


def load_keys():
    if os.path.exists(KEYS_FILE):
        try:
            d = json.load(open(KEYS_FILE))
            return d.get("api_key"), d.get("api_secret")
        except Exception:
            pass
    return os.environ.get("KRAKEN_API_KEY"), os.environ.get("KRAKEN_API_SECRET")


NONCE_FILE = os.path.join(os.path.dirname(KEYS_FILE), ".kraken_nonce")


class _NonceLock:
    """Cross-process lock held from nonce generation THROUGH the HTTP response. Kraken rejects a
    nonce lower than one it has already seen, and ~50 cron processes share one key, so both the
    numbering and the arrival order must be serialized (private calls are rare, so this is cheap)."""
    def __enter__(self):
        import fcntl
        self.f = open(NONCE_FILE, "a+")
        fcntl.flock(self.f, fcntl.LOCK_EX)
        self.f.seek(0)
        try:
            last = int(self.f.read().strip() or 0)
        except ValueError:
            last = 0
        self.nonce = max(int(time.time() * 1000), last + 1)
        self.f.seek(0); self.f.truncate(); self.f.write(str(self.nonce)); self.f.flush()
        return self.nonce

    def __exit__(self, *exc):
        import fcntl
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


class Kraken:
    def __init__(self, dry_run=None):
        self.key, self.secret = load_keys()
        self.live = bool(self.key and self.secret)
        self.dry_run = (not self.live) if dry_run is None else dry_run

    def _sign(self, urlpath, data):
        postdata = urllib.parse.urlencode(data)
        encoded = (str(data["nonce"]) + postdata).encode()
        message = urlpath.encode() + hashlib.sha256(encoded).digest()
        mac = hmac.new(base64.b64decode(self.secret), message, hashlib.sha512)
        return base64.b64encode(mac.digest()).decode()

    def _private(self, method, data=None):
        data = dict(data or {})
        with _NonceLock() as nonce:
            data["nonce"] = str(nonce)
            urlpath = f"/0/private/{method}"
            headers = {"API-Key": self.key, "API-Sign": self._sign(urlpath, data)}
            body = urllib.parse.urlencode(data).encode()
            req = urllib.request.Request(API_URL + urlpath, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    resp = json.load(r)
            except urllib.error.HTTPError as e:
                raise RuntimeError(f"Kraken {method} -> HTTP {e.code}: {e.read().decode()[:200]}")
        if resp.get("error"):
            raise RuntimeError(f"Kraken {method} error: {resp['error']}")
        return resp["result"]

    def _public(self, method, params=None):
        qs = "?" + urllib.parse.urlencode(params) if params else ""
        with urllib.request.urlopen(f"{API_URL}/0/public/{method}{qs}", timeout=15) as r:
            resp = json.load(r)
        if resp.get("error"):
            raise RuntimeError(f"Kraken {method} error: {resp['error']}")
        return resp["result"]

    # --- read-only ---
    def balance(self):
        """Read-only — gated on having real keys (`live`), NOT on `dry_run`.
        dry_run only ever controls whether market_order() submits a real order;
        a paper account with real keys should still be able to show the real
        account balance, that's the whole point of 'paper trades on the real
        connection.'"""
        if not self.live:
            return {"DRY_RUN": True, "note": "no keys configured"}
        # Read-only display/sanity call made by every tick: share one result across
        # processes for 5 minutes instead of hitting the private API ~50x an hour.
        cache = os.path.join(os.path.dirname(KEYS_FILE), ".balance_cache.json")
        try:
            c = json.load(open(cache))
            if time.time() - c["t"] < 300:
                return c["balance"]
        except Exception:
            pass
        bal = self._private("Balance")
        try:
            json.dump({"t": time.time(), "balance": bal}, open(cache, "w"))
        except Exception:
            pass
        return bal

    def ticker_price(self, product):
        pair = to_kraken_pair(product)
        result = self._public("Ticker", {"pair": pair})
        return float(next(iter(result.values()))["c"][0])  # last trade price

    # Short-lived shared price cache for DISPLAY paths (dashboard, leaderboard).
    # Trading paths keep calling ticker_price() directly — an order is never
    # sized off a cached price.
    _PRICE_CACHE = {}   # pair -> (fetched_at, price)

    def prefetch_prices(self, products):
        """One bulk Ticker request for many products (Kraken accepts a
        comma-separated pair list) instead of one HTTP call per held position."""
        pairs = [to_kraken_pair(p) for p in products if to_kraken_pair(p)]
        if not pairs:
            return
        result = self._public("Ticker", {"pair": ",".join(pairs)})
        now = time.time()
        for key, v in result.items():
            try:
                Kraken._PRICE_CACHE[key] = (now, float(v["c"][0]))
            except Exception:
                pass

    def ticker_price_cached(self, product, ttl=6):
        pair = to_kraken_pair(product)
        hit = Kraken._PRICE_CACHE.get(pair)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        price = self.ticker_price(product)
        Kraken._PRICE_CACHE[pair] = (time.time(), price)
        return price

    # --- orders: buy/sell ONLY. No withdrawal method exists in this class. ---
    def market_order(self, product, side, volume):
        """side: 'buy' or 'sell'. volume: amount of the COIN (not dollars) —
        caller must convert notional to volume using current price first.
        Always returns a dict including 'volume' — the exact amount ordered (or
        that WOULD be ordered, in dry-run) — so callers track local state from
        this single canonical number, never a separately-estimated one."""
        pair = to_kraken_pair(product)
        if not pair:
            raise ValueError(f"no Kraken pair mapping for {product}")
        if self.dry_run:
            print(f"    [DRY] {side.upper()} {volume:.8f} {pair} (market)")
            return {"dry_run": True, "pair": pair, "side": side, "volume": volume}
        result = self._private("AddOrder", {
            "pair": pair, "type": side, "ordertype": "market", "volume": f"{volume:.8f}"})
        result["volume"] = volume
        return result

    def buy_notional(self, product, dollars):
        """Convenience: buy $`dollars` worth of `product` at the current price.
        ticker_price is a PUBLIC endpoint (no auth, no order placed) — always
        call the real one, even in dry-run, so a dry-run preview shows the real
        coin volume it would actually buy, not a placeholder number."""
        try:
            price = self.ticker_price(product)
        except Exception:
            price = None
        if not price:
            print(f"    [DRY] could not fetch live price for {product}; skipping preview")
            return {"dry_run": True, "side": "buy", "skipped": True}
        volume = dollars / price
        return self.market_order(product, "buy", volume)
