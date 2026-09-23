"""How much of the minute-level gain survives stricter limit-order fill assumptions?  A resting order fills only when price trades
THROUGH it by `through` (0.01% = optimistic, 0.05% = daily-engine default, 0.10% / 0.20% = strict, queue-priority-like).
Unseen 18 coins, 5 years, real fees, divisible orders.   python3 minute_fill_sens.py -> logs/minute_fill_sens.json"""
import calendar, json, os, time
from marketcoach import market_sim as ms, minute_data as md, time_machine as tm, bot_live as bl
BASE = os.path.dirname(os.path.abspath(__file__)); DAY = 86400
T = lambda y, m, d: calendar.timegm((y, m, d, 0, 0, 0))
SET_B = md.universe()[9:]
CFG = {
  "dca d5 SO5 F1":      ("dca", dict(safety=5, dev=0.05, mult=2.0, tp=0.03, stop=0.2, filter="F1")),
  "dca d3 SO5 F1":      ("dca", dict(safety=5, dev=0.03, mult=2.0, tp=0.03, stop=0.2, filter="F1")),
  "grid L8 s6 F1":      ("grid", dict(levels=8, step=0.06, filter="F1")),
  "quick27 F1 (tp3)":   ("dca", dict(safety=6, dev=0.03, mult=1.8, tp=0.03, stop=0.25, filter="F1")),
  "quick27 (tp2 limit)": ("dca", dict(safety=6, dev=0.05, mult=1.8, tp=0.02, stop=0.25, filter="none", base_limit=0.002)),
}
THROUGH = (0.0001, 0.0005, 0.001, 0.002)

def stats(eq):
    peak, dd = eq[0], 0.0
    for v in eq: peak = max(peak, v); dd = min(dd, v / peak - 1)
    return (eq[-1] / eq[0] - 1) * 100, dd * 100

first, last = T(2021, 10, 1), T(2026, 9, 20)
base = ms.load_long(); f0, f1 = ms.filters_for(base)
days = list(range(first, last + DAY, DAY))
ok = {"none": None, "F0": (lambda i: days[i] in f0), "F1": (lambda i: days[i] in f1)}
store = tm.MinuteDays(SET_B); res = {}
print(f"unseen 18 coins, 5y: minute engine at different fill strictness (through = how far price must trade beyond the limit)", flush=True)
for name, (kind, p) in CFG.items():
    row = []
    for th in THROUGH:
        t0 = time.time()
        eq, _, ctx = tm.run(bl.make_bot(kind, dict(p), ok), SET_B, first, last, cash=100.0, through=th, store=store)
        r, dd = stats(eq); row.append({"through": th, "ret": r, "dd": dd, "trips": len(ctx.trips)})
        print(f"{name:22s} through {th*100:.2f}%: {r:+7.1f}% dd {dd:6.1f}% ({len(ctx.trips):5d} trips) [{time.time()-t0:.0f}s]", flush=True)
        res[name] = row; json.dump(res, open(os.path.join(BASE, "logs", "minute_fill_sens.json"), "w"), indent=1)
