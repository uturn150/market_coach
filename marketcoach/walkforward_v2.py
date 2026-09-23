"""Rolling walk-forward validation — upgrades the project's original single
60/40 train/test split (still in walkforward.py and portfolio_optimize._pooled,
both left untouched) to genuine TRAIN -> TEST -> ADVANCE -> TRAIN -> TEST windows
across the full history.

Why this matters beyond a single split: a strategy can look great on one
particular out-of-sample slice by luck (that slice happened to be a trend the
strategy is built for) and still have no durable edge. Walking forward through
MANY non-overlapping windows answers a different, harder question: does the
edge show up again and again across different market periods, or was the one
good out-of-sample result a fluke of when the split happened to land?

Every deployed strategy here is a FIXED set of rules (RSI thresholds, SMA
windows, etc.) — there is no per-window re-optimization to do for them, only
re-testing. (A future template/param-grid search COULD reuse rolling_windows()
below to optimize only on each TRAIN segment before testing forward — that
hook is deliberately left open via train_bars being passed to the caller, not
consumed internally — but today's job is validating what's already live.)

No lookahead: each window's TEST segment is data the strategy has not been
retuned on. The regime filter (allow_buy_ts) is computed once over the FULL
history up front, exactly like every other backtest in this project — it is a
market-wide fact (BTC vs its own trailing SMA), not something derived from a
strategy's own future.
"""
import json, os, time, math
from . import data, engine, metrics, regime
from .strategy import Strategy

RESULTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs", "walkforward")


def rolling_windows(bars, train_n, test_n, step_n=None):
    """Yield (train_bars, test_bars) tuples advancing through bars. step_n
    defaults to test_n (non-overlapping test windows); a smaller step_n gives
    overlapping windows for more samples at the cost of correlated results."""
    step_n = step_n or test_n
    i = 0
    while i + train_n + test_n <= len(bars):
        yield bars[i:i + train_n], bars[i + train_n:i + train_n + test_n]
        i += step_n


def _sortino(rets, bars_per_year=365):
    """Like Sharpe, but only penalizes downside deviation — a strategy with
    big upside swings and small downside swings shouldn't be punished for the
    upside volatility the way Sharpe punishes all volatility equally."""
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    downside = [min(r, 0.0) ** 2 for r in rets]
    dd = math.sqrt(sum(downside) / len(downside))
    return (mean / dd) * math.sqrt(bars_per_year) if dd > 0 else 0.0


def _market_exposure(result):
    """Fraction of the window's total time actually holding a position (vs
    sitting in cash). Buy/sell fills come in pairs (a sell always closes what
    a prior buy opened, FIFO); sum the held durations, divide by total span."""
    ts0, ts1 = result.timestamps[0], result.timestamps[-1]
    span = ts1 - ts0
    if span <= 0:
        return 0.0
    held = 0
    open_ts = None
    for f in result.fills:
        if f.side == "buy" and open_ts is None:
            open_ts = f.ts
        elif f.side == "sell" and open_ts is not None:
            held += f.ts - open_ts
            open_ts = None
    if open_ts is not None:  # still holding at window end
        held += ts1 - open_ts
    return min(1.0, held / span)


def _cost_breakdown(strat, turnover_usd):
    """Split the strategy's own itemized cost model proportionally across the
    dollar turnover — an analytical estimate (fee_bps, half_spread_bps,
    slippage_bps are already known exactly from the strategy's costs block),
    not a per-fill instrumented measurement. That instrumented version is
    priority #5 (realistic paper fills / real order-book fills); this is
    accurate enough for walk-forward reporting today since the itemized rates
    ARE the ground truth the engine actually charged."""
    c = strat.costs
    if c["order_type"] == "maker":
        fee_bps, spread_bps, slip_bps = c["maker_fee_bps"], 0.0, c["slippage_bps"]
    else:
        fee_bps, spread_bps, slip_bps = c["taker_fee_bps"], c["half_spread_bps"], c["slippage_bps"]
    total_bps = fee_bps + spread_bps + slip_bps
    total_cost = turnover_usd * (total_bps / 10_000.0) if total_bps else 0.0
    def share(bps):
        return total_cost * (bps / total_bps) if total_bps else 0.0
    return {"fee_cost_usd": round(share(fee_bps), 4),
            "spread_cost_usd": round(share(spread_bps), 4),
            "slippage_cost_usd": round(share(slip_bps), 4),
            "total_cost_usd": round(total_cost, 4)}


def _trip_stats(trips):
    """win_rate/avg_winner/avg_loser/profit_factor from a list of (gross, net)
    round trips. Pulled out as its own function (rather than only living
    inside metrics.profit_factor's fill-pairing) so callers that already have
    a correctly-computed trip list — e.g. one aggregated PER COIN before
    pooling, see _pool_windows below — never need to re-derive trips from a
    fill stream that can't support it (see the pooling note below)."""
    if not trips:
        return {"win_rate_pct": 0.0, "avg_winner_pct": 0.0, "avg_loser_pct": 0.0,
                "profit_factor": 0.0, "n_round_trips": 0}
    wins = [net for _, net in trips if net > 0]
    losses = [net for _, net in trips if net <= 0]
    gross_win = sum(w for w in wins)
    gross_loss = -sum(l for l in losses)
    pf = (gross_win / gross_loss) if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)
    return {
        "win_rate_pct": round(len(wins) / len(trips) * 100, 1),
        "avg_winner_pct": round((sum(wins) / len(wins) * 100) if wins else 0.0, 2),
        "avg_loser_pct": round((sum(losses) / len(losses) * 100) if losses else 0.0, 2),
        "profit_factor": round(pf, 2),
        "n_round_trips": len(trips),
    }


def window_metrics(strat, result, bars_per_year=365, trips=None):
    """Every metric the spec asked for, for ONE segment (train or test) of
    ONE window. Reuses metrics.summarize() for the equity-curve-based numbers
    (Sharpe, drawdown, total return — these only need the equity PATH, which
    is valid even for a pooled multi-coin curve since it's built by summing
    each coin's own equity per bar).

    IMPORTANT: `metrics.roundtrips()`/`profit_factor()` FIFO-match buys to
    sells assuming a single position stream (Fill has no coin identifier) —
    correct for a single-coin result, silently WRONG if `result` pools fills
    from multiple coins (a sell from coin B can get matched against a buy lot
    from coin A). Pass a pre-aggregated `trips` list (each coin's own
    metrics.roundtrips() output, concatenated) when `result` is pooled;
    only let this function derive trips itself for a genuine single-coin
    result."""
    rep = metrics.summarize(result, bars_per_year=bars_per_year)
    if trips is None:
        trips = metrics.roundtrips(result, strat.side_rate)
    trip_stats = _trip_stats(trips)
    daily_rets = metrics._daily_returns(result.equity)
    turnover_usd = sum(f.units * f.price for f in result.fills)
    costs = _cost_breakdown(strat, turnover_usd)
    return {
        "net_return_pct": round(rep.total_return * 100, 2),
        "buy_hold_return_pct": round(rep.buy_hold_return * 100, 2),
        "excess_pct": round(rep.excess * 100, 2),
        "n_trades": rep.n_trades,
        "n_round_trips": trip_stats["n_round_trips"],
        "win_rate_pct": trip_stats["win_rate_pct"],
        "avg_winner_pct": trip_stats["avg_winner_pct"],
        "avg_loser_pct": trip_stats["avg_loser_pct"],
        "profit_factor": trip_stats["profit_factor"],
        "max_drawdown_pct": round(rep.max_dd * 100, 1),
        "sharpe": round(rep.sharpe, 2),
        "sortino": round(_sortino(daily_rets, bars_per_year), 2),
        "turnover_usd": round(turnover_usd, 2),
        "market_exposure_pct": round(_market_exposure(result) * 100, 1),
        "fee_cost_usd": costs["fee_cost_usd"],
        "spread_cost_usd": costs["spread_cost_usd"],
        "slippage_cost_usd": costs["slippage_cost_usd"],
        "total_cost_usd": costs["total_cost_usd"],
        "low_confidence": rep.low_confidence,
    }


def _regime_hold_return(bars, allow_buy_ts, side_rate):
    """Return of the dumbest regime-aware benchmark: hold the coin only while
    the market regime filter is risk-on (decided from the PREVIOUS bar, so no
    lookahead), cash otherwise, paying the same per-side cost on every flip.
    Every strategy here is gated by that same regime filter, so plain
    buy-and-hold is an unfair benchmark — in a falling market ANY cash
    position beats it. The honest question is whether a strategy's own entry/
    exit rules add anything on top of the filter they all share."""
    if allow_buy_ts is None or len(bars) < 2:
        return bars[-1].close / bars[0].close - 1
    eq, held = 1.0, False
    for i in range(1, len(bars)):
        want = bars[i - 1].ts in allow_buy_ts
        if want != held:
            eq *= (1 - side_rate)
            held = want
        if held:
            eq *= bars[i].close / bars[i - 1].close
    if held:
        eq *= (1 - side_rate)
    return eq - 1


def _pool_windows(strategy_path, coin_bars, train_n, test_n, step_n, cash_per_coin,
                  allow_buy_ts, bars_per_year):
    """Run every coin through the SAME set of rolling windows (aligned by
    position in each coin's own bar list, since coins can have slightly
    different history lengths) and pool each window's TEST-segment fills
    across coins — matching how every other gate in this project pools across
    the basket rather than judging one coin alone."""
    min_len = min(len(b) for b in coin_bars.values())
    windows = list(rolling_windows(range(min_len), train_n, test_n, step_n))
    results = []
    from .engine import Result
    for w_i, (train_idx, test_idx) in enumerate(windows):
        test_slice = slice(test_idx[0], test_idx[-1] + 1)
        all_fills, per_coin_equity, all_ts, exposures, all_trips = [], [], None, [], []
        eq_start_total = bh_start_total = bh_end_total = rh_end_total = 0.0
        strat = Strategy.load(strategy_path)
        for product, bars in coin_bars.items():
            # Align on the MOST RECENT min_len bars so index i means the same
            # calendar date for every coin. (Slicing from each coin's own START
            # put coins with shorter histories in different calendar windows —
            # e.g. XRP's 1162 bars vs 1500 for others shifted its windows by
            # ~11 months. Bars are contiguous, so tail-alignment is date-aligned.)
            test_bars = bars[-min_len:][test_slice]
            if len(test_bars) < 10:
                continue
            res = engine.run(Strategy.load(strategy_path), test_bars, cash=cash_per_coin,
                             allow_buy_ts=allow_buy_ts)
            all_fills.extend(res.fills)
            per_coin_equity.append(res.equity)
            all_ts = res.timestamps if all_ts is None else all_ts
            eq_start_total += cash_per_coin
            bh_start_total += cash_per_coin
            bh_end_total += cash_per_coin * (test_bars[-1].close / test_bars[0].close)
            rh_end_total += cash_per_coin * (1 + _regime_hold_return(test_bars, allow_buy_ts, strat.side_rate))
            # Exposure AND round trips must be computed per-coin, before
            # pooling: both assume a single-position fill stream (Fill has no
            # coin identifier), which a merged multi-coin stream breaks —
            # a sell from coin B can get FIFO-matched against a buy lot from
            # coin A. (Caught this via a sanity check: window 4 showed +42%
            # excess alongside profit_factor 0.00, which is impossible —
            # that was the pooled-fill-pairing bug, not a real result.)
            exposures.append(_market_exposure(res))
            all_trips.extend(metrics.roundtrips(res, strat.side_rate))
        if eq_start_total == 0 or not per_coin_equity:
            continue
        # Genuine pooled per-bar equity curve (sum every coin's equity at each
        # aligned bar index) so Sharpe/Sortino/drawdown reflect the real path,
        # not just a start/end 2-point line. Fine to pool for THIS purpose —
        # unlike fill-pairing, summing equity needs no per-coin identity.
        n_bars = min(len(e) for e in per_coin_equity)
        pooled_equity = [sum(e[i] for e in per_coin_equity) for i in range(n_bars)]
        eq_end_total = pooled_equity[-1]
        pooled = Result(equity=pooled_equity, closes=pooled_equity, timestamps=all_ts[:n_bars],
                        fills=all_fills, cash_start=eq_start_total)
        wm = window_metrics(strat, pooled, bars_per_year, trips=all_trips)
        # Override return/excess with the TRUE pooled dollar figures (the
        # synthetic 2-point equity curve above is only for fills/turnover;
        # start/end dollars are exact).
        wm["net_return_pct"] = round((eq_end_total / eq_start_total - 1) * 100, 2)
        wm["buy_hold_return_pct"] = round((bh_end_total / bh_start_total - 1) * 100, 2)
        wm["excess_pct"] = round(wm["net_return_pct"] - wm["buy_hold_return_pct"], 2)
        wm["market_exposure_pct"] = round((sum(exposures) / len(exposures)) * 100, 1)
        wm["regime_hold_return_pct"] = round((rh_end_total / bh_start_total - 1) * 100, 2)
        wm["excess_vs_regime_hold_pct"] = round(wm["net_return_pct"] - wm["regime_hold_return_pct"], 2)
        wm["window_index"] = w_i
        first = list(coin_bars.values())[0][-min_len:]
        wm["test_start_ts"] = first[test_idx[0]].ts
        wm["test_end_ts"] = first[test_idx[-1]].ts
        results.append(wm)
    return results


def run(strategy_path, coins=None, granularity=86400, train_days=365, test_days=120,
       step_days=None, cash=50.0, use_regime=True, max_bars=1500, verbose=True):
    """Full rolling walk-forward for one strategy across a coin basket. Returns
    (per_window_results, summary) and writes both to logs/walkforward/."""
    from .portfolio_optimize import DEFAULT_BASKET
    coins = coins or DEFAULT_BASKET
    products = [c if "-" in c else f"{c}-USD" for c in coins]
    if verbose:
        print(f"Loading {len(products)}-coin basket for walk-forward...")
    coin_bars = {}
    for p in products:
        try:
            coin_bars[p] = data.get_bars(p, granularity=granularity, max_bars=max_bars)
        except Exception as e:
            if verbose:
                print(f"  skip {p}: {e}")
    bars_per_day = {86400: 1, 3600: 24, 21600: 4, 14400: 6}.get(granularity, 1)
    train_n, test_n = train_days * bars_per_day, test_days * bars_per_day
    step_n = (step_days or test_days) * bars_per_day
    bars_per_year = 365 * bars_per_day

    allow_buy_ts = regime.risk_on_timestamps("BTC-USD", granularity=granularity,
                                             max_bars=max_bars) if use_regime else None
    cash_per_coin = cash / len(coin_bars)

    per_window = _pool_windows(strategy_path, coin_bars, train_n, test_n, step_n,
                               cash_per_coin, allow_buy_ts, bars_per_year)

    if not per_window:
        summary = {"strategy": strategy_path, "n_windows": 0,
                  "verdict": "NOT ENOUGH HISTORY for even one full train+test window "
                             f"at train={train_days}d/test={test_days}d"}
    else:
        rh = [w["excess_vs_regime_hold_pct"] for w in per_window]
        rh_beats = sum(1 for e in rh if e > 0)
        excesses = [w["excess_pct"] for w in per_window]
        beats = sum(1 for e in excesses if e > 0)
        excesses_sorted = sorted(excesses)
        median_excess = excesses_sorted[len(excesses_sorted) // 2]
        worst_dd = min(w["max_drawdown_pct"] for w in per_window)
        total_trips = sum(w["n_round_trips"] for w in per_window)
        summary = {
            "strategy": strategy_path, "n_windows": len(per_window),
            "windows_beating_hold": beats,
            "pct_windows_beating_hold": round(beats / len(per_window) * 100, 1),
            "median_excess_pct": round(median_excess, 2),
            "best_window_excess_pct": max(excesses),
            "worst_window_excess_pct": min(excesses),
            "windows_beating_regime_hold": rh_beats,
            "pct_windows_beating_regime_hold": round(rh_beats / len(per_window) * 100, 1),
            "median_excess_vs_regime_hold_pct": round(sorted(rh)[len(rh) // 2], 2),
            "worst_window_drawdown_pct": worst_dd,
            "total_round_trips_across_windows": total_trips,
            "sample_ok": total_trips >= metrics.MIN_TRADES_FOR_CONFIDENCE,
        }
        if beats == len(per_window):
            summary["verdict"] = "PERSISTS: beats hold in EVERY window tested"
        elif beats / len(per_window) >= 0.6:
            summary["verdict"] = f"MOSTLY PERSISTS: beats hold in {beats}/{len(per_window)} windows"
        else:
            summary["verdict"] = (f"DOES NOT PERSIST: only beats hold in {beats}/{len(per_window)} "
                                  f"windows — the earlier single-split result may have been luck")

    if verbose:
        print(f"\n{summary.get('verdict', '')}")
        for w in per_window:
            print(f"  window {w['window_index']}: excess {w['excess_pct']:+.1f}%  "
                  f"PF {w['profit_factor']:.2f}  trips {w['n_round_trips']}  "
                  f"maxDD {w['max_drawdown_pct']:.1f}%  exposure {w['market_exposure_pct']:.0f}%  "
                  f"vs regime-hold {w['excess_vs_regime_hold_pct']:+.1f}%")

    os.makedirs(RESULTS_DIR, exist_ok=True)
    name = os.path.basename(strategy_path).replace(".json", "")
    out_path = os.path.join(RESULTS_DIR, f"{name}_{int(time.time())}.json")
    json.dump({"summary": summary, "windows": per_window}, open(out_path, "w"), indent=2)
    if verbose:
        print(f"\nSaved -> {out_path}")
    return per_window, summary
