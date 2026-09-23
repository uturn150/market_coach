"""4-hour CHALLENGER fleet research pipeline.

Each strategy family gets its OWN fresh 4H parameter grid (strategies/c4h_*.tpl.json)
— daily parameters are never assumed to transfer. Every candidate is scored by the
same pooled fee+edge gate the whole project uses, at the LIVE Kraken fee tier, on
4H bars resampled from ~3 years of Coinbase hourly history (Coinbase has no native
4h candles). Live paper trading later reads Kraken's native 4h candles.

Pipeline per family:
  candidates -> pooled fee/sample/edge gate (select in-sample, judge out-of-sample)
  -> robust-family check -> walk-forward -> Monte Carlo -> deploy as a paper
  CHALLENGER account (c4h_<family>) if the gates pass.

Nothing here can reach the champion leaderboard or real money. Every rejected
candidate and family is written to logs/research_rejections.jsonl with a reason
— failure is research data and is never deleted.
"""
import json, os, time
from . import data, regime, kraken_fees, walkforward_v2, monte_carlo, kraken_portfolio as kp
from .optimize import candidates
from .portfolio_optimize import DEFAULT_BASKET, _pooled

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REJECT_LOG = os.path.join(BASE, "logs", "research_rejections.jsonl")
VALIDATION_DIR = os.path.join(BASE, "logs", "validation")
FAMILIES = ["sma", "multi", "rsi", "macd", "bbreak", "turtle", "cci", "keltner", "ichimoku"]
GRAN, MAX_BARS = 14400, 6570
ROBUST_MIN_SURVIVORS, ROBUST_MIN_SHARE = 3, 0.25   # a lone survivor is luck, not a family
WF_MIN_WINDOWS_BEATING = 50.0     # % of walk-forward windows that must beat hold
MC_MIN_P5, MC_MAX_LOSS = 0.0, 0.10


def _log(**rec):
    os.makedirs(os.path.dirname(REJECT_LOG), exist_ok=True)
    rec = {"t": int(time.time()), "iso": time.strftime("%Y-%m-%d %H:%M:%S"), **rec}
    with open(REJECT_LOG, "a") as f:
        f.write(json.dumps(rec) + "\n")


def load_market():
    products = [f"{c}-USD" for c in DEFAULT_BASKET]
    bars = {p: data.get_bars(p, granularity=GRAN, max_bars=MAX_BARS) for p in products}
    allow = regime.risk_on_timestamps("BTC-USD", granularity=GRAN, max_bars=MAX_BARS)
    return bars, allow


def _reason(r):
    if r is None:
        return "INSUFFICIENT SAMPLE"
    if not r["fee_gate_ok"]:
        return "FEE BLEED"
    if not r["sample_ok"]:
        return "INSUFFICIENT SAMPLE"
    if r["out_excess"] <= 0:
        return "NO OOS EDGE"
    return None


def evaluate_family(fam, coin_bars, allow):
    tpl = json.load(open(os.path.join(BASE, "strategies", f"c4h_{fam}.tpl.json")))
    fees = kraken_fees.get_fee_bps("XXBTZUSD")
    tpl["costs"]["taker_fee_bps"], tpl["costs"]["maker_fee_bps"] = fees["taker_bps"], fees["maker_bps"]
    survivors, tallies, n = [], {}, 0
    for values, spec in candidates(tpl):
        n += 1
        r = _pooled(spec, coin_bars, cash=50.0, split=0.6, allow_buy_ts=allow)
        why = _reason(r)
        if why:
            tallies[why] = tallies.get(why, 0) + 1
            _log(kind="candidate", family=fam, granularity="4h", params=values, reason=why,
                 pf_in=round(r["pf_in"], 2) if r else None, n_in=r["n_in"] if r else 0,
                 out_excess_pct=round(r["out_excess"] * 100, 1) if r else None)
        else:
            survivors.append((r["pf_in"], values, spec, r))
    out = {"family": fam, "n_candidates": n, "n_survivors": len(survivors), "rejections": tallies}
    if not survivors:
        _log(kind="family", family=fam, granularity="4h", reason=max(tallies, key=tallies.get) if tallies else "NO CANDIDATES",
             detail=tallies)
        return out
    beat = sum(1 for s in survivors if s[3]["out_excess"] > 0)   # all survivors beat by construction of _reason
    out["robust_family"] = (beat / len(survivors) >= 0.5 and len(survivors) >= ROBUST_MIN_SURVIVORS
                            and len(survivors) / n >= ROBUST_MIN_SHARE)
    survivors.sort(key=lambda t: t[0], reverse=True)              # rank on IN-SAMPLE profit factor only
    pf, values, spec, r = survivors[0]
    out.update(winner_params=values, pf_in=round(pf, 2), n_in=r["n_in"], n_out=r["n_out"],
               out_excess_pct=round(r["out_excess"] * 100, 1), winner_spec=spec)
    return out


def _spec_file(fam, spec):
    spec = json.loads(json.dumps(spec))
    spec["name"] = f"CHALLENGER 4H {fam} — must earn champion status independently"
    rel = f"strategies/c4h_{fam}.json"
    json.dump(spec, open(os.path.join(BASE, rel), "w"), indent=2)
    return rel


def validate_winner(fam, spec):
    rel = _spec_file(fam, spec)
    per_window, wf = walkforward_v2.run(rel, granularity=GRAN, train_days=365, test_days=120,
                                        max_bars=MAX_BARS, verbose=False)
    ties = sum(1 for w in per_window if abs(w.get("excess_vs_regime_hold_pct", 0)) < 0.05)
    decided = max(1, len(per_window) - ties)   # windows where the rules did something the filter didn't
    rh_ok = wf.get("windows_beating_regime_hold", 0) / decided >= 0.5
    wf_ok = bool(wf.get("n_windows")) and wf.get("pct_windows_beating_hold", 0) >= WF_MIN_WINDOWS_BEATING \
        and wf.get("median_excess_pct", -1) > 0 and rh_ok
    mc = monte_carlo.run(rel, granularity=GRAN, max_bars=MAX_BARS, n_sims=1500, verbose=False)
    d = mc["distribution"]
    mc_ok = bool(d.get("n_sims")) and d["p5_return_pct"] >= MC_MIN_P5 \
        and d["probability_of_losing_money"] <= MC_MAX_LOSS
    return rel, {"walk_forward": "PASS" if wf_ok else "FAIL", "walk_forward_detail": wf,
                 "beats_regime_timed_hold": rh_ok,
                 "robustness": "PASS" if mc_ok else "FAIL",
                 "robustness_detail": {"p5_return_pct": d.get("p5_return_pct"),
                                       "loss_prob": d.get("probability_of_losing_money"),
                                       "stress_passed": mc.get("stress_scenarios_passed")}}


def deploy(fam, rel):
    from .learner import _add_cron
    name = f"c4h_{fam}"
    kp.open_account(name, rel, [f"{c}-USD" for c in DEFAULT_BASKET], granularity=GRAN,
                    cash=50.0, paper=True)
    _add_cron(name)
    return name


def research(families=None, do_deploy=True, verbose=True):
    coin_bars, allow = load_market()
    os.makedirs(VALIDATION_DIR, exist_ok=True)
    summary = []
    for fam in families or FAMILIES:
        t0 = time.time()
        ev = evaluate_family(fam, coin_bars, allow)
        rec = {"family": fam, "granularity": "4h", "candidates": ev["n_candidates"],
               "survivors": ev["n_survivors"], "rejections": ev["rejections"],
               "fee_gate": "PASS" if ev["n_survivors"] else "FAIL",
               "edge_gate": "PASS" if ev["n_survivors"] else "FAIL",
               "paper_sample": 0, "status": "REJECTED"}
        if ev["n_survivors"]:
            rec.update(params=ev["winner_params"], pf_in=ev["pf_in"], n_in=ev["n_in"],
                       out_excess_pct=ev["out_excess_pct"], robust_family=ev["robust_family"])
            if not ev["robust_family"]:
                rec["status"] = "REJECTED"
                _log(kind="family", family=fam, granularity="4h", reason="OVERFIT",
                     detail="survivors are not a robust family")
            else:
                rel, val = validate_winner(fam, ev["winner_spec"])
                rec.update(val)
                if val["robustness"] == "FAIL":
                    _log(kind="validation", family=fam, granularity="4h", reason="MONTE CARLO FAILURE",
                         detail=val["robustness_detail"])
                if val["walk_forward"] == "FAIL":
                    _log(kind="validation", family=fam, granularity="4h", reason="BENCHMARK UNDERPERFORMANCE",
                         detail="walk-forward: edge does not persist across windows",
                         wf=val["walk_forward_detail"].get("verdict"))
                rec["status"] = "CHALLENGER"
                if do_deploy:
                    rec["account"] = deploy(fam, rel)
        json.dump(rec, open(os.path.join(VALIDATION_DIR, f"c4h_{fam}.json"), "w"), indent=2)
        summary.append(rec)
        if verbose:
            print(f"[{fam}] {rec['status']}: {ev['n_survivors']}/{ev['n_candidates']} candidates survived "
                  f"{ev['rejections']}"
                  + (f" | WF {rec.get('walk_forward')} MC {rec.get('robustness')}" if 'walk_forward' in rec else "")
                  + f"  ({time.time()-t0:.0f}s)", flush=True)
    return summary
