"""Fleet audit: how did EVERY bot do on real history, how independent are they, and which are worth keeping?
Daily bots (signal + community) are replayed over 2020-01 -> now on the same 9 coins with real fees and their own
market filters; 4h bots over their 3-year window. Report only; nothing is retired or changed.
    python3 fleet_audit.py"""
import glob, json, math, os, time
from collections import defaultdict
from marketcoach import market_sim as ms, bot_engine as be, bot_live, data, regime, market_filters as mf, kraken_fees
from marketcoach.regime_learner import account_allow
from marketcoach.portfolio_correlation import _dated_daily_equity
from marketcoach.portfolio_optimize import DEFAULT_BASKET
from marketcoach.strategy import Strategy
from marketcoach import engine

BASE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(BASE, "paper_state")


def daily_rets(ser):
    d = sorted(ser)
    return {d[i]: ser[d[i]] / ser[d[i - 1]] - 1 for i in range(1, len(d)) if ser[d[i - 1]] > 0}


def corr(a, b):
    days = sorted(set(a) & set(b))
    if len(days) < 60:
        return None
    x = [a[d] for d in days]; y = [b[d] for d in days]
    mx, my = sum(x) / len(x), sum(y) / len(y)
    sx = math.sqrt(sum((v - mx) ** 2 for v in x)); sy = math.sqrt(sum((v - my) ** 2 for v in y))
    if sx < 1e-12 or sy < 1e-12:
        return 0.0
    return sum((p - mx) * (q - my) for p, q in zip(x, y)) / (sx * sy)


def sharpe(r):
    v = list(r.values())
    if len(v) < 30: return 0.0
    m = sum(v) / len(v); s = math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1))
    return m / s * math.sqrt(365) if s > 1e-12 else 0.0


def main():
    bars = ms.load_long()
    f0, f1 = ms.filters_for(bars)
    ts = [b.ts for b in next(iter(bars.values()))]
    ok = {"none": None, "F0": (lambda i, s=f0: ts[i] in s), "F1": (lambda i, s=f1: ts[i] in s)}
    series, meta = {}, {}
    for f in sorted(glob.glob(os.path.join(STATE, "*.kraken.json"))):
        st = json.load(open(f)); name = st["account"]
        if name.startswith(("arena_", "krakendemo", "zz_")):
            continue
        flag = "retired" if st.get("retired") else ("paused" if st.get("needs_revalidation") else "")
        live_trips = sum(1 for x in st.get("fills", []) if x["side"] == "sell")
        meta[name] = {"kind": "community" if name.startswith("bot_") else ("4h" if st.get("granularity") == 14400 else "daily"),
                      "flag": flag, "live_sells": live_trips}
        try:
            if name.startswith("bot_"):
                params = dict(st["bot_params"]); params.pop("breaker", None)
                bot = bot_live.make_bot(st["bot_kind"], params, ok)
                r = be.run(bot, bars, cash=100.0)
                series[name] = dict(zip(r.ts, r.equity))
            elif st.get("granularity") == 86400:
                allow = account_allow(st, f0, bars)
                series[name] = _dated_daily_equity(st["strategy_path"], bars, 1.0, allow, False)
        except Exception as e:
            meta[name]["error"] = f"{type(e).__name__}: {e}"[:80]
    # 4h accounts on the 3-year 24-coin window (daily-sampled)
    b4 = {p: data.get_bars(p, granularity=14400, max_bars=6570) for p in [f"{c}-USD" for c in DEFAULT_BASKET]}
    a4 = regime.risk_on_timestamps("BTC-USD", granularity=14400, max_bars=6570)
    for f in sorted(glob.glob(os.path.join(STATE, "*.kraken.json"))):
        st = json.load(open(f)); name = st["account"]
        if st.get("granularity") == 14400 and name in meta:
            try:
                allow = account_allow(st, a4, b4)
                series[name] = _dated_daily_equity(st["strategy_path"], b4, 1.0, allow, True)
            except Exception as e:
                meta[name]["error"] = f"{type(e).__name__}: {e}"[:80]
    R = {n: daily_rets(s) for n, s in series.items()}
    rows = {}
    fleet_ret = defaultdict(list)
    for n, r in R.items():
        for d, v in r.items(): fleet_ret[d].append(v)
    for n, s in series.items():
        d = sorted(s); r = R[n]
        y = ms.yearly(s) if len(d) > 400 else {"calendar": {}, "green_year_pct": None, "worst_pct": None}
        peak, dd = s[d[0]], 0.0
        for k in d:
            peak = max(peak, s[k]); dd = min(dd, s[k] / peak - 1)
        cut = d[int(len(d) * 0.6)]
        oos = s[d[-1]] / s[cut] - 1
        yrs = len(d) / 365
        cagr = (s[d[-1]] / s[d[0]]) ** (1 / max(0.5, yrs)) - 1
        # correlation with the rest of the fleet (equal-weight, excluding itself) and the most similar single bot
        others = {k: v for k, v in R.items() if k != n}
        eqw = {dd_: (sum(vals) - r.get(dd_, 0)) / max(1, len(vals) - (1 if dd_ in r else 0)) for dd_, vals in fleet_ret.items()}
        best = max(((corr(r, o) or -1, k) for k, o in others.items()), default=(-1, None))
        rows[n] = {**meta[n], "days": len(d), "ret_pct": round((s[d[-1]] / s[d[0]] - 1) * 100, 1), "cagr_pct": round(cagr * 100, 1),
                   "maxdd_pct": round(dd * 100, 1), "oos_pct": round(oos * 100, 1), "sharpe": round(sharpe(r), 2),
                   "calmar": round(cagr / abs(dd), 2) if dd < -0.005 else None, "green_yr_pct": y["green_year_pct"], "worst_yr_pct": y["worst_pct"],
                   "corr_fleet": round(corr(r, eqw) or 0, 2), "twin": best[1], "twin_corr": round(best[0], 2)}
    # marginal contribution: fleet Sharpe with vs without each bot (equal-weight fleet)
    names = list(series)
    def fleet_sharpe(excl=None):
        acc = defaultdict(list)
        for k in names:
            if k == excl: continue
            for d, v in R[k].items(): acc[d].append(v)
        return sharpe({d: sum(v) / len(v) for d, v in acc.items()})
    base = fleet_sharpe()
    for n in names:
        rows[n]["marginal_sharpe"] = round(base - fleet_sharpe(n), 3)          # >0 means the fleet is better WITH this bot
    # verdicts (rules fixed before looking)
    def verdict(n, r):
        if r["flag"]: return "REMOVE", f"already {r['flag']}"
        if r["ret_pct"] <= 0 and r["oos_pct"] <= 0: return "REMOVE", "no return over history and held-out"
        redundant = r["twin"] and r["twin_corr"] >= 0.92 and (rows[r["twin"]]["calmar"] or 0) >= (r["calmar"] or 0) and not rows[r["twin"]]["flag"]
        if redundant: return "REDUNDANT", f"corr {r['twin_corr']} with {r['twin']} which has equal/better Calmar"
        if r["ret_pct"] > 0 and r["oos_pct"] > 0 and r["maxdd_pct"] > -35 and r["marginal_sharpe"] >= 0: return "KEEP", "positive history + held-out, DD ok, adds to fleet"
        if r["ret_pct"] > 0 and r["maxdd_pct"] <= -35: return "WATCH", f"drawdown {r['maxdd_pct']}% too deep"
        if r["marginal_sharpe"] < 0: return "WATCH", "fleet Sharpe is better without it"
        return "WATCH", "held-out return not positive"
    for n, r in rows.items():
        r["verdict"], r["why"] = verdict(n, r)
    order = {"KEEP": 0, "WATCH": 1, "REDUNDANT": 2, "REMOVE": 3}
    print(f"{len(rows)} bots audited | fleet Sharpe {base:.2f}\n")
    print(f"{'bot':32s} {'kind':9s} {'days':>5s} {'ret%':>7s} {'CAGR':>6s} {'maxDD':>6s} {'OOS%':>6s} {'Sharpe':>6s} {'grn-yr':>6s} {'worst-yr':>8s} {'corr fleet':>10s} {'twin (corr)':>34s} verdict")
    for n, r in sorted(rows.items(), key=lambda kv: (order[kv[1]['verdict']], -(kv[1]['calmar'] or -9))):
        print(f"{n:32s} {r['kind']:9s} {r['days']:>5d} {r['ret_pct']:>+7.1f} {r['cagr_pct']:>+6.1f} {r['maxdd_pct']:>6.1f} {r['oos_pct']:>+6.1f} {r['sharpe']:>6.2f} {str(r['green_yr_pct']):>6s} {str(r['worst_yr_pct']):>8s} {r['corr_fleet']:>10.2f} {(str(r['twin'])[:22] + ' (' + str(r['twin_corr']) + ')'):>34s} {r['verdict']}")
    print("\nverdict counts:", {v: sum(1 for r in rows.values() if r['verdict'] == v) for v in order})
    json.dump({"generated": time.strftime("%Y-%m-%d %H:%M"), "fleet_sharpe": base, "rows": rows}, open(os.path.join(BASE, "logs", "fleet_audit.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
