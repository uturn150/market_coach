"""Different lever for weekly greens: a WEEKLY GUARD on the pooled fleet (not a new entry signal).
Each week (UTC, Sun close): if week-to-date pool P&L reaches +TARGET, bank it (liquidate, no new entries
until next week); if it falls to -LIMIT, cut it (same). Costs of the extra exits are real (fees).
Pre-registered grid: target {none,1,2,3}% x limit {none,1,2,3}% on the 11 champions' shared pool,
F0 filter, 24 coins.  Judged on the whole history and on the held-out last 40%.  Report only.
    python3 weekly_guard_study.py"""
import json, os
from marketcoach import portfolio_sim as ps, data
from green_study import stats, split, week_key

BASE = os.path.dirname(os.path.abspath(__file__))


class WeeklyGuard:
    def __init__(self, ts, target, limit):
        self.ts, self.target, self.limit = ts, target, limit
        self.week, self.start_eq, self.locked, self.last_eq = None, None, False, None
        self.locks = 0
    def approve(self, request, state):
        return 0.0 if self.locked else 1.0
    def on_bar(self, state):
        i, eq = state["i"], state["equity"]
        w = week_key(self.ts[i])
        if w != self.week:                      # new week: baseline = last close of the previous week
            self.week, self.start_eq, self.locked = w, self.last_eq if self.last_eq else eq, False
        self.last_eq = eq
        wtd = eq / self.start_eq - 1
        liq = False
        if not self.locked and ((self.target is not None and wtd >= self.target) or (self.limit is not None and wtd <= -self.limit)):
            self.locked, liq = True, True
            self.locks += 1
        return {"liquidate": liq}
    def summary(self):
        return {"locks": self.locks}


def main():
    ts_full = [b.ts for b in data.get_bars("BTC-USD", 86400, max_bars=1500)]
    rows = []
    for target in (None, 0.01, 0.02, 0.03):
        for limit in (None, 0.01, 0.02, 0.03):
            base = ps.simulate(verbose=False)
            ts = ts_full[-len(base["_series"]["dates"]):]
            g = WeeklyGuard(ts, target, limit) if (target or limit) else None
            r = ps.simulate(verbose=False, risk_manager=g) if g else base
            ser = {d: e for d, e in zip(r["_series"]["dates"], r["_series"]["equity"])}
            a, o = stats(ser), stats(split(ser)[1])
            rows.append({"target": target, "limit": limit, "locks": g.locks if g else 0, "all": a, "oos": o})
    print(f"{'target':>7s} {'limit':>6s} {'locks':>6s} | ALL: ret dd grn wk% of act worst | OOS: ret dd grn wk% of act worst")
    for r in rows:
        a, o = r["all"], r["oos"]
        f = lambda x: "none" if x is None else f"{x*100:.0f}%"
        print(f"{f(r['target']):>7s} {f(r['limit']):>6s} {r['locks']:>6d} | {a['total_return_pct']:>+6.1f}% {a['max_dd_pct']:>6.1f}% {a['green_week_pct']:>5.1f}% {a['green_of_active_wk_pct']:>5.1f}% {a['worst_week_pct']:>6.1f}% | {o['total_return_pct']:>+6.1f}% {o['max_dd_pct']:>6.1f}% {o['green_week_pct']:>5.1f}% {o['green_of_active_wk_pct']:>5.1f}% {o['worst_week_pct']:>6.1f}%")
    json.dump(rows, open(os.path.join(BASE, "logs", "weekly_guard_study.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
