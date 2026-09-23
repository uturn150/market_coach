"""Prototype study for the trend-strength (ADX) family. The grid is fixed in
strategies/trend_strength.tpl.json BEFORE running. Every candidate goes through the same
real-fee two-gate test; the winner is chosen on IN-SAMPLE profit factor only, then run
through the full graduation backtest stages (hist, OOS, walk-forward, fee/edge, Monte Carlo,
unseen coins). Nothing is deployed.   python3 trend_strength_study.py"""
import json, os
from marketcoach import data, regime, kraken_fees, graduation
from marketcoach.optimize import candidates
from marketcoach.portfolio_optimize import DEFAULT_BASKET, _pooled

BASE = os.path.dirname(os.path.abspath(__file__))


def main():
    tpl = json.load(open(os.path.join(BASE, "strategies", "trend_strength.tpl.json")))
    coin_bars = {f"{c}-USD": data.get_bars(f"{c}-USD", 86400, max_bars=1500) for c in DEFAULT_BASKET}
    allow = regime.risk_on_timestamps("BTC-USD", 86400, max_bars=1500)
    rows = []
    for values, spec in candidates(tpl):
        real, _ = kraken_fees.apply_to_spec(spec)
        r = _pooled(real, coin_bars, cash=50.0, split=0.6, allow_buy_ts=allow)
        if r is None:
            rows.append((values, None)); continue
        rows.append((values, r))
    ok = [(v, r) for v, r in rows if r]
    print(f"{len(rows)} candidates, {len(ok)} traded")
    fee_ok = [(v, r) for v, r in ok if r["fee_gate_ok"] and r["sample_ok"]]
    oos_pos = [(v, r) for v, r in ok if r["out_return"] > 0]
    beat = [(v, r) for v, r in fee_ok if r["out_excess"] > 0]
    print(f"pass fee+sample: {len(fee_ok)} | OOS return > 0: {len(oos_pos)} | pass fee+sample AND beat hold OOS: {len(beat)}")
    print("median OOS return across ALL candidates: %+.1f%%" % (100 * sorted(r["out_return"] for _, r in ok)[len(ok) // 2]))
    for v, r in sorted(ok, key=lambda t: -t[1]["pf_in"])[:8]:
        print(f"  {v}  PF_in {r['pf_in']:.2f} n_in {r['n_in']} n_out {r['n_out']}  OOS ret {r['out_return']*100:+.1f}%  excess {r['out_excess']*100:+.1f}%")
    if not fee_ok:
        print("no candidate passes fee gate + sample; stop."); return
    best_v, best_r = max(fee_ok, key=lambda t: t[1]["pf_in"])       # in-sample only
    spec = next(s for v, s in candidates(tpl) if v == best_v)
    spec["name"] = f"Trend-strength PROTOTYPE {best_v}"
    rel = "strategies/proto_trend_strength.json"
    json.dump(spec, open(os.path.join(BASE, rel), "w"), indent=2)
    print("\nselected (in-sample PF):", best_v)
    cache = {}
    res = graduation.evaluate_backtest_stages("proto_trend_strength",
                                              {"granularity": 86400, "strategy_path": rel}, cache)
    for k, v in res.items():
        if isinstance(v, dict):
            print(f"  {k:14s} {v['status']:5s} {v['detail']}")


if __name__ == "__main__":
    main()
