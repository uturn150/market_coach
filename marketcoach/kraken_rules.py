"""Kraken's REAL per-coin trading rules (public AssetPairs, read-only): minimum order size in coins, minimum cost in USD, lot/price
decimals. Used by the paper simulators so orders are the size Kraken would actually accept. Cached on disk for 24h.
No private endpoint, no orders."""
import json, os, time
from .broker_kraken import Kraken, PAIR_MAP

CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "kraken_pairs.json")
TTL = 24 * 3600


def _fetch():
    raw = Kraken(dry_run=True)._public("AssetPairs")
    by_alt = {v.get("altname"): v for v in raw.values()}
    by_key = dict(raw)
    out = {}
    for prod, kp in PAIR_MAP.items():
        v = by_key.get(kp) or by_alt.get(kp)
        if not v:
            continue
        out[prod] = {"kraken_pair": kp, "ordermin": float(v.get("ordermin", 0) or 0), "costmin": float(v.get("costmin", 0) or 0),
                     "lot_decimals": int(v.get("lot_decimals", 8)), "pair_decimals": int(v.get("pair_decimals", 2)),
                     "tick_size": float(v.get("tick_size", 0) or 0)}
    return out


def load(force=False):
    try:
        c = json.load(open(CACHE))
        if not force and time.time() - c["t"] < TTL:
            return c["pairs"]
    except Exception:
        c = None
    try:
        pairs = _fetch()
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        json.dump({"t": time.time(), "pairs": pairs}, open(CACHE, "w"), indent=1)
        return pairs
    except Exception:
        if c:
            return c["pairs"]                       # stale rules beat none
        raise


def rules_for(coins):
    p = load()
    return {c: p[c] for c in coins if c in p}


def min_notional(coin, price, rules=None):
    r = (rules or load()).get(coin)
    if not r:
        return 0.0
    return max(r["ordermin"] * price, r["costmin"])


if __name__ == "__main__":
    p = load(force=True)
    print(len(p), "pairs")
    for c, r in sorted(p.items()):
        print(f"{c:10s} ordermin {r['ordermin']:<10g} costmin ${r['costmin']:<5g} lot_dec {r['lot_decimals']} px_dec {r['pair_decimals']} tick {r['tick_size']}")
