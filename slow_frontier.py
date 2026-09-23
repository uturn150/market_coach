"""Return-vs-drawdown frontier for the slow $50 trader: every genome the trainer has seen, scored on FUTURE years (2024-26) and on UNSEEN coins.
Answers 'how much more return can I get, and what drawdown does it cost?'   python3 slow_frontier.py -> logs/slow_frontier.json"""
import json, os, random, time, calendar
from marketcoach import market_sim as ms, slow_learner as sl, slow_updater as su
BASE = os.path.dirname(os.path.abspath(__file__))
T = lambda y, m, d: calendar.timegm((y, m, d, 0, 0, 0))
bars = ms.load_long(); f0, f1 = ms.filters_for(bars)
W = sl.World(bars, {"F0": f0, "F1": f1})
e_future = W.episodes(T(2024, 1, 1), T(2026, 9, 1)); e_all = W.episodes(T(2020, 3, 1), T(2026, 9, 1))
mem = sl.load(); cur = json.load(open(os.path.join(BASE, "paper_state", "slow50_versions.json")))["current"]["genome"]
seen = [v["genome"] for v in mem["seen"].values() if not v["genome"].get("maker") and v["genome"].get("size_mode", "full") in su.SUPPORTED_SIZE]
top = sorted(mem["seen"].values(), key=lambda r: -r["train_ret"])
cands = [cur] + [r["genome"] for r in top[:60] if not r["genome"].get("maker")]
uniq = {}; [uniq.setdefault(sl.key(g), g) for g in cands]
rows = []
print(f"{len(uniq)} candidate genomes", flush=True)
for k, g in uniq.items():
    t0 = time.time(); v = su.validate(W, g, e_future, e_all)
    rows.append({"key": k[:90], "future_med": v["future_med"], "unseen_med": v["unseen_med"], "future_dd": v["future_dd"], "unseen_worst_dd": v["unseen_worst_dd"], "is_current": g is cands[0], "coins": g.get("coins")})
    print(f"future {v['future_med']:+6.1f}% dd {v['future_dd']:5.1f}% | unseen {v['unseen_med']:+6.1f}% worst dd {v['unseen_worst_dd']:5.1f}% {'<== CURRENT' if g is cands[0] else ''} [{time.time()-t0:.0f}s]", flush=True)
    json.dump(rows, open(os.path.join(BASE, "logs", "slow_frontier.json"), "w"), indent=1)
