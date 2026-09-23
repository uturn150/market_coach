"""Realistic paper execution: instead of assuming "signal price = fill price"
plus a flat bps haircut, walk Kraken's ACTUAL live order book at the moment
an order would be sent, and record exactly where the money went.

For every simulated market order this produces, and callers log:
  SIGNAL PRICE      price at the bar close that triggered the decision
  EXPECTED FILL     best ask (buy) / best bid (sell) — the price you'd hope for
  SIMULATED FILL    volume-weighted average after walking the book
  SPREAD COST       half-spread vs mid, in dollars
  SLIPPAGE COST     walking deeper than the touch + latency buffer, in dollars
  FEE               real Kraken taker fee for this account's volume tier
  TOTAL EXEC COST   spread + slippage + fee, vs a mid-price ideal fill

Deliberately CONSERVATIVE (per the project rule: underestimate profitability
rather than manufacture it):
  - PARTICIPATION_CAP: only a fraction of each visible book level is assumed
    obtainable (other takers, hidden/iceberg competition, stale snapshot).
  - LATENCY_BPS: extra adverse move between our book snapshot and an order
    actually arriving.
  - Orders larger than the obtainable visible depth FILL PARTIALLY — the
    unfilled remainder is reported, never silently assumed filled.
  - If the book can't be fetched, fall back to mid-price plus a wide,
    assumed haircut (never to the flattering "no cost" assumption).

Taker (market) orders only. Resting limit-order (maker) simulation needs
order-lifetime tracking across ticks and is NOT modeled here — maker fills
must not be assumed until that exists.
"""
from .broker_kraken import to_kraken_pair
from . import kraken_fees

PARTICIPATION_CAP = 0.5      # assume only 50% of each displayed level is obtainable
LATENCY_BPS = 2.0            # extra adverse drift between snapshot and arrival
FALLBACK_HAIRCUT_BPS = 15.0  # spread+slippage assumed when the book is unavailable
BOOK_LEVELS = 50
FEE_REFERENCE_PAIR = "XXBTZUSD"  # fee tier is account-wide at these volumes; one lookup, not 24


def _fee_rate():
    return kraken_fees.get_fee_bps(FEE_REFERENCE_PAIR)["taker_bps"] / 10_000.0


def fetch_book(broker, product):
    """Returns (asks, bids) as lists of (price, volume), best first."""
    raw = broker._public("Depth", {"pair": to_kraken_pair(product), "count": BOOK_LEVELS})
    book = next(iter(raw.values()))
    asks = [(float(p), float(v)) for p, v, *_ in book["asks"]]
    bids = [(float(p), float(v)) for p, v, *_ in book["bids"]]
    return asks, bids


def _walk(levels, want_units=None, want_dollars=None):
    """Consume levels best-first. Stops at want_units (coins) or want_dollars,
    whichever is given. Returns (units, dollars, levels_used)."""
    units = dollars = 0.0
    used = 0
    for price, vol in levels:
        avail = vol * PARTICIPATION_CAP
        if avail <= 0:
            continue
        if want_units is not None:
            take = min(avail, want_units - units)
        else:
            take = min(avail, (want_dollars - dollars) / price)
        if take <= 0:
            break
        units += take
        dollars += take * price
        used += 1
        if (want_units is not None and units >= want_units - 1e-12) or \
           (want_dollars is not None and dollars >= want_dollars - 1e-9):
            break
    return units, dollars, used


def simulate_market_buy(broker, product, notional, signal_price=None):
    """Spend up to `notional` dollars INCLUDING the fee. Returns a dict; check
    filled_fraction — a thin book can fill only part of the order."""
    fee = _fee_rate()
    budget = notional / (1 + fee)  # dollars available for coins after the fee
    try:
        asks, bids = fetch_book(broker, product)
        best_ask, best_bid = asks[0][0], bids[0][0]
        mid = (best_ask + best_bid) / 2
        units, gross, used = _walk(asks, want_dollars=budget)
        source = "book"
    except Exception as e:
        # No book: assume a wide adverse haircut off the last trade price.
        mid = broker.ticker_price(product)
        best_ask = mid * (1 + FALLBACK_HAIRCUT_BPS / 2 / 10_000)
        units = budget / (mid * (1 + FALLBACK_HAIRCUT_BPS / 10_000))
        gross = units * mid * (1 + FALLBACK_HAIRCUT_BPS / 10_000)
        used, source = 0, f"fallback({type(e).__name__})"
    if units <= 0:
        return {"filled_units": 0.0, "filled_fraction": 0.0, "source": source,
                "signal_price": signal_price, "reason": "no obtainable depth"}
    walked_gross = gross
    latency_cost = gross * LATENCY_BPS / 10_000
    gross += latency_cost
    fee_usd = gross * fee
    total = gross + fee_usd
    avg = gross / units
    spread_cost = units * max(0.0, best_ask - mid)
    slippage_cost = max(0.0, gross - units * best_ask)  # incl. latency buffer
    return {
        "side": "buy", "source": source, "levels_used": used,
        "signal_price": signal_price, "mid": mid, "expected_fill": best_ask,
        "sim_fill": avg, "filled_units": units,
        "filled_fraction": min(1.0, walked_gross / budget) if budget else 0.0,
        "unfilled_notional": max(0.0, notional - total),
        "spread_cost_usd": spread_cost, "slippage_cost_usd": slippage_cost,
        "fee_usd": fee_usd, "total_exec_cost_usd": spread_cost + slippage_cost + fee_usd,
        "total_cost_usd": total,  # cash actually spent, fee included
    }


def simulate_market_sell(broker, product, units, signal_price=None):
    """Sell up to `units` coins into the bid side. May fill partially."""
    fee = _fee_rate()
    try:
        asks, bids = fetch_book(broker, product)
        best_ask, best_bid = asks[0][0], bids[0][0]
        mid = (best_ask + best_bid) / 2
        sold, gross, used = _walk(bids, want_units=units)
        source = "book"
    except Exception as e:
        mid = broker.ticker_price(product)
        best_bid = mid * (1 - FALLBACK_HAIRCUT_BPS / 2 / 10_000)
        sold = units
        gross = units * mid * (1 - FALLBACK_HAIRCUT_BPS / 10_000)
        used, source = 0, f"fallback({type(e).__name__})"
    if sold <= 0:
        return {"filled_units": 0.0, "filled_fraction": 0.0, "source": source,
                "signal_price": signal_price, "reason": "no obtainable depth"}
    latency_cost = gross * LATENCY_BPS / 10_000
    gross -= latency_cost
    fee_usd = gross * fee
    net = gross - fee_usd
    avg = gross / sold
    spread_cost = sold * max(0.0, mid - best_bid)
    slippage_cost = max(0.0, sold * best_bid - gross)
    return {
        "side": "sell", "source": source, "levels_used": used,
        "signal_price": signal_price, "mid": mid, "expected_fill": best_bid,
        "sim_fill": avg, "filled_units": sold, "filled_fraction": sold / units if units else 0.0,
        "unfilled_units": max(0.0, units - sold),
        "spread_cost_usd": spread_cost, "slippage_cost_usd": slippage_cost,
        "fee_usd": fee_usd, "total_exec_cost_usd": spread_cost + slippage_cost + fee_usd,
        "net_proceeds_usd": net,
    }


def exec_fields(sim):
    """The flat, rounded subset that gets logged on every fill record."""
    keys = ["source", "signal_price", "mid", "expected_fill", "sim_fill", "filled_fraction",
            "spread_cost_usd", "slippage_cost_usd", "fee_usd", "total_exec_cost_usd"]
    out = {}
    for k in keys:
        v = sim.get(k)
        out[k] = round(v, 8) if isinstance(v, float) else v
    return out


def calibrate(coins=None, notionals=(2.08, 50.0)):
    """Measure REAL half-spread and slippage across the basket for the order
    sizes this fleet actually uses — the evidence for whether the backtest's
    flat 5bps spread + 3bps slippage assumption is honest."""
    from .broker_kraken import Kraken
    from .portfolio_optimize import DEFAULT_BASKET
    broker = Kraken(dry_run=True)
    coins = coins or DEFAULT_BASKET
    rows = []
    for c in coins:
        product = c if "-" in c else f"{c}-USD"
        row = {"coin": c}
        try:
            asks, bids = fetch_book(broker, product)
            mid = (asks[0][0] + bids[0][0]) / 2
            row["half_spread_bps"] = round((asks[0][0] - bids[0][0]) / 2 / mid * 1e4, 1)
            for n in notionals:
                units, gross, _ = _walk(asks, want_dollars=n)
                row[f"filled_{n}"] = round(gross / n, 3) if n else 0
                row[f"slip_bps_{n}"] = round((gross / units / asks[0][0] - 1) * 1e4, 1) if units else None
        except Exception as e:
            row["error"] = f"{type(e).__name__}"
        rows.append(row)
    return rows
