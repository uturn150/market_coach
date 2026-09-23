"""Monte Carlo robustness testing — answers "does this strategy remain viable
when reality is slightly worse than the backtest," not "what's the best case."

Two complementary techniques, chosen for what each is actually good at:

1. BOOTSTRAP RESAMPLING (thousands of fast simulations): take the REAL
   sequence of round-trip trade returns the strategy actually produced across
   the whole basket/history, then run thousands of simulated alternate
   histories by resampling those trades (with replacement) in random order,
   occasionally dropping one entirely (a missed/failed entry), and shaving a
   bit more off each one (worse fills/slippage/delayed execution than the
   backtest already assumed). This is the standard, computationally tractable
   way to ask "how much does the exact sequence of these historical trades
   matter, and how much worse can plausible execution reality make things."
   It does NOT invent new trades or a different market — it's asking how
   fragile the ALREADY-OBSERVED edge is to ordering and execution noise.

2. DISCRETE STRESS SCENARIOS (a handful of full re-simulations, not
   thousands): actually re-run the real engine with harsher cost assumptions
   (1.5x/2x fees, 2x spread+slippage) and small parameter perturbations. This
   catches things bootstrap resampling can't — e.g. a strategy that only
   clears the fee gate because fees happen to be assumed exactly right, or
   one whose edge disappears the moment its RSI period shifts by 1.

Percentile framing throughout, never a single "expected" number — a strategy
that looks great on its median outcome but has an ugly 5th-percentile tail or
a high probability of losing money should be rejected even if the backtest
itself looked clean.
"""
import json, os, random, re, time
from . import data, engine, metrics, regime, kraken_fees
from .strategy import Strategy
from .portfolio_optimize import DEFAULT_BASKET, _pooled

RESULTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs", "monte_carlo")


def collect_trades(strategy_path, coin_bars, cash_per_coin, allow_buy_ts):
    """Real round-trip NET returns (fractions) from an actual full-history
    backtest across the whole basket — the raw material bootstrap resampling
    draws from. Per-coin (not pooled fills) for the same reason walk-forward
    had to fix: FIFO trade-matching breaks across a mixed multi-coin stream."""
    trips = []
    for product, bars in coin_bars.items():
        strat = Strategy.load(strategy_path)
        res = engine.run(strat, bars, cash=cash_per_coin, allow_buy_ts=allow_buy_ts)
        trips.extend(net for _, net in metrics.roundtrips(res, strat.side_rate))
    return trips


def bootstrap(trade_returns, n_sims=2000, n_trades_per_sim=None, starting_cash=50.0,
              n_sleeves=24, extra_degradation_bps=(0, 30), drop_prob=0.05, seed=1):
    """Run n_sims simulated alternate histories from the real trade-return
    pool. Each trade drawn is: possibly dropped entirely (drop_prob, a missed
    or failed entry), then given EXTRA adverse slippage on top of whatever the
    backtest already charged (uniform in extra_degradation_bps) — modeling
    worse fills/execution lag than the backtest assumed, never better.

    Splits capital across n_sleeves PARALLEL compounding streams (matching how
    the real account actually works — ~$50 divided across a 24-coin basket,
    not one all-in bet compounding sequentially). This matters a lot: naively
    compounding all N trades on ONE equity stream produces wild, unrealistic
    blowup/ruin outcomes purely from multiplicative variance, because it
    pretends every trade risks the entire portfolio in sequence — the actual
    strategy never does that. (Caught this before trusting any output: a
    first version showed a median outcome of +1600% alongside a 96% median
    drawdown, which is impossible for a real strategy and was purely an
    artifact of the wrong compounding model.)

    KNOWN LIMITATION, stated plainly rather than hidden: each sleeve's trades
    are drawn independently from the pooled trade-return pool, which assumes
    coins' trades are uncorrelated with each other. The portfolio correlation
    analysis in this project (2026-09-19) found the OPPOSITE is often true —
    a shared regime filter and correlated crypto market mean many coins can
    lose together in a real crash. This bootstrap likely UNDERSTATES true
    tail risk for that reason; a block-bootstrap that resamples whole
    historical PERIODS across all sleeves at once (preserving same-day
    correlation) would be a more faithful future upgrade. Treat the p5/loss-
    probability numbers here as a floor on fragility, not a ceiling.

    Returns a list of {final_equity, max_drawdown, n_trades_used} dicts."""
    if not trade_returns:
        return []
    n_trades_per_sim = n_trades_per_sim or len(trade_returns)
    rounds = max(1, n_trades_per_sim // n_sleeves)
    cash_per_sleeve = starting_cash / n_sleeves
    rng = random.Random(seed)
    results = []
    for _ in range(n_sims):
        sleeve_equity = [cash_per_sleeve] * n_sleeves
        portfolio_path = [starting_cash]
        used = 0
        for _ in range(rounds):
            for s in range(n_sleeves):
                trade = rng.choice(trade_returns)
                if rng.random() < drop_prob:
                    continue  # missed/failed entry — no effect at all
                extra_bps = rng.uniform(*extra_degradation_bps)
                degraded = trade - extra_bps / 10_000.0
                sleeve_equity[s] *= (1 + degraded)
                used += 1
            portfolio_path.append(sum(sleeve_equity))
        peak, max_dd = portfolio_path[0], 0.0
        for v in portfolio_path:
            peak = max(peak, v)
            max_dd = min(max_dd, v / peak - 1) if peak > 0 else max_dd
        results.append({"final_equity": sum(sleeve_equity), "max_drawdown": max_dd, "n_trades_used": used})
    return results


def summarize_distribution(sims, starting_cash, buy_hold_return_pct):
    """The percentile view the spec explicitly asked for — median AND the
    ugly tail, never just an average."""
    if not sims:
        return {"n_sims": 0, "note": "no trades to resample — nothing to stress-test"}
    finals = sorted(s["final_equity"] for s in sims)
    returns_pct = sorted((f / starting_cash - 1) * 100 for f in finals)
    dds = sorted(s["max_drawdown"] * 100 for s in sims)  # most negative first
    n = len(sims)
    def pct(sorted_list, p):
        idx = min(n - 1, max(0, int(p / 100 * n)))
        return sorted_list[idx]
    prob_loss = sum(1 for r in returns_pct if r < 0) / n
    prob_underperform_hold = sum(1 for r in returns_pct if r < buy_hold_return_pct) / n
    return {
        "n_sims": n,
        "median_return_pct": round(pct(returns_pct, 50), 2),
        "p5_return_pct": round(pct(returns_pct, 5), 2),
        "p95_return_pct": round(pct(returns_pct, 95), 2),
        "median_max_drawdown_pct": round(pct(dds, 50), 1),
        "p5_max_drawdown_pct": round(pct(dds, 5), 1),  # worst-of-the-worst tail
        "probability_of_losing_money": round(prob_loss, 3),
        "probability_of_underperforming_hold": round(prob_underperform_hold, 3),
        "buy_hold_return_pct": round(buy_hold_return_pct, 2),
    }


def _perturb_periods(spec, delta):
    """Shift every embedded indicator period (the number after each ':' in a
    ref like 'rsi:10' or 'sma:40:160') by `delta`, and any bare numeric
    threshold in a rule's left/right by a proportional nudge. Generic across
    strategy shapes rather than strategy-specific, at the cost of being an
    approximation for thresholds that aren't really "periods" (e.g. RSI
    30/70 levels) — flagged in the report, not hidden."""
    out = json.loads(json.dumps(spec))  # deep copy via round-trip, simplest correct option here

    def bump_ref(ref):
        if not isinstance(ref, str) or ":" not in ref:
            return ref
        name, *parts = ref.split(":")
        bumped = [str(max(2, int(p) + delta)) if p.lstrip("-").isdigit() else p for p in parts]
        return ":".join([name] + bumped)

    def walk_when(when):
        when["left"] = bump_ref(when.get("left"))
        when["right"] = bump_ref(when.get("right"))

    for rule in out.get("rules", []):
        walk_when(rule["when"])
    for sig in out.get("entry", {}).get("signals", []):
        walk_when(sig["when"])
    return out


def stress_scenarios(strategy_path, coin_bars, allow_buy_ts, cash_per_strategy=50.0, split=0.6):
    """A handful of full re-simulations under harsher-than-backtested
    assumptions. Reuses the SAME _pooled() fee+edge gate every other
    validation in this project uses, so 'PASS' here means the exact same
    thing it has meant everywhere else."""
    base_spec = json.load(open(strategy_path))
    fees = kraken_fees.get_fee_bps("XXBTZUSD")
    scenarios = {}

    def run_scenario(name, spec):
        r = _pooled(spec, coin_bars, cash=cash_per_strategy, split=split, allow_buy_ts=allow_buy_ts)
        if r is None:
            scenarios[name] = {"result": "no trades"}
            return
        scenarios[name] = {
            "fee_gate_pass": r["fee_gate_ok"], "edge_gate_pass": r["out_excess"] > 0,
            "out_excess_pct": round(r["out_excess"] * 100, 1), "n_in": r["n_in"],
        }

    baseline = json.loads(json.dumps(base_spec))
    baseline.setdefault("costs", {})["taker_fee_bps"] = fees["taker_bps"]
    baseline["costs"]["maker_fee_bps"] = fees["maker_bps"]
    run_scenario("baseline_real_fee", baseline)

    fee_1_5x = json.loads(json.dumps(baseline))
    fee_1_5x["costs"]["taker_fee_bps"] *= 1.5
    fee_1_5x["costs"]["maker_fee_bps"] *= 1.5
    run_scenario("fees_1.5x", fee_1_5x)

    fee_2x = json.loads(json.dumps(baseline))
    fee_2x["costs"]["taker_fee_bps"] *= 2.0
    fee_2x["costs"]["maker_fee_bps"] *= 2.0
    run_scenario("fees_2x", fee_2x)

    spread_2x = json.loads(json.dumps(baseline))
    spread_2x["costs"]["half_spread_bps"] = spread_2x["costs"].get("half_spread_bps", 5) * 2
    spread_2x["costs"]["slippage_bps"] = spread_2x["costs"].get("slippage_bps", 3) * 2
    run_scenario("spread_slippage_2x", spread_2x)

    run_scenario("params_tighter", _perturb_periods(baseline, -2))
    run_scenario("params_looser", _perturb_periods(baseline, +2))

    passed = sum(1 for s in scenarios.values()
                if s.get("fee_gate_pass") and s.get("edge_gate_pass"))
    return scenarios, passed, len(scenarios)


def run(strategy_path, coins=None, granularity=86400, cash=50.0, use_regime=True,
       max_bars=1500, n_sims=2000, verbose=True):
    coins = coins or DEFAULT_BASKET
    products = [c if "-" in c else f"{c}-USD" for c in coins]
    if verbose:
        print(f"Loading {len(products)}-coin basket for Monte Carlo robustness test...")
    coin_bars = {p: data.get_bars(p, granularity=granularity, max_bars=max_bars) for p in products}
    allow_buy_ts = regime.risk_on_timestamps("BTC-USD", granularity=granularity,
                                             max_bars=max_bars) if use_regime else None
    cash_per_coin = cash / len(coin_bars)

    trades = collect_trades(strategy_path, coin_bars, cash_per_coin, allow_buy_ts)
    bh_start = cash
    bh_end = sum(cash_per_coin * (bars[-1].close / bars[0].close) for bars in coin_bars.values())
    buy_hold_return_pct = (bh_end / bh_start - 1) * 100

    sims = bootstrap(trades, n_sims=n_sims, starting_cash=cash)
    dist = summarize_distribution(sims, cash, buy_hold_return_pct)

    scenarios, n_passed, n_total = stress_scenarios(strategy_path, coin_bars, allow_buy_ts, cash)

    verdict_bits = []
    if dist.get("n_sims"):
        if dist["p5_return_pct"] < -30 or dist["probability_of_losing_money"] > 0.3:
            verdict_bits.append("FRAGILE: bad-tail outcomes are severe or common")
        else:
            verdict_bits.append("Bootstrap distribution looks survivable")
        if dist["probability_of_underperforming_hold"] > 0.5:
            verdict_bits.append("underperforms hold in the MAJORITY of simulated paths")
    if n_total and n_passed < n_total:
        verdict_bits.append(f"only {n_passed}/{n_total} stress scenarios still clear both gates")
    verdict = "; ".join(verdict_bits) or "insufficient trade history to judge robustness"

    summary = {"strategy": strategy_path, "n_real_trades": len(trades),
              "distribution": dist, "stress_scenarios": scenarios,
              "stress_scenarios_passed": f"{n_passed}/{n_total}", "verdict": verdict}

    if verbose:
        print(f"\nReal trades collected: {len(trades)}")
        print(f"Buy & hold over same period: {buy_hold_return_pct:+.1f}%")
        print(f"\n=== Bootstrap ({n_sims} simulations) ===")
        for k, v in dist.items():
            print(f"  {k}: {v}")
        print(f"\n=== Stress scenarios ===")
        for name, s in scenarios.items():
            print(f"  {name}: {s}")
        print(f"\nVERDICT: {verdict}")

    os.makedirs(RESULTS_DIR, exist_ok=True)
    name = os.path.basename(strategy_path).replace(".json", "")
    out_path = os.path.join(RESULTS_DIR, f"{name}_{int(time.time())}.json")
    json.dump(summary, open(out_path, "w"), indent=2)
    if verbose:
        print(f"\nSaved -> {out_path}")
    return summary
