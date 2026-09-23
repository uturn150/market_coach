"""Performance metrics + the buy-and-hold benchmark. Buy-and-hold is our 'mirror
match sanity check': if a strategy can't beat just holding the asset — after
costs, on data it never trained on — it has no edge, full stop.
"""
import math
from collections import namedtuple

Report = namedtuple("Report", "total_return cagr sharpe max_dd n_trades win_rate "
                              "buy_hold_return excess final_equity "
                              "total_fees fee_drag n_round_trips avg_rt_cost_pct "
                              "profit_factor low_confidence")

# Below this many round trips, a backtest's stats are mostly noise — too few
# samples to trust. Common industry rule of thumb (echoed in the wild as "need
# 100+ trades"); we flag rather than hard-block since data is often scarce.
MIN_TRADES_FOR_CONFIDENCE = 30


def _daily_returns(equity):
    return [equity[i] / equity[i - 1] - 1 for i in range(1, len(equity)) if equity[i - 1] > 0]


def summarize(result, bars_per_year=365):
    eq = result.equity
    if len(eq) < 2 or eq[0] <= 0:
        return Report(0, 0, 0, 0, 0, 0, 0, 0, eq[-1] if eq else 0, 0, 0, 0, 0, 0.0, True)

    total = eq[-1] / eq[0] - 1
    years = len(eq) / bars_per_year
    cagr = (eq[-1] / eq[0]) ** (1 / years) - 1 if years > 0 and eq[-1] > 0 else 0

    rets = _daily_returns(eq)
    if len(rets) > 1:
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        sd = math.sqrt(var)
        sharpe = (mean / sd) * math.sqrt(bars_per_year) if sd > 0 else 0
    else:
        sharpe = 0

    peak, max_dd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v)
        max_dd = min(max_dd, v / peak - 1)

    # round-trip win rate: pair each sell against running realized P&L
    wins = trades = 0
    for f in result.fills:
        if f.side == "sell":
            trades += 1
    # simple proxy: count sells that left equity above the prior buy is complex to
    # track here; report trade count and leave win_rate as sells-that-helped later.
    n_trades = sum(1 for f in result.fills if f.side == "buy")

    # --- fee microscope: nothing hides ---
    total_fees = sum(f.cost for f in result.fills)
    fee_drag = total_fees / result.cash_start if result.cash_start else 0
    n_round_trips = sum(1 for f in result.fills if f.side == "sell")
    turnover = sum(f.units * f.price for f in result.fills)
    avg_rt_cost_pct = (total_fees / turnover) * 2 if turnover else 0  # ~round-trip %

    # --- profit factor: gross $ won / gross $ lost on closed round trips.
    # A distinct signal from win rate — a strategy can win rarely (34% of
    # trades, say) and still be excellent if winners are much bigger than
    # losers. >1 means profitable gross of the win-rate story; <1 means the
    # losers outweigh the winners even if there are more winning trades. ---
    pf = profit_factor(result)
    low_conf = n_round_trips < MIN_TRADES_FOR_CONFIDENCE

    bh = result.closes[-1] / result.closes[0] - 1
    return Report(total, cagr, sharpe, max_dd, n_trades,
                  _win_rate(result), bh, total - bh, eq[-1],
                  total_fees, fee_drag, n_round_trips, avg_rt_cost_pct,
                  pf, low_conf)


def profit_factor(result):
    """Gross profit / gross loss across closed round trips (dollar P&L, net of
    the fills' own costs). None/inf-safe: no losing trades -> a large finite cap
    instead of literal infinity, so it still sorts/prints sensibly."""
    lots, gross_win, gross_loss = [], 0.0, 0.0
    for f in result.fills:
        if f.side == "buy":
            lots.append([f.units, f.price])
        elif f.side == "sell":
            remaining = f.units
            while remaining > 1e-12 and lots:
                lot = lots[0]
                take = min(lot[0], remaining)
                pnl = (f.price - lot[1]) * take
                if pnl >= 0:
                    gross_win += pnl
                else:
                    gross_loss += -pnl
                lot[0] -= take
                remaining -= take
                if lot[0] <= 1e-12:
                    lots.pop(0)
    if gross_loss <= 0:
        return gross_win / 1e-9 if gross_win > 0 else 0.0
    return gross_win / gross_loss


def _win_rate(result):
    """Fraction of completed buy->sell round trips that were profitable (gross of
    the mark-to-market, net of the modeled costs already baked into fills)."""
    lots = []           # FIFO of (units, price_incl_cost)
    wins = closed = 0
    for f in result.fills:
        if f.side == "buy":
            lots.append([f.units, f.price])
        elif f.side == "sell":
            remaining = f.units
            while remaining > 1e-12 and lots:
                lot = lots[0]
                take = min(lot[0], remaining)
                if f.price > lot[1]:
                    wins += 1
                closed += 1
                lot[0] -= take
                remaining -= take
                if lot[0] <= 1e-12:
                    lots.pop(0)
    return wins / closed if closed else 0.0


def fmt(r):
    pf = f"{r.profit_factor:.2f}" if r.profit_factor < 100 else "inf"
    conf = "  [LOW CONFIDENCE: <30 round trips, mostly noise]" if r.low_confidence else ""
    return (f"return {r.total_return*100:+6.1f}%   buy&hold {r.buy_hold_return*100:+6.1f}%   "
            f"excess {r.excess*100:+6.1f}%\n"
            f"CAGR {r.cagr*100:+6.1f}%   Sharpe {r.sharpe:5.2f}   maxDD {r.max_dd*100:6.1f}%   "
            f"trades {r.n_trades:3d}   win% {r.win_rate*100:4.0f}   PF {pf:>5}   "
            f"final ${r.final_equity:,.2f}{conf}")


def roundtrips(result, rate):
    """FIFO-match buys to sells into round trips. Returns list of (gross_move,
    net_move) per trip, where net is after paying `rate` on each side."""
    lots, trips = [], []
    for f in result.fills:
        if f.side == "buy":
            lots.append([f.units, f.price])
        elif f.side == "sell":
            rem = f.units
            while rem > 1e-12 and lots:
                lot = lots[0]
                take = min(lot[0], rem)
                gross = f.price / lot[1] - 1
                net = (f.price * (1 - rate)) / (lot[1] * (1 + rate)) - 1
                trips.append((gross, net))
                lot[0] -= take
                rem -= take
                if lot[0] <= 1e-12:
                    lots.pop(0)
    return trips


FeeGate = namedtuple("FeeGate", "ok verdict n_trips avg_gross avg_net breakeven hit_rate margin")


def fee_gate(result, strat, margin=1.5):
    """Does the typical trade clear its own fee wall? This is the guardrail that
    was missing a year ago. FAIL if the average trade loses money to fees; PASS
    only if the average gross move clears break-even with a `margin` cushion."""
    rate = strat.side_rate
    be = 2 * rate
    trips = roundtrips(result, rate)
    n = len(trips)
    if n == 0:
        return FeeGate(True, "NO TRADES (nothing to gate)", 0, 0, 0, be, 0, margin)
    avg_gross = sum(g for g, _ in trips) / n
    avg_net = sum(nt for _, nt in trips) / n
    hit = sum(1 for g, _ in trips if g > be) / n
    if avg_net <= 0:
        return FeeGate(False, "FAIL: the average trade LOSES money to fees", n,
                       avg_gross, avg_net, be, hit, margin)
    if avg_gross < be * margin:
        return FeeGate(False, "MARGINAL: positive but thin cushion over the fee wall",
                       n, avg_gross, avg_net, be, hit, margin)
    return FeeGate(True, "PASS: the average trade clears its fee wall with room",
                   n, avg_gross, avg_net, be, hit, margin)


def fmt_gate(g):
    return (f"FEE GATE [{g.verdict}]\n"
            f"      avg trade move {g.avg_gross*100:+.2f}%  vs  break-even wall "
            f"{g.breakeven*100:.2f}%   (net/trade {g.avg_net*100:+.3f}%, "
            f"{g.hit_rate*100:.0f}% of trades clear the wall, n={g.n_trips})")


def fmt_fees(r, strat=None):
    """The line that would have saved you a year ago."""
    s = (f"FEES: paid ${r.total_fees:,.2f} = {r.fee_drag*100:.1f}% of starting cash   "
         f"round trips {r.n_round_trips}   ~{r.avg_rt_cost_pct*100:.2f}% cost per round trip")
    if strat is not None:
        s += (f"\n      each round trip must gain > {strat.breakeven_move*100:.2f}% "
              f"JUST to break even  [{strat.costs['order_type']}]")
    return s
