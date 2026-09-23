"""Hummingbot 'bollingrid' port: BB-dip grid on real minute data. Same protocol as minute_lab.py: SET_A = 9 coins
the community bots were tuned on, SET_B = 18 coins never used for anything in this project. Real fees, 5 years.
    python3 bollingrid_lab.py -> logs/bollingrid_lab.json"""
import calendar, json, os, time
from marketcoach import market_sim as ms, minute_data as md, time_machine as tm, community_bots as cb
BASE = os.path.dirname(os.path.abspath(__file__)); DAY = 86400
T = lambda y, m, d: calendar.timegm((y, m, d, 0, 0, 0))
SET_A, SET_B = md.universe()[:9], md.universe()[9:]
CFG = {
  "bollingrid default (bb100/2std, 5 levels)": dict(bb_len=100, bb_std=2.0, levels=5, start_coef=0.25, end_coef=0.75, limit_coef=0.35),
  "bollingrid wide (bb50/1.5std, 8 levels)":    dict(bb_len=50, bb_std=1.5, levels=8, start_coef=0.35, end_coef=0.9, limit_coef=0.5),
  "bollingrid tight (bb100/2std, 3 levels)":    dict(bb_len=100, bb_std=2.0, levels=3, start_coef=0.15, end_coef=0.5, limit_coef=0.25),
}


def stats(eq):
    peak, dd = eq[0], 0.0
    for v in eq: peak = max(peak, v); dd = min(dd, v / peak - 1)
    return (eq[-1] / eq[0] - 1) * 100, dd * 100


def main():
    first, last = T(2021, 10, 1), T(2026, 9, 20)
    base = ms.load_long(); f0, f1 = ms.filters_for(base)
    days = list(range(first, last + DAY, DAY))
    ok_f1 = lambda i: days[i] in f1
    res = {}
    for label, coins in (("SET_A tuned-9", SET_A), ("SET_B unseen-18", SET_B)):
        store = tm.MinuteDays(coins)
        print(f"\n== {label}: {len(coins)} coins, {(last-first)/DAY/365:.1f}y, real fees, F1 filter ==", flush=True)
        for name, kw in CFG.items():
            t0 = time.time()
            bot = cb.BollinGridBot(market_ok=ok_f1, **kw)
            eq, _, ctx = tm.run(bot, coins, first, last, cash=100.0, store=store)
            r, dd = stats(eq)
            res[f"{label}|{name}"] = {"ret": r, "dd": dd, "trips": len(ctx.trips)}
            print(f"{name:40s} {r:+7.1f}% dd {dd:6.1f}% ({len(ctx.trips):5d} trips) [{time.time()-t0:.0f}s]", flush=True)
            json.dump(res, open(os.path.join(BASE, "logs", "bollingrid_lab.json"), "w"), indent=1)

main()
