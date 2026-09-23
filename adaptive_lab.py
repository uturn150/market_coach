"""Switch SETTINGS and RISK by regime (not bots).
A) AdaptiveDCA: the DCA bot picks its ladder spacing / take-profit per regime from a table re-learned every 180 days
   from PAST data only (per-regime Sharpe of each of 6 pre-registered settings; sit out a regime if all are negative).
B) Regime risk overlay: the equal-weight fleet is scaled to 100% / 50% / 0% (cash) per regime from past per-regime Sharpe.
Both on 2020-26 real history, 9 coins, real fees.   python3 adaptive_lab.py"""
import json, math, os
from marketcoach import market_sim as ms, bot_engine as be, community_bots as cb, regimes
from meta_lab import fleet_series, COST, WARM
from bot_lab import make_ok
from green_study import stats, week_key

BASE = os.path.dirname(os.path.abspath(__file__))
SETS = [(5, 0.03, 2.0, 0.02), (5, 0.03, 2.0, 0.03), (5, 0.05, 2.0, 0.03), (5, 0.05, 2.0, 0.05), (5, 0.07, 2.0, 0.03), (5, 0.07, 2.0, 0.05)]
MIN_DAYS, RETRAIN = 40, 180


def sharpe(v):
    if len(v) < MIN_DAYS: return None
    m = sum(v) / len(v); sd = math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1))
    return m / sd if sd > 1e-12 else 0.0


def main():
    bars = ms.load_long(); ok = make_ok(bars)
    ts = [b.ts for b in next(iter(bars.values()))]
    lm = regimes.label_map(bars["BTC-USD"]); labels = [lm.get(t) for t in ts]
    prev = lambda i: labels[i - 1] if i >= 1 else None
    # fixed-setting runs (F1) -> daily returns
    fixed = {}
    for s in SETS:
        r = be.run(cb.DCACycleBot(safety=s[0], dev=s[1], mult=s[2], tp=s[3], stop=0.2, market_ok=ok["F1"]), bars, cash=100.0)
        fixed[s] = r.equity
    fr = {s: [0.0] + [e[i] / e[i - 1] - 1 for i in range(1, len(e))] for s, e in fixed.items()}
    n = len(ts)
    # learned tables, retrained every RETRAIN days from day 500 on
    tables = {}
    for t0 in range(500, n, RETRAIN):
        tbl = {}
        for lab in ("TRENDING_UP", "TRENDING_DOWN", "SIDEWAYS", "HIGH_VOLATILITY"):
            sc = []
            for s in SETS:
                v = [fr[s][d] for d in range(2, t0) if labels[d - 1] == lab]
                x = sharpe(v)
                if x is not None: sc.append((x, s))
            if sc:
                best = max(sc)
                tbl[lab] = best[1] if best[0] > 0 else None
        tables[t0] = tbl
    starts = sorted(tables)
    def table_at(i):
        k = max([s for s in starts if s <= i], default=None)
        return tables[k] if k is not None else {}
    bot = cb.AdaptiveDCA(table_at, prev, default=(5, 0.05, 2.0, 0.03), stop=0.2, market_ok=ok["F1"])
    r = be.run(bot, bars, cash=100.0)
    a0 = starts[0]
    def rep(name, eq, from_i):
        ser = {t: e for t, e in zip(ts[from_i:], eq[from_i:])}
        s = stats(ser); y = ms.yearly(ser)
        print(f"  {name:38s} {s['total_return_pct']:>+7.1f}% maxDD {s['max_dd_pct']:>6.1f}% worst wk {s['worst_week_pct']:>6.1f}% green wk {s['green_week_pct']:>5.1f}%  worst yr {y['worst_pct']:>+6.1f}%  " + " ".join(f"{k}:{v:+.0f}%" for k, v in sorted(y['calendar'].items())))
        return s
    print(f"A) Adaptive DCA (scored from bar {a0} = {ms.time.strftime('%Y-%m-%d', ms.time.gmtime(ts[a0])) if hasattr(ms,'time') else a0}, tables re-learned every {RETRAIN}d)")
    out = {"adaptive": rep("AdaptiveDCA (learned per regime)", r.equity, a0)}
    for s in SETS:
        out[str(s)] = rep(f"fixed dev{s[1]*100:g}% tp{s[3]*100:g}%", fixed[s], a0)
    print("   last learned table:", {k: v for k, v in tables[starts[-1]].items()})
    # B) risk overlay on the equal-weight fleet
    ts2, R, fam, up = fleet_series()
    eqw = [sum(R[k][i] for k in R) / len(R) for i in range(len(ts2))]
    def overlay(mode):
        eq, cur = [1.0], 0.0
        table = {}
        for i in range(1, len(ts2)):
            if i >= WARM and (i % 7 == 0 or not table):
                table = {}
                for lab in ("TRENDING_UP", "TRENDING_DOWN", "SIDEWAYS", "HIGH_VOLATILITY"):
                    v = [eqw[d] for d in range(2, i) if labels[d - 1] == lab]
                    x = sharpe(v)
                    table[lab] = 1.0 if (x is None or x > 0.03) else (0.5 if x > 0 else 0.0)
            scale = table.get(prev(i), 1.0) if (i >= WARM and mode == "overlay") else 1.0
            eq.append(eq[-1] * (1 - COST * abs(scale - cur)) * (1 + scale * eqw[i])); cur = scale
        return eq
    print("\nB) Regime risk overlay on the equal-weight fleet (scored from day %d)" % (WARM + 5))
    out["overlay"] = rep("equal-weight fleet + regime overlay", overlay("overlay"), WARM + 5)
    out["equal"] = rep("equal-weight fleet (baseline)", overlay("none"), WARM + 5)
    json.dump({k: v for k, v in out.items()}, open(os.path.join(BASE, "logs", "adaptive_lab.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
