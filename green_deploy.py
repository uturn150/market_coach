"""Deploy the green-search finalists as paper bots: one per (dip family) x {large12, mid12} universe,
each behind F1. Prefix chal_gr_*. Live paper results (and the live loop's auto-pause) decide.
The 4 specs are the top in-sample pick of each family from green_search.py (logs/green_search.json)."""
import json, os, sys
from marketcoach import kraken_portfolio as kp, learner as L
from marketcoach.factory import UNIVERSES

BASE = os.path.dirname(os.path.abspath(__file__))
PICK = {"stoch_dip": dict(lo=15, trend=100, tp=0.03, sl=0.1), "bb_dip": dict(k=1.5, trend=100, tp=0.03, sl=0.1),
        "cci_dip": dict(lo=-150, trend=100, tp=0.05, sl=0.1), "willr_dip": dict(lo=-90, trend=100, tp=0.05, sl=0.1)}


def main(act):
    R = json.load(open(os.path.join(BASE, "logs", "green_search.json")))
    os.makedirs(os.path.join(BASE, "strategies", "factory"), exist_ok=True)
    for fam, kw in PICK.items():
        spec = next(r["spec"] for r in R if r["family"] == fam and all(r["params"].get(k) == v for k, v in kw.items()))
        for uni, coins in UNIVERSES.items():
            name = f"chal_gr_{fam}_{uni}"
            if os.path.exists(os.path.join(BASE, "paper_state", f"{name}.kraken.json")):
                continue
            print(("deploy " if act else "[dry] ") + name)
            if not act:
                continue
            rel = f"strategies/factory/{name}.json"
            s = json.loads(json.dumps(spec)); s["name"] = f"GREEN CANDIDATE {fam} {uni} + F1 (small take-profit, dip-buy in uptrend)"
            json.dump(s, open(os.path.join(BASE, rel), "w"), indent=2)
            kp.open_account(name, rel, [f"{c}-USD" for c in coins], granularity=86400, cash=50.0, paper=True, market_filter="F1")
            L._add_cron(name)


if __name__ == "__main__":
    main("--act" in sys.argv)
