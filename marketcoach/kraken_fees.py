"""Dynamic Kraken fee model — replaces the old flat, hardcoded taker_fee_bps=26
assumption baked into every strategy file with the ACTUAL fee schedule for this
account's real 30-day trading volume, pulled from Kraken's own TradeVolume
endpoint.

Why this exists: every strategy in this project was validated assuming a
0.26% taker fee. The real fee at this account's current volume tier (checked
2026-09-19) is 0.80% taker / 0.40% maker — roughly 3x higher. Since paper
trading generates zero real Kraken volume, this account will likely sit at
the lowest tier indefinitely unless real money starts trading, so the lowest
tier's numbers are the honest ones to validate against, not a temporary
placeholder.

Conservative-by-design: if the real fee can't be fetched (no keys, network
failure, a genuinely new pair Kraken hasn't priced), fall back to Kraken's
documented PUBLISHED lowest-tier schedule (0.40%/0.25% as of this writing)
rather than the old optimistic 0.26% — per the project's rule that paper
trading should underestimate profitability, never manufacture it via a
flattering cost model.
"""
import json, os, time
from .broker_kraken import Kraken

STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "paper_state")
CACHE_FILE = os.path.join(STATE_DIR, "fee_tier_cache.json")
CACHE_TTL = 6 * 3600  # refresh every 6h — a real account's 30-day volume tier
                      # doesn't move fast enough to need checking more often

# Kraken's published lowest-tier ($0-$10k/30d) standard schedule, used ONLY when
# the real TradeVolume call can't be made at all (e.g. no keys). This is a
# conservative floor, not a target — real trading always uses the live number.
FALLBACK_TAKER_BPS = 40.0
FALLBACK_MAKER_BPS = 25.0


def _load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            return json.load(open(CACHE_FILE))
        except Exception:
            pass
    return {}


def _save_cache(cache):
    os.makedirs(STATE_DIR, exist_ok=True)
    json.dump(cache, open(CACHE_FILE, "w"), indent=2)


def get_fee_bps(pair="XXBTZUSD", force_refresh=False):
    """Returns {"taker_bps": float, "maker_bps": float, "tier_volume_usd": float,
    "fetched_at": int, "source": "live"|"fallback"}. Cached per-pair on disk so
    every backtest/validation run doesn't re-hit Kraken's private API."""
    cache = _load_cache()
    entry = cache.get(pair)
    now = int(time.time())
    if not force_refresh and entry and now - entry.get("fetched_at", 0) < CACHE_TTL:
        return entry

    try:
        broker = Kraken(dry_run=True)  # dry_run only blocks ORDER placement; reads are fine
        if not broker.live:
            raise RuntimeError("no Kraken keys configured")
        r = broker._private("TradeVolume", {"pair": pair})
        taker = float(r["fees"][pair]["fee"]) * 100  # Kraken returns a percent string, e.g. "0.8000"
        maker = float(r["fees_maker"][pair]["fee"]) * 100
        entry = {"taker_bps": round(taker, 2), "maker_bps": round(maker, 2),
                 "tier_volume_usd": float(r.get("volume", 0.0)), "fetched_at": now,
                 "source": "live"}
    except Exception as e:
        entry = {"taker_bps": FALLBACK_TAKER_BPS, "maker_bps": FALLBACK_MAKER_BPS,
                 "tier_volume_usd": None, "fetched_at": now, "source": "fallback",
                 "fallback_reason": f"{type(e).__name__}: {e}"}

    cache[pair] = entry
    _save_cache(cache)
    return entry


def apply_to_spec(spec, pair="XXBTZUSD", force_refresh=False):
    """Return a COPY of a strategy spec with costs.taker_fee_bps/maker_fee_bps
    overwritten to the real current fee tier. Never mutates the input —
    callers decide whether to persist the change to a strategy's JSON file or
    just use it for a one-off validation run."""
    import copy
    fees = get_fee_bps(pair, force_refresh)
    out = copy.deepcopy(spec)
    c = out.setdefault("costs", {})
    c["taker_fee_bps"] = fees["taker_bps"]
    c["maker_fee_bps"] = fees["maker_bps"]
    return out, fees
