"""How often is each strategy (and combinations of them) GREEN — by day and by week — and is that
stable out of sample?  Uses the same pooled daily equity as the correlation work: real fees, the
account's own filters, held-out = the last 40% of history.  Report only.

A green DAY = pooled equity up vs the previous day (flat days, e.g. all cash, counted separately).
A green WEEK = up vs the previous Sunday-close (UTC weeks).  Also: worst week, longest run of red
weeks, and the number of weeks it was invested at all.   python3 green_study.py"""
import glob, json, os, time
from collections import defaultdict
from marketcoach import data, regime, market_filters as mf
from marketcoach.portfolio_correlation import _dated_daily_equity
from marketcoach.portfolio_optimize import DEFAULT_BASKET
from marketcoach.regime_learner import account_allow

BASE = os.path.dirname(os.path.abspath(__file__))


def week_key(ts):
    return (ts // 86400 + 3) // 7      # weeks end Sunday UTC (epoch day 0 was a Thursday)


def stats(series):
    days = sorted(series)
    if len(days) < 30:
        return None
    rets = [series[days[i]] / series[days[i - 1]] - 1 for i in range(1, len(days))]
    up = sum(1 for r in rets if r > 1e-9); dn = sum(1 for r in rets if r < -1e-9); flat = len(rets) - up - dn
    wk = {}
    for d in days:
        wk[week_key(d)] = series[d]          # last day of each week
    ks = sorted(wk)
    wr = [wk[ks[i]] / wk[ks[i - 1]] - 1 for i in range(1, len(ks))]
    wu = sum(1 for r in wr if r > 1e-9); wd = sum(1 for r in wr if r < -1e-9)
    run = worst_run = 0
    for r in wr:
        run = run + 1 if r < -1e-9 else 0
        worst_run = max(worst_run, run)
    peak, mdd = series[days[0]], 0.0
    for d in days:
        peak = max(peak, series[d]); mdd = min(mdd, series[d] / peak - 1)
    return {"days": len(rets), "green_day_pct": round(100 * up / len(rets), 1),
            "green_of_active_pct": round(100 * up / max(1, up + dn), 1), "flat_pct": round(100 * flat / len(rets), 1),
            "weeks": len(wr), "green_week_pct": round(100 * wu / max(1, len(wr)), 1),
            "green_of_active_wk_pct": round(100 * wu / max(1, wu + wd), 1),
            "worst_week_pct": round(100 * min(wr), 1) if wr else 0, "max_red_week_run": worst_run,
            "total_return_pct": round((series[days[-1]] / series[days[0]] - 1) * 100, 1), "max_dd_pct": round(mdd * 100, 1)}


def split(series, frac=0.6):
    days = sorted(series); c = days[int(len(days) * frac)]
    return {d: v for d, v in series.items() if d < c}, {d: v for d, v in series.items() if d >= c}


def combine(series_list):
    """Equal-weight portfolio of several strategies' pooled equity series."""
    days = sorted(set.intersection(*[set(s) for s in series_list]))
    return {d: sum(s[d] for s in series_list) for d in days}


def load_daily_universe():
    bars = {f"{c}-USD": data.get_bars(f"{c}-USD", 86400, max_bars=1500) for c in DEFAULT_BASKET[:12]}
    f0 = regime.risk_on_timestamps("BTC-USD", 86400, max_bars=1500)
    daily = data.get_bars("BTC-USD", 86400, max_bars=1500)
    return bars, f0, f0 & mf.f1_timestamps(daily)


def main():
    bars, f0, f1 = load_daily_universe()
    per = 50.0 / len(bars)
    rows = {}
    paths = {}
    for f in sorted(glob.glob(os.path.join(BASE, "paper_state", "*.kraken.json"))):
        st = json.load(open(f))
        if st.get("granularity") != 86400 or st["account"].startswith("krakendemo"):
            continue
        paths[st["account"]] = st
    for name, st in paths.items():
        allow = account_allow(st, f0, bars)
        if st.get("market_filter") != "F1" and not st.get("avoid_regimes"):
            pass
        try:
            s = _dated_daily_equity(st["strategy_path"], bars, per, allow, False)
        except Exception as e:
            continue
        i, o = split(s)
        rows[name] = {"all": stats(s), "oos": stats(o), "series": s}
        # also the same strategy behind F1 for plain accounts
        if st.get("market_filter") != "F1":
            s1 = _dated_daily_equity(st["strategy_path"], bars, per, f1 & allow, False)
            rows[name + "+F1"] = {"all": stats(s1), "oos": stats(split(s1)[1]), "series": s1}
    print(f"{'strategy':34s} {'OOS grn wk%':>11s} {'of active':>9s} {'grn day%':>8s} {'flat%':>6s} {'worst wk':>8s} {'red run':>7s} {'OOS ret':>8s}  | full-history grn wk%")
    for name, r in sorted(rows.items(), key=lambda kv: -(kv[1]["oos"] or {}).get("green_week_pct", 0)):
        o, a = r["oos"], r["all"]
        if not o or not a: continue
        print(f"{name:34s} {o['green_week_pct']:>10.1f}% {o['green_of_active_wk_pct']:>8.1f}% {o['green_day_pct']:>7.1f}% {o['flat_pct']:>5.1f}% {o['worst_week_pct']:>7.1f}% {o['max_red_week_run']:>7d} {o['total_return_pct']:>+7.1f}%  | {a['green_week_pct']}%")
    # greedy combination: choose members on IN-SAMPLE green-week %, judge OOS
    cand = {n: r for n, r in rows.items() if r["all"]}
    chosen, best = [], None
    pool = list(cand)
    for _ in range(6):
        pick = None
        for n in pool:
            if n in chosen: continue
            comb = combine([cand[m]["series"] for m in chosen + [n]])
            i, o = split(comb)
            si = stats(i)
            if not si: continue
            score = (si["green_week_pct"], -abs(si["max_dd_pct"]))
            if pick is None or score > pick[0]:
                pick = (score, n)
        if pick is None: break
        chosen.append(pick[1])
        comb = combine([cand[m]["series"] for m in chosen]); i, o = split(comb)
        si, so = stats(i), stats(o)
        print(f"\nportfolio of {len(chosen)} {chosen}\n  in-sample green wk {si['green_week_pct']}% (dd {si['max_dd_pct']}%)  |  OUT-OF-SAMPLE green wk {so['green_week_pct']}%, green day {so['green_day_pct']}%, worst wk {so['worst_week_pct']}%, ret {so['total_return_pct']:+.1f}%, dd {so['max_dd_pct']}%")
    out = os.path.join(BASE, "logs", "green_study.json")
    json.dump({k: {"all": v["all"], "oos": v["oos"]} for k, v in rows.items()}, open(out, "w"), indent=1)


if __name__ == "__main__":
    main()
