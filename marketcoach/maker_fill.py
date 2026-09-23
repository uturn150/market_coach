"""Resting limit-order (maker) fill model for backtests.

The legacy arena assumption was: a "maker" order always fills at the signal
price and costs (maker_fee + slippage). Real limit orders do not work that way:
they only fill if price trades THROUGH them, and the fills you get are
adverse-selected (you fill when price is moving against you; you miss when it
runs away in your favour). This models that:

  * Signal-driven orders are posted at the bar's open, joined at the touch
    (buy at best bid = open*(1-half_spread), sell at best ask).
  * One bar to work. A buy fills only if the bar's low trades THROUGH the limit
    by TRADE_THROUGH_BPS (a mere touch is not assumed to fill: queue position
    is unknown). A sell likewise on the high. The fill price is the limit; the
    fee is the real maker fee; no slippage (passive).
  * Unfilled BUY  -> cancelled, the entry is MISSED (counted, never retried).
  * Unfilled SELL -> we chase: crossed as a taker at the bar's close side
    (open*(1 - half_spread) less taker fee). An exit is never skipped.
  * Risk exits (stop / trailing stop, flagged "urgent") are always taker.

Every fill's EFFECTIVE price (fee included) is recorded in stats["eff_fills"],
so round-trip P&L can be computed exactly without a flat-rate approximation.
Costs are the REAL fee tier (kraken_fees), not the strategy file's assumptions.
"""
from .execution import Fill

HALF_SPREAD_BPS = 2.4        # measured average from live Depth (paper_exec.calibrate)
TRADE_THROUGH_BPS = 1.0      # must trade this far through the limit to count as filled
TAKER_SLIPPAGE_BPS = 0.5     # measured ~0 at these sizes; small allowance


def config(maker_bps=None, taker_bps=None, half_spread_bps=HALF_SPREAD_BPS,
           trade_through_bps=TRADE_THROUGH_BPS, taker_slip_bps=TAKER_SLIPPAGE_BPS):
    if maker_bps is None or taker_bps is None:
        from . import kraken_fees
        f = kraken_fees.get_fee_bps()
        maker_bps = f["maker_bps"] if maker_bps is None else maker_bps
        taker_bps = f["taker_bps"] if taker_bps is None else taker_bps
    return {"maker": maker_bps / 1e4, "taker": taker_bps / 1e4, "hs": half_spread_bps / 1e4,
            "tt": trade_through_bps / 1e4, "slip": taker_slip_bps / 1e4}


def _bump(stats, key, n=1):
    if stats is not None:
        stats[key] = stats.get(key, 0) + n


def _eff(stats, side, units, price):
    if stats is not None:
        stats.setdefault("eff_fills", []).append((side, units, price))


def execute(pending, bar, cash, units, avg_cost, fills, cfg, stats):
    for act in pending:
        side = act["action"]
        urgent = bool(act.get("urgent"))
        if side == "buy":
            if cash <= 0:
                continue
            limit = bar.open * (1 - cfg["hs"])
            _bump(stats, "buy_attempts")
            if bar.low <= limit * (1 - cfg["tt"]):
                spend = cash * _frac(act.get("size", "100%"))
                got = spend / (limit * (1 + cfg["maker"]))
                new_units = units + got
                avg_cost = ((avg_cost * units) + limit * got) / new_units
                cash -= spend
                units = new_units
                fills.append(Fill(bar.ts, "buy", limit, got, spend * cfg["maker"]))
                _eff(stats, "buy", got, limit * (1 + cfg["maker"]))
                _bump(stats, "buy_fills")
            else:
                _bump(stats, "buy_missed")
        elif side == "sell" and units > 0:
            frac = 1.0 if act.get("size") in ("all", None) else _frac(act["size"])
            sell_units = units * frac
            limit = bar.open * (1 + cfg["hs"])
            _bump(stats, "sell_attempts")
            if not urgent and bar.high >= limit * (1 + cfg["tt"]):
                px, rate, kind = limit, cfg["maker"], "maker"
                _bump(stats, "sell_fills")
            else:
                # urgent exits cross at once; a missed limit exit waited the whole bar, so chase at its close
                ref = bar.open if urgent else bar.close
                px, rate, kind = ref * (1 - cfg["hs"] - cfg["slip"]), cfg["taker"], "taker"
                _bump(stats, "sell_urgent_taker" if urgent else "sell_chased_taker")
            cash += sell_units * px * (1 - rate)
            units -= sell_units
            fills.append(Fill(bar.ts, "sell", px, sell_units, sell_units * px * rate))
            _eff(stats, "sell", sell_units, px * (1 - rate))
            if units <= 1e-12:
                units, avg_cost = 0.0, 0.0
    return cash, units, avg_cost, fills


def _frac(size):
    if isinstance(size, (int, float)):
        return float(size)
    s = str(size).strip()
    return float(s[:-1]) / 100.0 if s.endswith("%") else (1.0 if s == "all" else float(s))


def trips_from_stats(stats):
    """FIFO round trips on effective (fee-inclusive) prices -> list of net moves."""
    lots, out = [], []
    for side, units, px in stats.get("eff_fills", []):
        if side == "buy":
            lots.append([units, px])
        else:
            rem = units
            while rem > 1e-12 and lots:
                take = min(lots[0][0], rem)
                out.append(px / lots[0][1] - 1)
                lots[0][0] -= take
                rem -= take
                if lots[0][0] <= 1e-12:
                    lots.pop(0)
    return out
