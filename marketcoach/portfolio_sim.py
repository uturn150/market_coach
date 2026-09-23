"""Shared-capital portfolio simulator.

The 10 champions normally run as ten isolated $50 accounts. This runs them
TOGETHER against ONE cash pool (default $500 = 10 x $50), the way a single
real account would actually hold them, and measures what isolation hides:
combined drawdown, total crypto exposure, how many positions overlap, how
much of the book is really one coin or one strategy family, and how many
INDEPENDENT bets the fleet really contains.

The isolated accounts are untouched — this is a separate, read-only research
tool that writes only to logs/portfolio_sim/.

Mechanics (mirror engine.run so a lone sleeve behaves identically):
  - signals are computed at bar close i and executed at bar i+1's OPEN, at the
    strategy's own itemized side_rate (real fee + spread + slippage) — no
    lookahead; trailing/hard stops use the same peak/avg_cost rules.
  - every (strategy, coin) pair is a SLEEVE. Entry size = pool equity at the
    prior close / total sleeves, capped by cash actually available; entries
    the pool can't fund are counted as `blocked_by_cash`, never faked.
  - each strategy keeps its own cash LEDGER (its buys/sells only) so P&L can be
    attributed per strategy and per coin, even though sizing uses pool equity.
  - `risk_manager` is a hook for the Master Risk Manager (spec item #10): if
    given, `risk_manager.approve(request, state)` returns a fraction in [0,1]
    of the requested entry size that is allowed (0 = veto). Default None =
    unconstrained, which is the baseline every constraint gets measured against.

Timeline: daily bars, all coins tail-aligned so index i is the same calendar
date for every coin (the alignment fix from the walk-forward bug).
"""
import json, os, time
from collections import defaultdict
from . import data, regime, risk
from .strategy import Strategy
from .portfolio_optimize import DEFAULT_BASKET
from .portfolio_correlation import REAL_STRATEGIES

RESULTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "portfolio_sim")

# What each strategy fundamentally IS, for family-concentration reporting.
FAMILY = {"smart_swing": "MA-crossover trend", "fast_swing": "MA-crossover trend",
          "slow_swing": "MA-crossover trend", "macd_cross": "momentum oscillator",
          "cci_momentum": "momentum oscillator", "bollinger_breakout": "volatility breakout",
          "turtle_donchian": "channel breakout", "keltner_breakout": "volatility breakout",
          "multi_signal": "composite", "ichimoku_cloud": "composite"}


class _Sleeve:
    __slots__ = ("strat", "name", "coin", "units", "avg_cost", "peak", "pending", "sc")

    def __init__(self, strat, name, coin):
        self.strat, self.name, self.coin = strat, name, coin
        self.units = self.avg_cost = self.peak = 0.0
        self.pending = None   # "buy" | "sell" decided at the prior close
        self.sc = 0.0         # this sleeve's own cash (only used by sizing="sleeve")


def simulate(strategies=None, coins=None, pool_cash=None, cash_per_strategy=50.0,
             use_regime=True, max_bars=1500, risk_manager=None, verbose=True, sizing="pool"):
    strategies = strategies or REAL_STRATEGIES
    coins = coins or DEFAULT_BASKET
    products = [c if "-" in c else f"{c}-USD" for c in coins]
    pool_cash = pool_cash if pool_cash is not None else cash_per_strategy * len(strategies)

    coin_bars = {p: data.get_bars(p, granularity=86400, max_bars=max_bars) for p in products}
    n = min(len(b) for b in coin_bars.values())
    bars = {p: b[-n:] for p, b in coin_bars.items()}                    # date-aligned tails
    allow = regime.risk_on_timestamps("BTC-USD", 86400, 200, max_bars) if use_regime else None
    series = {p: {"c": [b.close for b in bars[p]], "v": [b.volume for b in bars[p]],
                  "h": [b.high for b in bars[p]], "l": [b.low for b in bars[p]]} for p in products}

    sleeves = []
    for acct, sname in strategies.items():
        for p in products:
            sleeves.append(_Sleeve(Strategy.load(f"strategies/{sname}.json"), acct, p))
    n_sleeves = len(sleeves)
    accounts = list(strategies)
    if sizing == "sleeve":            # validation mode: every sleeve is an isolated mini-account
        for sl in sleeves:
            sl.sc = pool_cash / n_sleeves
    ledger = {a: pool_cash / len(accounts) for a in accounts}            # per-strategy cash
    cash = pool_cash
    fees_paid = defaultdict(float)
    blocked_by_cash = vetoed = entries = 0
    realized = defaultdict(float)                                        # by coin, net of costs
    realized_strat = defaultdict(float)

    eq_series, exposure_series, npos_series = [], [], []
    coin_value = {p: [] for p in products}
    strat_value = {a: [] for a in accounts}
    strat_npos = {a: [] for a in accounts}
    family_value = defaultdict(list)
    dates = [b.ts for b in bars[products[0]]]
    prev_equity = pool_cash

    for i in range(n):
        # 1) execute yesterday's decisions at today's open
        for sl in sleeves:
            if sl.pending is None:
                continue
            px_open = bars[sl.coin][i].open
            rate = sl.strat.side_rate
            if sl.pending == "buy" and sl.units == 0:
                want = sl.sc if sizing == "sleeve" else prev_equity / n_sleeves
                if risk_manager is not None:
                    frac = risk_manager.approve(
                        {"strategy": sl.name, "coin": sl.coin, "notional": want, "bar": i},
                        {"cash": cash, "equity": prev_equity,
                         "positions": [(s.name, s.coin, s.units * bars[s.coin][i].open) for s in sleeves if s.units > 0]})
                    if frac <= 0:
                        vetoed += 1
                        sl.pending = None
                        continue
                    want *= min(1.0, frac)
                spend = min(cash, want)
                if spend < want * 0.999:
                    blocked_by_cash += 1
                if spend > 1e-9:
                    fill_px = px_open * (1 + rate)
                    sl.units = spend / fill_px
                    sl.avg_cost, sl.peak = px_open, 0.0
                    cash -= spend
                    sl.sc -= spend
                    ledger[sl.name] -= spend
                    fees_paid[sl.name] += spend * rate
                    entries += 1
            elif sl.pending == "sell" and sl.units > 0:
                proceeds = sl.units * px_open * (1 - rate)
                cost_basis = sl.units * sl.avg_cost * (1 + rate)
                pnl = proceeds - cost_basis
                realized[sl.coin] += pnl
                realized_strat[sl.name] += pnl
                fees_paid[sl.name] += sl.units * px_open * rate
                cash += proceeds
                sl.sc += proceeds
                ledger[sl.name] += proceeds
                sl.units = sl.avg_cost = sl.peak = 0.0
            sl.pending = None

        # 2) risk exits vs today's close (acted on next open), then 3) mark to market
        close = {p: series[p]["c"][i] for p in products}
        for sl in sleeves:
            if sl.units > 0 and sl.avg_cost > 0:
                c = close[sl.coin]
                sl.peak = max(sl.peak, c) if sl.peak else c
                chg = c / sl.avg_cost - 1
                r = sl.strat.risk
                stop, take, trail = r.get("stop_loss_pct"), r.get("take_profit_pct"), r.get("trailing_stop_pct")
                if ((trail is not None and c <= sl.peak * (1 - abs(trail)))
                        or (stop is not None and chg <= -abs(stop))
                        or (take is not None and chg >= abs(take))):
                    sl.pending = "sell"
        values = defaultdict(float)
        for sl in sleeves:
            if sl.units > 0:
                values[(sl.name, sl.coin)] += sl.units * close[sl.coin]
        invested = sum(values.values())
        equity = cash + invested
        eq_series.append(equity)
        exposure_series.append(invested / equity if equity > 0 else 0.0)
        npos_series.append(sum(1 for s in sleeves if s.units > 0))
        for p in products:
            coin_value[p].append(sum(v for (a, c), v in values.items() if c == p))
        fam_now = defaultdict(float)
        for a in accounts:
            v = sum(val for (aa, c), val in values.items() if aa == a)
            strat_value[a].append(ledger[a] + v)
            strat_npos[a].append(sum(1 for s in sleeves if s.name == a and s.units > 0))
            fam_now[FAMILY[strategies[a]]] += v
        for fam in set(FAMILY.values()):
            family_value[fam].append(fam_now.get(fam, 0.0))
        prev_equity = equity

        # 3b) pool-level risk state machine (drawdown breaker, loss block, kill switch)
        if risk_manager is not None:
            act = risk_manager.on_bar({"i": i, "equity": equity, "cash": cash,
                                       "strategy_equity": {a: strat_value[a][-1] for a in accounts}})
            if act.get("liquidate"):
                for sl in sleeves:
                    if sl.units > 0:
                        sl.pending = "sell"

        # 4) decide today's signals (executed tomorrow)
        if i < n - 1:
            for sl in sleeves:
                if sl.pending is not None:
                    continue
                sr = series[sl.coin]
                for act in sl.strat.signals_at(i, sr["c"], sr["v"], sr["h"], sr["l"]):
                    if act["action"] == "buy" and sl.units == 0:
                        if allow is None or bars[sl.coin][i].ts in allow:
                            sl.pending = "buy"; break
                    elif act["action"] == "sell" and sl.units > 0:
                        sl.pending = "sell"; break

    return _report(pool_cash, dates, eq_series, exposure_series, npos_series, coin_value, strat_value,
                   strat_npos, family_value, fees_paid, realized, realized_strat, blocked_by_cash, vetoed,
                   entries, strategies, n_sleeves, verbose, risk_manager)


def _rets(eq):
    return [eq[i] / eq[i - 1] - 1 for i in range(1, len(eq)) if eq[i - 1] > 0]


def _dd_series(eq):
    peak, out = eq[0], []
    for v in eq:
        peak = max(peak, v)
        out.append(v / peak - 1 if peak else 0.0)
    return out


def _sharpe(rets):
    if len(rets) < 2:
        return 0.0
    m = sum(rets) / len(rets)
    sd = (sum((r - m) ** 2 for r in rets) / (len(rets) - 1)) ** 0.5
    return (m / sd) * (365 ** 0.5) if sd > 0 else 0.0


def _report(pool_cash, dates, eq, expo, npos, coin_value, strat_value, strat_npos, family_value,
            fees_paid, realized, realized_strat, blocked, vetoed, entries, strategies, n_sleeves, verbose,
            rm=None):
    names = list(strategies)
    dd = _dd_series(eq)
    total_ret = eq[-1] / pool_cash - 1
    # concentration: share of INVESTED value, averaged over days with a book
    days = [i for i in range(len(eq)) if sum(cv[i] for cv in coin_value.values()) > 1e-9]
    def avg_share(vals_by_key):
        out = {}
        for k, vs in vals_by_key.items():
            out[k] = sum(vs[i] / max(1e-12, sum(v[i] for v in vals_by_key.values())) for i in days) / max(1, len(days))
        return out
    coin_share = avg_share(coin_value)
    fam_share = avg_share(family_value)
    max_single_coin = max((max(coin_value[p][i] / eq[i] for i in range(len(eq)) if eq[i] > 0), p) for p in coin_value)
    # cross-strategy: return / drawdown / open-position correlations
    rets = {a: _rets(strat_value[a]) for a in names}
    dds = {a: _dd_series(strat_value[a]) for a in names}
    def avg_pair(series):
        vals = []
        for x in range(len(names)):
            for y in range(x + 1, len(names)):
                c = risk.correlation(series[names[x]], series[names[y]])
                if c is not None:
                    vals.append(c)
        return sum(vals) / len(vals) if vals else 0.0
    ret_corr, dd_corr = avg_pair(rets), avg_pair(dds)
    pos_corr = avg_pair({a: [float(v) for v in strat_npos[a]] for a in names})
    n_eff = len(names) / (1 + (len(names) - 1) * max(0.0, ret_corr))
    indiv = {a: {"return_pct": round((strat_value[a][-1] / (pool_cash / len(names)) - 1) * 100, 1),
                 "max_dd_pct": round(min(_dd_series(strat_value[a])) * 100, 1)} for a in names}
    top_coins = sorted(coin_share.items(), key=lambda kv: -kv[1])[:5]
    rep = {
        "pool_cash": pool_cash, "n_strategies": len(names), "n_sleeves": n_sleeves,
        "period": [time.strftime("%Y-%m-%d", time.gmtime(dates[0])), time.strftime("%Y-%m-%d", time.gmtime(dates[-1]))],
        "final_equity": round(eq[-1], 2), "total_return_pct": round(total_ret * 100, 1),
        "sharpe": round(_sharpe(_rets(eq)), 2), "max_drawdown_pct": round(min(dd) * 100, 1),
        "exposure": {"avg_invested_pct": round(sum(expo) / len(expo) * 100, 1),
                     "max_invested_pct": round(max(expo) * 100, 1),
                     "avg_open_positions": round(sum(npos) / len(npos), 1), "max_open_positions": max(npos),
                     "days_fully_in_cash_pct": round(sum(1 for e in expo if e < 0.005) / len(expo) * 100, 1)},
        "coin_concentration": {"avg_share_top5_pct": {c: round(s * 100, 1) for c, s in top_coins},
                               "largest_single_coin_pct_of_equity": round(max_single_coin[0] * 100, 1),
                               "largest_single_coin": max_single_coin[1].replace("-USD", "")},
        "family_concentration_avg_share_pct": {f: round(s * 100, 1) for f, s in sorted(fam_share.items(), key=lambda kv: -kv[1])},
        "cross_strategy": {"avg_return_correlation": round(ret_corr, 2), "avg_drawdown_correlation": round(dd_corr, 2),
                           "avg_open_position_correlation": round(pos_corr, 2),
                           "effective_independent_strategies": round(n_eff, 2)},
        "pnl_by_strategy_realized_usd": {a: round(realized_strat[a], 2) for a in names},
        "pnl_by_coin_realized_usd": {c.replace("-USD", ""): round(v, 2) for c, v in sorted(realized.items(), key=lambda kv: -kv[1])},
        "fees_paid_usd": round(sum(fees_paid.values()), 2),
        "fees_as_pct_of_start": round(sum(fees_paid.values()) / pool_cash * 100, 1),
        "entries": entries, "blocked_by_cash": blocked, "vetoed_by_risk_manager": vetoed,
        "individual_strategy_stats": indiv,
    }
    half = len(eq) // 2
    def seg(a, b):
        e = eq[a:b]
        return {"return_pct": round((e[-1] / e[0] - 1) * 100, 1), "max_dd_pct": round(min(_dd_series(e)) * 100, 1)}
    rep["first_half"], rep["second_half"] = seg(0, half), seg(half, len(eq))
    rep["_series"] = {"dates": list(dates), "equity": [round(x, 4) for x in eq]}   # for weekly/green analysis
    if rm is not None:
        rep["risk_manager"] = rm.summary()
    os.makedirs(RESULTS_DIR, exist_ok=True)
    json.dump(rep, open(os.path.join(RESULTS_DIR, f"sim_{int(time.time())}.json"), "w"), indent=2)
    if verbose:
        print(json.dumps({k: v for k, v in rep.items() if k not in ("individual_strategy_stats", "pnl_by_coin_realized_usd")}, indent=2))
    return rep


def write_baseline():
    """Unconstrained shared-pool run, saved to a FIXED name the dashboard
    reads (simulate() otherwise writes a timestamped file per call, including
    every risk-manager experiment)."""
    rep = simulate(verbose=False, sizing="pool")
    os.makedirs(RESULTS_DIR, exist_ok=True)
    json.dump(rep, open(os.path.join(RESULTS_DIR, "baseline.json"), "w"), indent=2)
    return rep
