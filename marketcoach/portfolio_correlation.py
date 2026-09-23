"""Cross-strategy portfolio-level analysis: are the 10 "different" validated
strategies actually independent bets, or secretly the same trade wearing
different clothes? Motivated directly by the rolling walk-forward finding
(2026-09-19) that all 10 strategies won and lost in the exact same historical
windows — this module quantifies that suspicion with real numbers instead of
eyeballing a pattern in printed output.

Everything here is READ-ONLY analysis over backtest data — no live account is
touched, no strategy file is changed. The "combined portfolio" simulation is
independent per-coin-per-strategy backtests SUMMED together after the fact —
the correct baseline for asking "would pooling capital even help," before
building the real cross-strategy capital-allocation machinery a genuine
shared pool would need once multiple strategies compete for the same cash at
the same moment (that contention-handling layer is the Master Risk Manager,
spec item #10 — deliberately not built into this analysis module).
"""
import json, os, time
from . import data, engine, regime, risk
from .strategy import Strategy
from .portfolio_optimize import DEFAULT_BASKET

RESULTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs", "portfolio_correlation")

# The 10 currently-validated real strategies. krakenrsi deliberately excluded
# — it's flagged needs_revalidation, not currently a validated strategy.
REAL_STRATEGIES = {
    "realpaper": "smart_swing", "krakenmulti": "multi_signal", "krakenfast": "fast_swing",
    "krakenslow": "slow_swing", "krakenmacd": "macd_cross", "krakenbbreak": "bollinger_breakout",
    "krakenturtle": "turtle_donchian", "krakencci": "cci_momentum",
    "krakenkeltner": "keltner_breakout", "krakenichimoku": "ichimoku_cloud",
}


def _backtest_strategy(strategy_path, coin_bars, cash_per_coin, allow_buy_ts):
    """Run one strategy across every coin over the full common history.
    Returns (pooled_per_bar_equity, per_coin_holding_bool_arrays)."""
    n_bars = min(len(b) for b in coin_bars.values())
    pooled_equity = [0.0] * n_bars
    per_coin_holding = {}
    for product, bars in coin_bars.items():
        bars = bars[-n_bars:]   # tail-align: index i = same calendar date for every coin
        strat = Strategy.load(strategy_path)
        res = engine.run(strat, bars, cash=cash_per_coin, allow_buy_ts=allow_buy_ts)
        for i in range(n_bars):
            pooled_equity[i] += res.equity[i]
        # Reconstruct per-bar "holding this coin" from fills, so concentration
        # analysis can ask "how many strategies are long the same coin right
        # now" without needing engine.py itself to track that (it only knows
        # about one strategy running alone).
        holding = [False] * n_bars
        ts_to_idx = {b.ts: i for i, b in enumerate(bars)}
        open_i = None
        for f in res.fills:
            idx = ts_to_idx.get(f.ts)
            if idx is None:
                continue
            if f.side == "buy" and open_i is None:
                open_i = idx
            elif f.side == "sell" and open_i is not None:
                for j in range(open_i, min(idx + 1, n_bars)):
                    holding[j] = True
                open_i = None
        if open_i is not None:
            for j in range(open_i, n_bars):
                holding[j] = True
        per_coin_holding[product] = holding
    return pooled_equity, per_coin_holding


def _sharpe_maxdd(equity, bars_per_year=365):
    rets = [equity[i] / equity[i - 1] - 1 for i in range(1, len(equity)) if equity[i - 1] > 0]
    if len(rets) > 1:
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        sd = var ** 0.5
        sharpe = (mean / sd) * (bars_per_year ** 0.5) if sd > 0 else 0.0
    else:
        sharpe = 0.0
    peak, max_dd = equity[0], 0.0
    for v in equity:
        peak = max(peak, v)
        max_dd = min(max_dd, v / peak - 1) if peak else max_dd
    return sharpe, max_dd


def analyze(strategies=None, coins=None, granularity=86400, cash_per_strategy=50.0,
           use_regime=True, max_bars=1500, verbose=True):
    strategies = strategies or REAL_STRATEGIES
    coins = coins or DEFAULT_BASKET
    products = [c if "-" in c else f"{c}-USD" for c in coins]
    if verbose:
        print(f"Loading {len(products)}-coin basket for {len(strategies)}-strategy correlation analysis...")
    coin_bars = {p: data.get_bars(p, granularity=granularity, max_bars=max_bars) for p in products}
    allow_buy_ts = regime.risk_on_timestamps("BTC-USD", granularity=granularity,
                                             max_bars=max_bars) if use_regime else None
    cash_per_coin = cash_per_strategy / len(coin_bars)

    equities, holdings_by_strategy = {}, {}
    for account, strat_name in strategies.items():
        path = f"strategies/{strat_name}.json"
        eq, holding = _backtest_strategy(path, coin_bars, cash_per_coin, allow_buy_ts)
        equities[account] = eq
        holdings_by_strategy[account] = holding
        if verbose:
            print(f"  {account}: final ${eq[-1]:.2f} (from ${cash_per_strategy:.2f})")

    names = list(strategies.keys())
    n_bars = min(len(e) for e in equities.values())

    # --- 1. return correlation matrix ---
    daily_returns = {n: [equities[n][i] / equities[n][i - 1] - 1
                         for i in range(1, n_bars) if equities[n][i - 1] > 0] for n in names}
    corr_matrix = {a: {b: (1.0 if a == b else round(risk.correlation(daily_returns[a], daily_returns[b]) or 0.0, 2))
                       for b in names} for a in names}
    pairs = [(a, b) for i, a in enumerate(names) for b in names[names.index(a) + 1:]]
    pairwise = [corr_matrix[a][b] for a, b in pairs]
    avg_corr = sum(pairwise) / len(pairwise) if pairwise else 0.0
    high_corr_pairs = [(a, b, corr_matrix[a][b]) for a, b in pairs if corr_matrix[a][b] > 0.7]

    # --- 2. simultaneous-position / coin concentration ---
    products_list = list(coin_bars.keys())
    coin_concurrent_max, coin_concurrent_bardays = {}, {}
    for product in products_list:
        per_bar_count = [sum(1 for n in names if holdings_by_strategy[n][product][i])
                         for i in range(n_bars)]
        coin_concurrent_max[product.replace("-USD", "")] = max(per_bar_count) if per_bar_count else 0
        coin_concurrent_bardays[product.replace("-USD", "")] = sum(per_bar_count)
    total_bardays = sum(coin_concurrent_bardays.values()) or 1
    coin_concentration_pct = {c: round(v / total_bardays * 100, 1)
                              for c, v in sorted(coin_concurrent_bardays.items(),
                                                 key=lambda kv: -kv[1])}
    max_ever_concurrent = max(coin_concurrent_max.values()) if coin_concurrent_max else 0
    worst_coin = max(coin_concurrent_max, key=coin_concurrent_max.get) if coin_concurrent_max else None

    # --- 3. combined portfolio vs individual: does pooling actually diversify? ---
    combined_equity = [sum(equities[n][i] for n in names) for i in range(n_bars)]
    combined_start = cash_per_strategy * len(names)
    combined_sharpe, combined_maxdd = _sharpe_maxdd(combined_equity)
    individual_sharpes = []
    individual_maxdds = []
    for n in names:
        s, dd = _sharpe_maxdd(equities[n][:n_bars])
        individual_sharpes.append(s)
        individual_maxdds.append(dd)
    avg_individual_sharpe = sum(individual_sharpes) / len(individual_sharpes)
    avg_individual_maxdd = sum(individual_maxdds) / len(individual_maxdds)
    # If strategies were independent, pooling N of them should shrink relative
    # drawdown roughly like 1/sqrt(N) vs the average individual drawdown, and
    # raise the Sharpe ratio (averaging out idiosyncratic noise). If combined
    # numbers look about the SAME as the average individual ones, there was no
    # real diversification benefit — consistent with high correlation.
    expected_dd_if_independent = avg_individual_maxdd / (len(names) ** 0.5)

    summary = {
        "n_strategies": len(names), "strategies": names,
        "avg_pairwise_correlation": round(avg_corr, 2),
        "high_correlation_pairs (>0.7)": [(a, b, c) for a, b, c in high_corr_pairs],
        "max_ever_concurrent_positions_same_coin": max_ever_concurrent,
        "worst_concentration_coin": worst_coin,
        "coin_concentration_pct_of_all_position_bardays": coin_concentration_pct,
        "combined_portfolio_sharpe": round(combined_sharpe, 2),
        "avg_individual_sharpe": round(avg_individual_sharpe, 2),
        "combined_portfolio_max_dd_pct": round(combined_maxdd * 100, 1),
        "avg_individual_max_dd_pct": round(avg_individual_maxdd * 100, 1),
        "expected_max_dd_pct_if_truly_independent": round(expected_dd_if_independent * 100, 1),
        "combined_final_equity": round(combined_equity[-1], 2),
        "combined_start_equity": combined_start,
        "combined_return_pct": round((combined_equity[-1] / combined_start - 1) * 100, 2),
    }
    if avg_corr > 0.7:
        summary["verdict"] = ("HIGH CORRELATION: these strategies are largely the same bet. "
                              "Running all 10 is much closer to running one strategy at 10x size "
                              "than to genuine diversification.")
    elif avg_corr > 0.4:
        summary["verdict"] = ("MODERATE CORRELATION: meaningful shared risk. Some diversification "
                              "benefit exists but is smaller than the strategy count suggests.")
    else:
        summary["verdict"] = "LOW CORRELATION: genuine diversification — the count of strategies is real."

    if verbose:
        print(f"\n=== Correlation matrix (daily returns) ===")
        header = "         " + " ".join(f"{n[:8]:>9}" for n in names)
        print(header)
        for a in names:
            print(f"{a[:8]:<9}" + " ".join(f"{corr_matrix[a][b]:>9.2f}" for b in names))
        print(f"\nAverage pairwise correlation: {avg_corr:.2f}")
        print(f"High-correlation pairs (>0.7): {len(high_corr_pairs)}")
        print(f"\n=== Coin concentration (% of all strategy-coin-position-days) ===")
        for c, pct in list(coin_concentration_pct.items())[:8]:
            print(f"  {c:<8} {pct:>5.1f}%")
        print(f"Max coins ever held by ALL {len(names)} strategies simultaneously on ONE coin: "
              f"{max_ever_concurrent} (worst: {worst_coin})")
        print(f"\n=== Combined portfolio (${combined_start:.0f}) vs individual accounts ===")
        print(f"Combined Sharpe: {combined_sharpe:.2f}   avg individual Sharpe: {avg_individual_sharpe:.2f}")
        print(f"Combined max DD: {combined_maxdd*100:.1f}%   avg individual max DD: {avg_individual_maxdd*100:.1f}%"
              f"   (expected if truly independent: {expected_dd_if_independent*100:.1f}%)")
        print(f"\n{summary['verdict']}")

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_path = os.path.join(RESULTS_DIR, f"analysis_{int(time.time())}.json")
    json.dump({"summary": summary, "correlation_matrix": corr_matrix}, open(out_path, "w"), indent=2)
    if verbose:
        print(f"\nSaved -> {out_path}")
    return summary, corr_matrix


# ---------------------------------------------------------------------------
# Cross-granularity: are the 4h challengers a genuinely different bet from the
# daily champions, or the same trade at a finer sampling rate?
# ---------------------------------------------------------------------------
FOUR_H = ["multi", "turtle", "keltner", "bbreak", "sma", "cci", "ichimoku", "rsi", "macd"]
FOUR_H_PASSERS = ["multi", "turtle", "keltner", "bbreak"]
SAME_FAMILY = {"multi": "krakenmulti", "turtle": "krakenturtle", "keltner": "krakenkeltner",
               "bbreak": "krakenbbreak", "sma": "realpaper", "cci": "krakencci",
               "ichimoku": "krakenichimoku", "macd": "krakenmacd", "rsi": "krakenrsi"}


def _dated_daily_equity(strategy_path, coin_bars, cash_per_coin, allow_buy_ts, four_hour):
    """{day_start_ts: pooled_equity_at_that_day's_close}. For 4h bars the day's
    close is the 20:00-UTC bar (it closes at 24:00), so both granularities are
    sampled at the same instant and can be joined by date."""
    n = min(len(b) for b in coin_bars.values())
    ts_ref = [b.ts for b in list(coin_bars.values())[0][-n:]]
    pooled = [0.0] * n
    for product, bars in coin_bars.items():
        res = engine.run(Strategy.load(strategy_path), bars[-n:], cash=cash_per_coin,
                         allow_buy_ts=allow_buy_ts)
        for i in range(n):
            pooled[i] += res.equity[i]
    out = {}
    for i, t in enumerate(ts_ref):
        if four_hour:
            if t % 86400 == 72000:
                out[t - 72000] = pooled[i]
        else:
            out[t] = pooled[i]
    return out


def _series_corr(a, b):
    days = sorted(set(a) & set(b))
    ra = [a[days[i]] / a[days[i - 1]] - 1 for i in range(1, len(days)) if a[days[i - 1]] > 0]
    rb = [b[days[i]] / b[days[i - 1]] - 1 for i in range(1, len(days)) if b[days[i - 1]] > 0]
    return risk.correlation(ra, rb), len(days)


def analyze_cross_granularity(cash_per_strategy=50.0, verbose=True):
    products = [f"{c}-USD" for c in DEFAULT_BASKET]
    daily_bars = {p: data.get_bars(p, granularity=86400, max_bars=1500) for p in products}
    four_bars = {p: data.get_bars(p, granularity=14400, max_bars=6570) for p in products}
    allow_d = regime.risk_on_timestamps("BTC-USD", granularity=86400, max_bars=1500)
    allow_4 = regime.risk_on_timestamps("BTC-USD", granularity=14400, max_bars=6570)
    per_coin_d = cash_per_strategy / len(daily_bars)

    daily = {acct: _dated_daily_equity(f"strategies/{name}.json", daily_bars, per_coin_d, allow_d, False)
             for acct, name in REAL_STRATEGIES.items()}
    four = {fam: _dated_daily_equity(f"strategies/c4h_{fam}.json", four_bars, per_coin_d, allow_4, True)
            for fam in FOUR_H if os.path.exists(f"strategies/c4h_{fam}.json")}

    rows, matrix = {}, {}
    for fam, s4 in four.items():
        cors = {}
        for acct, sd in daily.items():
            c, n_days = _series_corr(s4, sd)
            cors[acct] = round(c, 2) if c is not None else None
        vals = [v for v in cors.values() if v is not None]
        same = SAME_FAMILY.get(fam)
        rows[fam] = {"avg_corr_to_daily_champions": round(sum(vals) / len(vals), 2),
                     "max_corr": max(vals), "min_corr": min(vals),
                     "corr_to_same_family_daily": cors.get(same), "overlap_days": n_days}
        matrix[fam] = cors

    # does adding the passers to the 10 daily champions change portfolio risk?
    def combine(series_list):
        days = sorted(set.intersection(*[set(s) for s in series_list]))
        return [sum(s[d] for s in series_list) for d in days]
    base = combine(list(daily.values()))
    passers = [four[f] for f in FOUR_H_PASSERS if f in four]
    plus = combine(list(daily.values()) + passers)
    p_sh, p_dd = _sharpe_maxdd(base)
    q_sh, q_dd = _sharpe_maxdd(plus)
    only4 = combine(passers) if passers else []
    o_sh, o_dd = _sharpe_maxdd(only4) if only4 else (0, 0)
    summary = {"per_4h_family": rows,
               "daily_only": {"sharpe": round(p_sh, 2), "max_dd_pct": round(p_dd * 100, 1)},
               "daily_plus_4h_passers": {"sharpe": round(q_sh, 2), "max_dd_pct": round(q_dd * 100, 1)},
               "4h_passers_alone": {"sharpe": round(o_sh, 2), "max_dd_pct": round(o_dd * 100, 1)}}
    if verbose:
        print(f"{'4h family':<10}{'avg corr':>9}{'min':>6}{'max':>6}{'same-family daily':>19}{'days':>6}")
        for fam, r in rows.items():
            print(f"{fam:<10}{r['avg_corr_to_daily_champions']:>9}{r['min_corr']:>6}{r['max_corr']:>6}"
                  f"{str(r['corr_to_same_family_daily']):>19}{r['overlap_days']:>6}")
        print(f"\nPortfolio of the 10 daily champions:        Sharpe {p_sh:.2f}  maxDD {p_dd*100:.1f}%")
        print(f"Same + the 4 passing 4h challengers:        Sharpe {q_sh:.2f}  maxDD {q_dd*100:.1f}%")
        print(f"The 4 passing 4h challengers alone:         Sharpe {o_sh:.2f}  maxDD {o_dd*100:.1f}%")
    os.makedirs(RESULTS_DIR, exist_ok=True)
    json.dump({"summary": summary, "matrix": matrix},
              open(os.path.join(RESULTS_DIR, f"cross_granularity_{int(time.time())}.json"), "w"), indent=2)
    return summary, matrix
