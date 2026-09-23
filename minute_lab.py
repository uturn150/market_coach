"""Minute-level test of the community bots: same bots, same real fees, but limit orders now fill minute-by-minute
(time_machine) instead of one daily candle.  Also 'penny' variants that only make sense at minute resolution.
Two coin sets from the 27 with 5+ years of minutes: SET_A = the 9 the daily configs were tuned on, SET_B = the other 18 (never tuned on).
    python3 minute_lab.py [window_start_yyyy-mm]   -> logs/minute_lab.json"""
import calendar, json, os, sys, time
from marketcoach import market_sim as ms, minute_data as md, time_machine as tm, bot_engine as be, bot_live as bl, data
BASE = os.path.dirname(os.path.abspath(__file__)); DAY = 86400
T = lambda y, m, d: calendar.timegm((y, m, d, 0, 0, 0))
U = md.universe(); SET_A, SET_B = U[:9], U[9:]
CFG = {
  "dca d5 SO5 F1":      ("dca", dict(safety=5, dev=0.05, mult=2.0, tp=0.03, stop=0.2, filter="F1")),
  "dca d3 SO5 F1":      ("dca", dict(safety=5, dev=0.03, mult=2.0, tp=0.03, stop=0.2, filter="F1")),
  "grid L8 s6 F1":      ("grid", dict(levels=8, step=0.06, filter="F1")),
  "quick27 (tp2 limit)": ("dca", dict(safety=6, dev=0.05, mult=1.8, tp=0.02, stop=0.25, filter="none", base_limit=0.002)),
  "quick27 F1 (tp3)":   ("dca", dict(safety=6, dev=0.03, mult=1.8, tp=0.03, stop=0.25, filter="F1")),
  "grid L8 s3 F1":      ("grid", dict(levels=8, step=0.03, filter="F1")),
  "grid L6 s2 F1":      ("grid", dict(levels=6, step=0.02, filter="F1")),
  "dca d2 SO4 tp2 F1":  ("dca", dict(safety=4, dev=0.02, mult=1.6, tp=0.02, stop=0.2, filter="F1")),
}

def stats(eq):
    peak, dd = eq[0], 0.0
    for v in eq: peak = max(peak, v); dd = min(dd, v / peak - 1)
    return (eq[-1] / eq[0] - 1) * 100, dd * 100

def daily_bars(coins, first, last):
    out = {}
    for c in coins:
        b = data.get_bars(c, 86400, max_bars=1800)
        out[c] = [x for x in b if first <= x.ts <= last]
    n = min(len(v) for v in out.values()); return {c: v[-n:] for c, v in out.items()}

def main():
    y, m = (int(x) for x in (sys.argv[1] if len(sys.argv) > 1 else "2021-10").split("-"))
    first, last = T(y, m, 1), T(2026, 9, 20)
    base = ms.load_long(); f0, f1 = ms.filters_for(base)
    days = list(range(first, last + DAY, DAY))
    ok_tm = {"none": None, "F0": (lambda i: days[i] in f0), "F1": (lambda i: days[i] in f1)}
    res = {}
    for label, coins in (("SET_A tuned-9", SET_A), ("SET_B unseen-18", SET_B)):
        store = md.MinuteDays(coins) if hasattr(md, "MinuteDays") else tm.MinuteDays(coins)
        bars = daily_bars(coins, first, last); ts = [b.ts for b in next(iter(bars.values()))]
        ok_d = {"none": None, "F0": (lambda i, t=ts: t[i] in f0), "F1": (lambda i, t=ts: t[i] in f1)}
        print(f"\n== {label}: {len(coins)} coins, {(last-first)/DAY/365:.1f} years  (daily engine vs minute engine, real fees) ==", flush=True)
        for name, (kind, p) in CFG.items():
            t0 = time.time()
            rd = be.run(bl.make_bot(kind, dict(p), ok_d), bars, cash=100.0)
            a = stats(rd.equity)
            eq, _, ctx = tm.run(bl.make_bot(kind, dict(p), ok_tm), coins, first, last, cash=100.0, store=store)
            b = stats(eq); trips = len(ctx.trips); yrs = (last - first) / DAY / 365
            res[f"{label}|{name}"] = {"daily": a, "minute": b, "trips_daily": len(rd.trips), "trips_minute": trips}
            json.dump(res, open(os.path.join(BASE, "logs", "minute_lab.json"), "w"), indent=1)
            print(f"{name:22s} daily {a[0]:+7.1f}% dd {a[1]:6.1f}% ({len(rd.trips):5d} trips) | MINUTE {b[0]:+7.1f}% dd {b[1]:6.1f}% ({trips:5d} trips, {trips/yrs/365:.2f}/day) [{time.time()-t0:.0f}s]", flush=True)
    json.dump(res, open(os.path.join(BASE, "logs", "minute_lab.json"), "w"), indent=1)

main()
