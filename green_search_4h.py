"""4-hour version of green_search.py: same pre-registered dip families, periods x6 (a day = 6 bars),
same take-profit/stop grid, F1 (daily F1 mapped onto 4h bars), real fees.  Selection = in-sample
(first 60%) on 'large'; judged on held-out large, 'mid', and 20 unseen coins.  Equity is sampled
at the 20:00-UTC bar so days/weeks line up with the daily study.   python3 green_search_4h.py"""
import json, os
from marketcoach import data, engine, kraken_fees, regime, market_filters as mf, holdout
from marketcoach.factory import UNIVERSES, scaled
from marketcoach.optimize import candidates
from marketcoach.strategy import Strategy
from green_study import stats, split
from green_search import FAM

BASE = os.path.dirname(os.path.abspath(__file__))
GRAN, MAXB = 14400, 6570


def equity4(spec, bars, allow):
    n = min(len(b) for b in bars.values())
    ts = [b.ts for b in list(bars.values())[0][-n:]]
    tot = [0.0] * n
    for b in bars.values():
        r = engine.run(Strategy(spec), b[-n:], cash=2.0, allow_buy_ts=allow)
        for i in range(n):
            tot[i] += r.equity[i]
    return {t - 72000: v for t, v in zip(ts, tot) if t % 86400 == 72000}   # day's close = 20:00 bar


def load4(coins):
    out = {}
    for c in coins:
        p = c if "-" in c else f"{c}-USD"
        try:
            b = data.get_bars(p, granularity=GRAN, max_bars=MAXB)
            if len(b) > 2000: out[p] = b
        except Exception as e:
            print("skip", p, type(e).__name__)
    return out


def main():
    large = load4(UNIVERSES["large"]); mid = load4(UNIVERSES["mid"])
    print("loading unseen coins (hourly history, slow first time)...", flush=True)
    unseen = load4([p[:-4] for p in holdout.holdout_products()])
    daily = data.get_bars("BTC-USD", 86400, max_bars=1500)
    f1 = regime.risk_on_timestamps("BTC-USD", granularity=GRAN, max_bars=MAXB) & mf.f1_timestamps_4h(daily, large["BTC-USD"])
    scored = []
    for fam, t in FAM.items():
        for values, spec in candidates(scaled(t, GRAN)):
            spec, _ = kraken_fees.apply_to_spec(spec)
            si = stats(split(equity4(spec, large, f1))[0])
            if not si or si["weeks"] < 40 or si["total_return_pct"] <= 0 or si["max_dd_pct"] < -30:
                continue
            scored.append((si["green_of_active_wk_pct"], si["green_week_pct"], fam, values, spec, si))
    print(f"{len(scored)} profitable in-sample with dd<30%", flush=True)
    scored.sort(key=lambda t: (-t[0], -t[1]))
    by = {}
    for r in scored:
        by.setdefault(r[2], []).append(r)
    print(f"\n{'candidate (top 3/family by IN-SAMPLE green-of-active)':68s} IS   | OOS large: g/act g/all worst ret dd | MID g/act g/all ret | UNSEEN g/act g/all ret")
    results = []
    for fam, rows in by.items():
        for ga, gw, _, values, spec, si in rows[:3]:
            ol = stats(split(equity4(spec, large, f1))[1]); um = stats(equity4(spec, mid, f1)); uu = stats(equity4(spec, unseen, f1))
            if not (ol and um and uu): continue
            results.append((fam, values, spec, si, ol, um, uu))
            print(f"{fam + ' ' + json.dumps(values):68s} {ga:5.1f}% | {ol['green_of_active_wk_pct']:5.1f}% {ol['green_week_pct']:5.1f}% {ol['worst_week_pct']:6.1f}% {ol['total_return_pct']:+6.1f}% {ol['max_dd_pct']:6.1f}% | {um['green_of_active_wk_pct']:5.1f}% {um['green_week_pct']:5.1f}% {um['total_return_pct']:+6.1f}% | {uu['green_of_active_wk_pct']:5.1f}% {uu['green_week_pct']:5.1f}% {uu['total_return_pct']:+6.1f}%", flush=True)
    good = [r for r in results if min(r[4]["green_of_active_wk_pct"], r[5]["green_of_active_wk_pct"], r[6]["green_of_active_wk_pct"]) >= 60
            and min(r[4]["total_return_pct"], r[5]["total_return_pct"], r[6]["total_return_pct"]) > 0 and r[4]["worst_week_pct"] > -10]
    print(f"\nCONSISTENT (same bar as daily): {len(good)}")
    json.dump([{"family": r[0], "params": r[1], "spec": r[2], "in_sample": r[3], "oos_large": r[4], "mid": r[5], "unseen": r[6]} for r in results],
              open(os.path.join(BASE, "logs", "green_search_4h.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
