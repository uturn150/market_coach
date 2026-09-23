"""Spawn F1-filtered 4H paper challengers (c4h_<family>_f1), separate accounts next to the
plain 4H fleet. A family qualifies only if, at the real fee tier and on the same 60/40 split,
F1 improves BOTH in-sample profit factor and out-of-sample return over F0, the F1 version
still clears fee+sample gates, and its Monte Carlo bootstrap is not fragile. Families the
research already rejected as OVERFIT (c4h_rsi, c4h_macd) are skipped. Paper only; never
promotes anything. Judged by graduation_report like every other account."""
import json, os, time
from . import data, fleet4h, kraken_fees, kraken_portfolio as kp, market_filters as mf
from . import learner as L
from .monte_carlo import bootstrap, summarize_distribution
from .portfolio_optimize import DEFAULT_BASKET, _pooled

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAX_F1_4H = 4
MC_P5, MC_LOSS = 0.0, 0.10


def run(act=False, verbose=True):
    bars, F0 = fleet4h.load_market()
    daily = data.get_bars("BTC-USD", 86400, max_bars=1500)
    F1 = F0 & mf.f1_timestamps_4h(daily, bars["BTC-USD"])
    rows = []
    for fam in fleet4h.FAMILIES:
        path = os.path.join(BASE, "strategies", f"c4h_{fam}.json")
        if not os.path.exists(path):
            continue
        try:
            parent = kp.load(f"c4h_{fam}")
        except FileNotFoundError:
            continue
        if parent.get("needs_revalidation"):
            rows.append((fam, None, "parent rejected (needs_revalidation)"))
            continue
        spec, _ = kraken_fees.apply_to_spec(json.load(open(path)))
        r0, r1 = _pooled(spec, bars, 50.0, 0.6, F0), _pooled(spec, bars, 50.0, 0.6, F1)
        if not r0 or not r1:
            rows.append((fam, None, "no trades")); continue
        imp = r1["pf_in"] > r0["pf_in"] and r1["out_return"] > r0["out_return"]
        gates_ok = r1["fee_gate_ok"] and r1["sample_ok"]
        rows.append((fam, {"gain": r1["pf_in"] / r0["pf_in"] if r0["pf_in"] else 0, "imp": imp, "gates": gates_ok,
                           "pf0": r0["pf_in"], "pf1": r1["pf_in"], "o0": r0["out_return"], "o1": r1["out_return"],
                           "spec": spec}, ""))
    st = L._load_state()
    have = sum(1 for c in st["challengers"].values() if "market_filter" in (c.get("stamp") or {}) and c["status"] != "RETIRED"
               and c.get("granularity") == 14400)
    events = []
    ranked = sorted((r for r in rows if r[1]), key=lambda r: -r[1]["gain"])   # in-sample only
    for fam, d, _ in ranked:
        name = f"c4h_{fam}_f1"
        if not d["imp"] or not d["gates"] or os.path.exists(os.path.join(BASE, "paper_state", f"{name}.kraken.json")):
            continue
        if have >= MAX_F1_4H:
            events.append("4H F1 cap reached; further families wait"); break
        tmp = L.collect_trades_for(d["spec"], bars, F1)
        dist = summarize_distribution(bootstrap(tmp, n_sims=1500, starting_cash=50.0), 50.0, 0.0)
        if dist.get("n_sims") and (dist["p5_return_pct"] < MC_P5 or dist["probability_of_losing_money"] > MC_LOSS):
            L._log(L.REJECT_LOG, champion=f"c4h_{fam}", candidate="4h F1", stage="monte_carlo",
                   why="fragile bootstrap distribution", p5=dist["p5_return_pct"], p_loss=dist["probability_of_losing_money"])
            events.append(f"{fam}: F1 improves but failed Monte Carlo"); continue
        if act:
            rel = f"strategies/challengers/{name}.json"
            os.makedirs(os.path.join(BASE, "strategies", "challengers"), exist_ok=True)
            s = json.loads(json.dumps(d["spec"]))
            s["name"] = f"CHALLENGER 4H {fam} + market filter F1 — earns status only via the gates"
            json.dump(s, open(os.path.join(BASE, rel), "w"), indent=2)
            kp.open_account(name, rel, [f"{c}-USD" for c in DEFAULT_BASKET], granularity=14400, cash=50.0,
                            paper=True, market_filter="F1")
            L._add_cron(name)
            L._log(L.LOG, action="spawn_4h_f1", challenger=name, pf0=round(d["pf0"], 2), pf1=round(d["pf1"], 2),
                   oos0=round(d["o0"] * 100, 1), oos1=round(d["o1"] * 100, 1))
            events.append(f"spawned {name} (in-sample PF {d['pf0']:.2f}->{d['pf1']:.2f}, OOS {d['o0']*100:+.1f}%->{d['o1']*100:+.1f}%)")
            have += 1
        else:
            events.append(f"[dry run] would spawn {name} (PF {d['pf0']:.2f}->{d['pf1']:.2f}, OOS {d['o0']*100:+.1f}%->{d['o1']*100:+.1f}%)")
    if verbose:
        for fam, d, why in rows:
            print(f"  {fam:9s}", why or f"improves={d['imp']} gates={d['gates']} gain x{d['gain']:.2f}")
        print("\n".join(events or ["no action"]))
    return events
