"""Evidence-based graduation report. REPORTS ONLY — nothing in this file can
open an account, place an order, change a flag, or enable real trading. The
strongest thing it can ever say is ELIGIBLE_FOR_HUMAN_REVIEW; the human decides.

Replaces the old calendar rule ("60+ days live") with a chain of evidence.
Every stage is evaluated for every account (no short-circuiting, so the full
picture is visible) and each is one of:
  PASS          evidence clears the bar
  FAIL          evidence says no
  PENDING       not enough live evidence yet (never a pass, never a fail)
  NOT_EVALUATED only for the portfolio check, which is meaningful only once
                everything before it passes

The chain (spec order):
  1 HISTORICAL          full-history backtest at the LIVE fee tier: profitable
                        after costs, >=30 round trips, profit factor >1
  2 OUT_OF_SAMPLE       held-out 40%: profitable in absolute terms, >=15 trips
  3 WALK_FORWARD        rolling windows: beats hold in >=50% of windows, median
                        excess >0, AND beats regime-timed hold on the windows
                        where the rules did something the filter didn't
  4 FEE_GATE            average trade clears the real round-trip cost wall
  5 EDGE_GATE           held-out excess >0 vs buy-and-hold AND >0 vs the
                        regime-timed-hold benchmark (a cash-in-a-bear-market
                        "win" does not count as edge)
  6 MONTE_CARLO         bootstrap 5th percentile >=0, P(loss) <=10%, and at
                        least 5 of 6 harsher-cost/perturbed-parameter stress
                        scenarios still clear both gates
  6b HOLDOUT_COINS      same strategy file, real fees, same regime filter, on ~20 coins
                        that were never in the selection/validation basket: net >0,
                        PF >1 and beats the regime-timed hold. Independent evidence:
                        a real edge transfers to unseen coins, an overfit one does not.
                        (Daily strategies only; 4h stays PENDING until built.)
  7 LIVE_PAPER_EXECUTION account is paper, has >=10 fills carrying real
                        order-book execution records with <=10% fallback,
                        no unexplained circuit-breaker trip, not flagged
  8 LIVE_PAPER_SAMPLE   >=30 live paper round trips spanning >=2 market
                        regimes. Count and diversity, never elapsed days.
  9 PORTFOLIO_CHECK     not a redundant copy (trailing daily-return
                        correlation >=0.85) of an already-eligible strategy —
                        one bet does not get to graduate ten times

Thresholds are constants below; changing one is a visible, deliberate edit.
"""
import json, os, time
from . import data, engine, metrics, regime, kraken_fees, monte_carlo, walkforward_v2, journal, risk
from . import kraken_portfolio as kp
from .strategy import Strategy
from .portfolio_optimize import DEFAULT_BASKET, _pooled

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(BASE, "logs", "graduation")
STATE_DIR = os.path.join(BASE, "paper_state")

STAGES = ["HISTORICAL", "OUT_OF_SAMPLE", "WALK_FORWARD", "FEE_GATE", "EDGE_GATE",
          "MONTE_CARLO", "HOLDOUT_COINS", "LIVE_PAPER_EXECUTION", "LIVE_PAPER_SAMPLE", "PORTFOLIO_CHECK"]
MIN_HIST_TRIPS, MIN_OOS_TRIPS = 30, 15
WF_MIN_BEATING_PCT = 50.0
MC_MIN_P5, MC_MAX_LOSS, MC_MIN_STRESS_PASS = 0.0, 0.10, 5
MIN_EXEC_FILLS, MAX_FALLBACK_SHARE = 10, 0.10
MIN_PAPER_TRIPS, MIN_REGIMES_COVERED = 30, 2
REDUNDANCY_CORR = 0.85

CHAMPIONS = {
    "realpaper": "smart_swing", "krakenmulti": "multi_signal", "krakenfast": "fast_swing",
    "krakenslow": "slow_swing", "krakenrsi": "rsi_meanrev", "krakenmacd": "macd_cross",
    "krakenbbreak": "bollinger_breakout", "krakenturtle": "turtle_donchian",
    "krakencci": "cci_momentum", "krakenkeltner": "keltner_breakout",
    "krakenichimoku": "ichimoku_cloud",
}


def _stage(status, detail=""):
    return {"status": status, "detail": detail}


def _market(gran, cache):
    if gran in cache:
        return cache[gran]
    max_bars = 6570 if gran == 14400 else 1500
    products = [f"{c}-USD" for c in DEFAULT_BASKET]
    bars = {p: data.get_bars(p, granularity=gran, max_bars=max_bars) for p in products}
    allow = regime.risk_on_timestamps("BTC-USD", granularity=gran, max_bars=max_bars)
    cache[gran] = (bars, allow, max_bars)
    return cache[gran]


def _regimes_of_fills(fills):
    """Market-regime label (BTC, known at the prior day's close) for each fill."""
    from . import kraken_data, regimes
    bars = kraken_data.fetch_recent("BTC-USD", 86400)
    lm = regimes.label_map(bars)
    out = []
    for f in fills:
        day = (int(f["ts"]) // 86400) * 86400 - 86400
        out.append(lm.get(day))
    return [l for l in out if l]


def evaluate_backtest_stages(account, state, cache):
    """Stages 1-6: everything answerable from history at the live fee tier."""
    gran = state["granularity"]
    bars, allow, max_bars = _market(gran, cache)
    own = [p for p in state.get("sleeves", {}) if p in bars]
    if own and len(own) < len(bars):
        bars = {p: bars[p] for p in own}   # factory accounts run a coin sub-universe: judge them on it
    if gran == 86400:
        from .regime_learner import account_allow   # judge the account under its own learned restrictions
        allow = account_allow(state, allow, bars)
    spec = json.load(open(os.path.join(BASE, state["strategy_path"])))
    spec, fees = kraken_fees.apply_to_spec(spec)
    tmp = os.path.join(STATE_DIR, f"_grad_{account}.json")
    json.dump(spec, open(tmp, "w"))
    tmp_rel = os.path.relpath(tmp, BASE)
    res = {}
    try:
        per_coin = 50.0 / len(bars)
        # 1. HISTORICAL — whole history, per-coin runs (round trips need one coin at a time)
        start = end = 0.0
        trips = []
        for p, b in bars.items():
            st = Strategy(spec)
            r = engine.run(st, b, cash=per_coin, allow_buy_ts=allow)
            start += per_coin; end += r.equity[-1]
            trips.extend(net for _, net in metrics.roundtrips(r, st.side_rate))
        wins, losses = sum(t for t in trips if t > 0), -sum(t for t in trips if t <= 0)
        pf = wins / losses if losses > 0 else (999.0 if wins > 0 else 0.0)
        net_ret = end / start - 1
        ok = net_ret > 0 and len(trips) >= MIN_HIST_TRIPS and pf > 1
        res["HISTORICAL"] = _stage("PASS" if ok else "FAIL",
                                   f"net {net_ret*100:+.1f}% after real costs, {len(trips)} trips, PF {pf:.2f}")

        # 2/4/5 from the pooled 60/40 split (same test used everywhere in this project)
        r = _pooled(spec, bars, cash=50.0, split=0.6, allow_buy_ts=allow)
        if r is None:
            for s in ("OUT_OF_SAMPLE", "FEE_GATE", "EDGE_GATE"):
                res[s] = _stage("FAIL", "no trades")
        else:
            res["OUT_OF_SAMPLE"] = _stage(
                "PASS" if r["out_return"] > 0 and r["n_out"] >= MIN_OOS_TRIPS else "FAIL",
                f"held-out return {r['out_return']*100:+.1f}%, {r['n_out']} trips (need >0% and >={MIN_OOS_TRIPS})")
            res["FEE_GATE"] = _stage("PASS" if r["fee_gate_ok"] and r["sample_ok"] else "FAIL",
                                     f"avg net trade {'>' if r['fee_gate_ok'] else '<='} 0 at {fees['taker_bps']}bps taker, "
                                     f"{r['n_in']} in-sample trips (sample_ok={r['sample_ok']})")
            # regime-timed-hold benchmark over the SAME held-out slices
            rh_end = 0.0
            for p, b in bars.items():
                cut = int(len(b) * 0.6)
                rh_end += per_coin * (1 + walkforward_v2._regime_hold_return(b[cut:], allow, Strategy(spec).side_rate))
            rh_ret = rh_end / 50.0 - 1
            ex_hold, ex_rh = r["out_excess"], r["out_return"] - rh_ret
            res["EDGE_GATE"] = _stage("PASS" if ex_hold > 0 and ex_rh > 0 else "FAIL",
                                      f"held-out excess {ex_hold*100:+.1f}% vs hold, {ex_rh*100:+.1f}% vs regime-timed hold")

        # 3. WALK-FORWARD
        pw, wf = walkforward_v2.run(tmp_rel, granularity=gran, train_days=365, test_days=120,
                                    max_bars=max_bars, verbose=False)
        if not wf.get("n_windows"):
            res["WALK_FORWARD"] = _stage("FAIL", "not enough history for a full window")
        else:
            ties = sum(1 for w in pw if abs(w.get("excess_vs_regime_hold_pct", 0)) < 0.05)
            decided = max(1, len(pw) - ties)
            rh_ok = wf["windows_beating_regime_hold"] / decided >= 0.5
            ok = (wf["pct_windows_beating_hold"] >= WF_MIN_BEATING_PCT and wf["median_excess_pct"] > 0 and rh_ok)
            res["WALK_FORWARD"] = _stage(
                "PASS" if ok else "FAIL",
                f"beats hold {wf['windows_beating_hold']}/{wf['n_windows']} windows (median {wf['median_excess_pct']:+.1f}%), "
                f"beats regime-hold {wf['windows_beating_regime_hold']}/{decided} decided windows")

        # 6. MONTE CARLO / STRESS
        mc = monte_carlo.run(tmp_rel, granularity=gran, max_bars=max_bars, n_sims=2000, verbose=False)
        d = mc["distribution"]
        passed = int(str(mc["stress_scenarios_passed"]).split("/")[0])
        if not d.get("n_sims"):
            res["MONTE_CARLO"] = _stage("FAIL", "no trades to resample")
        else:
            ok = d["p5_return_pct"] >= MC_MIN_P5 and d["probability_of_losing_money"] <= MC_MAX_LOSS and passed >= MC_MIN_STRESS_PASS
            res["MONTE_CARLO"] = _stage("PASS" if ok else "FAIL",
                                        f"p5 {d['p5_return_pct']:+.1f}%, P(loss) {d['probability_of_losing_money']*100:.0f}%, "
                                        f"stress {mc['stress_scenarios_passed']}")
            res["_mc_p5"] = d["p5_return_pct"]

        # 6b. HOLDOUT_COINS — same strategy, real fees, on coins never used to select or validate it
        if gran == 86400:
            from . import holdout
            if "holdout" not in cache:
                hb = holdout.load(holdout.holdout_products())
                cache["holdout"] = hb
            hb = cache["holdout"]
            r = holdout.evaluate(json.load(open(os.path.join(BASE, state["strategy_path"]))), hb, allow) if hb else None
            if r is None:
                res["HOLDOUT_COINS"] = _stage("PENDING", "no hold-out data available")
            else:
                v, why = holdout.verdict(r)
                res["HOLDOUT_COINS"] = _stage("PASS" if v == "PASS" else ("PENDING" if v == "INSUFFICIENT" else "FAIL"),
                                              f"{r['coins']} unseen coins: {why}")
        else:
            res["HOLDOUT_COINS"] = _stage("PENDING", "hold-out coin test not built for 4h strategies yet")
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return res


def evaluate_live_stages(account, state):
    fills = state.get("fills", [])
    # 7. LIVE_PAPER_EXECUTION
    problems = []
    if state.get("paper") is not True:
        problems.append("paper flag is not True")
    if state.get("halted"):
        problems.append("circuit breaker currently tripped")
    if state.get("needs_revalidation"):
        problems.append(f"flagged needs_revalidation ({state.get('revalidation_reason')})")
    breaker_trips = len(journal.read(account, event="circuit_breaker"))
    if breaker_trips:
        problems.append(f"{breaker_trips} circuit-breaker trip(s) in the journal need a human explanation")
    ex = [f["exec"] for f in fills if isinstance(f.get("exec"), dict)]
    fallback = sum(1 for e in ex if str(e.get("source", "")).startswith("fallback"))
    out = {}
    if problems:
        out["LIVE_PAPER_EXECUTION"] = _stage("FAIL", "; ".join(problems))
    elif len(ex) < MIN_EXEC_FILLS:
        out["LIVE_PAPER_EXECUTION"] = _stage("PENDING", f"{len(ex)}/{MIN_EXEC_FILLS} fills with real order-book execution records")
    elif fallback / len(ex) > MAX_FALLBACK_SHARE:
        out["LIVE_PAPER_EXECUTION"] = _stage("FAIL", f"{fallback}/{len(ex)} fills fell back to assumed costs (book unavailable)")
    else:
        avg_cost = sum(e.get("total_exec_cost_usd", 0) or 0 for e in ex) / len(ex)
        out["LIVE_PAPER_EXECUTION"] = _stage("PASS", f"{len(ex)} book-priced fills, {fallback} fallback, avg exec cost ${avg_cost:.4f}")
    # 8. LIVE_PAPER_SAMPLE — counts and regime diversity, never elapsed time
    sells = [f for f in fills if f["side"] == "sell"]
    if len(sells) < MIN_PAPER_TRIPS:
        out["LIVE_PAPER_SAMPLE"] = _stage("PENDING", f"{len(sells)}/{MIN_PAPER_TRIPS} live paper round trips")
    else:
        regs = set(_regimes_of_fills(sells))
        if len(regs) < MIN_REGIMES_COVERED:
            out["LIVE_PAPER_SAMPLE"] = _stage("PENDING", f"{len(sells)} trips but only {len(regs)} market regime(s): {sorted(regs)}")
        else:
            out["LIVE_PAPER_SAMPLE"] = _stage("PASS", f"{len(sells)} trips across regimes {sorted(regs)}")
    return out


def _overall(stages):
    fails = [s for s in STAGES if stages.get(s, {}).get("status") == "FAIL"]
    pend = [s for s in STAGES if stages.get(s, {}).get("status") in ("PENDING", "NOT_EVALUATED")]
    if fails:
        return "NOT_ELIGIBLE", fails
    if pend:
        return "ACCUMULATING_EVIDENCE", pend
    return "ELIGIBLE_FOR_HUMAN_REVIEW", []


def _accounts():
    accts = [(a, "champion") for a in CHAMPIONS]
    import glob
    for f in sorted(glob.glob(os.path.join(STATE_DIR, "c4h_*.kraken.json"))):
        accts.append((os.path.basename(f)[:-len(".kraken.json")], "4h challenger"))
    for f in sorted(glob.glob(os.path.join(STATE_DIR, "chal_*.kraken.json"))):
        accts.append((os.path.basename(f)[:-len(".kraken.json")], "learner challenger"))
    return accts


def report(verbose=True, only=None):
    cache, results = {}, {}
    for account, kind in _accounts():
        if only and account not in only:
            continue
        try:
            state = kp.load(account)
        except FileNotFoundError:
            continue
        t0 = time.time()
        stages = evaluate_backtest_stages(account, state, cache)
        stages.update(evaluate_live_stages(account, state))
        results[account] = {"kind": kind, "stages": stages, "granularity": state["granularity"]}
        if verbose:
            print(f"  evaluated {account} ({time.time()-t0:.0f}s)", flush=True)

    # 9. PORTFOLIO_CHECK — only meaningful for accounts that cleared everything else
    from .portfolio_correlation import _dated_daily_equity
    prior_ok = [a for a, r in results.items()
                if all(r["stages"].get(s, {}).get("status") == "PASS" for s in STAGES[:-1])]
    accepted = []
    series = {}
    for a in sorted(prior_ok, key=lambda a: -results[a]["stages"].get("_mc_p5", 0)):
        st = kp.load(a)
        bars, allow, _ = _market(st["granularity"], cache)
        if st["granularity"] == 86400:
            from .regime_learner import account_allow
            allow = account_allow(st, allow, bars)
        series[a] = _dated_daily_equity(st["strategy_path"], bars, 50.0 / len(bars), allow, st["granularity"] == 14400)
        redundant = None
        for b in accepted:
            days = sorted(set(series[a]) & set(series[b]))
            ra = [series[a][days[i]] / series[a][days[i - 1]] - 1 for i in range(1, len(days))]
            rb = [series[b][days[i]] / series[b][days[i - 1]] - 1 for i in range(1, len(days))]
            c = risk.correlation(ra, rb)
            if c is not None and c >= REDUNDANCY_CORR:
                redundant = (b, c)
                break
        if redundant:
            results[a]["stages"]["PORTFOLIO_CHECK"] = _stage("FAIL", f"redundant copy of {redundant[0]} (corr {redundant[1]:.2f})")
        else:
            accepted.append(a)
            results[a]["stages"]["PORTFOLIO_CHECK"] = _stage("PASS", "adds a distinct return stream vs already-eligible strategies")
    for a, r in results.items():
        r["stages"].setdefault("PORTFOLIO_CHECK", _stage("NOT_EVALUATED", "evaluated only once every earlier stage passes"))
        r["stages"].pop("_mc_p5", None)
        r["status"], r["blocking"] = _overall(r["stages"])

    summary = {"generated": time.strftime("%Y-%m-%d %H:%M:%S"),
               "note": "REPORT ONLY. Nothing here enables real trading; a human decides.",
               "not_gradable": {"krakenmoon": "funnel architecture has no backtest harness in this framework"},
               "excluded_by_design": "arena_* and krakendemo* (experimental / known fee-failing)",
               "accounts": results}
    os.makedirs(OUT_DIR, exist_ok=True)
    json.dump(summary, open(os.path.join(OUT_DIR, f"report_{int(time.time())}.json"), "w"), indent=2)
    json.dump(summary, open(os.path.join(OUT_DIR, "latest.json"), "w"), indent=2)
    if verbose:
        print(format_report(summary))
    return summary


GLYPH = {"PASS": "P", "FAIL": "x", "PENDING": ".", "NOT_EVALUATED": "-"}


def format_report(summary):
    lines = []
    hdr = f"{'account':<16}{'kind':<15}" + " ".join(f"{i+1}" for i in range(len(STAGES))) + "   status"
    lines += ["", "GRADUATION REPORT — evidence chain (P=pass  x=fail  .=pending live evidence  -=not evaluated)",
              "stages: " + "  ".join(f"{i+1}={s}" for i, s in enumerate(STAGES)), "", hdr, "-" * len(hdr)]
    for a, r in summary["accounts"].items():
        g = " ".join(GLYPH[r["stages"][s]["status"]] for s in STAGES)
        lines.append(f"{a:<16}{r['kind']:<15}{g}   {r['status']}")
    counts = {}
    for r in summary["accounts"].values():
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    lines += ["", f"Totals: {counts}", "", "First blocking stage per account:"]
    for a, r in summary["accounts"].items():
        if r["blocking"]:
            s = r["blocking"][0]
            lines.append(f"  {a:<16}{s}: {r['stages'][s]['detail']}")
    lines += ["", "krakenmoon: NOT GRADABLE (funnel architecture, no backtest harness).",
              "This report never enables trading. Only a human can decide to go further."]
    return "\n".join(lines)
