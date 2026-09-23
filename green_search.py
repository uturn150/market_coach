"""Search for a CONSISTENTLY GREEN strategy (green weeks / green days), honestly.

Goal metric: share of ACTIVE weeks that end green (weeks a strategy is flat in cash prove
nothing), plus overall green-week share, worst week and drawdown.  Pre-registered space:
dip-buy / mean-reversion families inside an uptrend (the only structurally different family the
factory found: ~0.2 correlation with the trend fleet), behind the F1 market filter, real fees,
with small take-profit + stop.  Selection uses ONLY in-sample (first 60%) on the 12 'large' coins;
the winners are then judged on (a) the held-out 40% of the same coins, (b) 'mid' coins never used
to choose them, (c) 20 fully unseen coins.  A strategy is only 'consistent' if it stays green-ish
on ALL of those AND makes money AND has a survivable worst week.   python3 green_search.py"""
import json, os, time
from marketcoach import data, engine, kraken_fees, regime, market_filters as mf, holdout
from marketcoach.factory import UNIVERSES
from marketcoach.optimize import candidates
from marketcoach.strategy import Strategy
from marketcoach.factory import _tpl
from green_study import stats, split

BASE = os.path.dirname(os.path.abspath(__file__))
FEES = {"order_type": "taker", "taker_fee_bps": 80, "half_spread_bps": 5, "slippage_bps": 3, "maker_fee_bps": 40}
RISK = {"take_profit_pct": "{tp}", "stop_loss_pct": "{sl}"}
COMMON = {"tp": [0.03, 0.05, 0.08], "sl": [0.06, 0.10]}


def tpl(name, params, entry, exits):
    t = _tpl(name, {**params, **COMMON}, entry, exits)
    t["risk"] = RISK
    t["params"].pop("trail", None)
    return t


FAM = {
    "rsi_dip": tpl("RSI dip", {"n": [7, 14], "lo": [25, 35], "trend": [50, 100]},
                   [("dip", "rsi:{n}", "<", "{lo}"), ("trend", "price", ">", "sma:{trend}")],
                   [("rsi:{n}", "cross_above", 55)]),
    "bb_dip": tpl("BB dip", {"n": [20], "k": [1.5, 2], "trend": [50, 100]},
                  [("dip", "price", "<", "bollinger_lower:{n}:{k}"), ("trend", "price", ">", "sma:{trend}")],
                  [("price", "cross_above", "bollinger_mid:{n}")]),
    "willr_dip": tpl("Williams dip", {"n": [14], "lo": [-80, -90], "trend": [50, 100]},
                     [("dip", "willr:{n}", "<", "{lo}"), ("trend", "price", ">", "sma:{trend}")],
                     [("willr:{n}", "cross_above", -30)]),
    "stoch_dip": tpl("Stoch dip", {"n": [14], "lo": [15, 25], "trend": [50, 100]},
                     [("dip", "stoch_k:{n}", "<", "{lo}"), ("trend", "price", ">", "sma:{trend}")],
                     [("stoch_k:{n}", "cross_above", 70)]),
    "cci_dip": tpl("CCI dip", {"n": [14, 20], "lo": [-100, -150], "trend": [50, 100]},
                   [("dip", "cci:{n}", "<", "{lo}"), ("trend", "price", ">", "sma:{trend}")],
                   [("cci:{n}", "cross_above", 0)]),
    "kelt_dip": tpl("Keltner dip", {"n": [20], "trend": [50, 100]},
                    [("dip", "price", "<", "keltner_lower:{n}"), ("trend", "price", ">", "sma:{trend}")],
                    [("price", "cross_above", "sma:{n}")]),
}


def equity(spec, bars, allow):
    """Pooled daily equity {day_ts: equity} across coins (tail-aligned), $2 per coin."""
    n = min(len(b) for b in bars.values())
    ts = [b.ts for b in list(bars.values())[0][-n:]]
    tot = [0.0] * n
    for b in bars.values():
        r = engine.run(Strategy(spec), b[-n:], cash=2.0, allow_buy_ts=allow)
        for i in range(n):
            tot[i] += r.equity[i]
    return dict(zip(ts, tot))


def load(coins):
    out = {}
    for c in coins:
        p = c if "-" in c else f"{c}-USD"
        try:
            b = data.get_bars(p, 86400, max_bars=1500)
            if len(b) > 400: out[p] = b
        except Exception:
            pass
    return out


def main():
    large = load(UNIVERSES["large"]); mid = load(UNIVERSES["mid"])
    unseen = load([p[:-4] for p in holdout.holdout_products()])
    f0 = regime.risk_on_timestamps("BTC-USD", 86400, max_bars=1500)
    f1 = f0 & mf.f1_timestamps(data.get_bars("BTC-USD", 86400, max_bars=1500))
    scored = []
    for fam, t in FAM.items():
        for values, spec in candidates(t):
            spec, _ = kraken_fees.apply_to_spec(spec)
            s = equity(spec, large, f1)
            i, o = split(s)
            si = stats(i)
            if not si or si["weeks"] < 40 or si["total_return_pct"] <= 0 or si["max_dd_pct"] < -30:
                continue
            scored.append((si["green_of_active_wk_pct"], si["green_week_pct"], fam, values, spec, si))
    print(f"{sum(len(list(candidates(t))) for t in FAM.values())} candidates, {len(scored)} profitable in-sample with dd<30%")
    scored.sort(key=lambda t: (-t[0], -t[1]))            # in-sample only
    best_per_fam = {}
    for row in scored:
        best_per_fam.setdefault(row[2], []).append(row)
    print(f"\n{'candidate (top 3 per family by IN-SAMPLE green-of-active-weeks)':64s} IS g/act  | OOS large: g/act  g/all  worst  ret   dd | MID g/act g/all ret | UNSEEN g/act g/all ret")
    results = []
    for fam, rows in best_per_fam.items():
        for ga, gw, _, values, spec, si in rows[:3]:
            ol = stats(split(equity(spec, large, f1))[1])
            um = stats(equity(spec, mid, f1))
            uu = stats(equity(spec, unseen, f1))
            if not (ol and um and uu):
                continue
            results.append((fam, values, spec, si, ol, um, uu))
            print(f"{fam + ' ' + json.dumps(values):64s} {ga:5.1f}%  | {ol['green_of_active_wk_pct']:5.1f}% {ol['green_week_pct']:5.1f}% {ol['worst_week_pct']:6.1f}% {ol['total_return_pct']:+6.1f}% {ol['max_dd_pct']:6.1f}% | {um['green_of_active_wk_pct']:5.1f}% {um['green_week_pct']:5.1f}% {um['total_return_pct']:+6.1f}% | {uu['green_of_active_wk_pct']:5.1f}% {uu['green_week_pct']:5.1f}% {uu['total_return_pct']:+6.1f}%")
    good = [r for r in results if r[4]["green_of_active_wk_pct"] >= 60 and r[5]["green_of_active_wk_pct"] >= 60
            and r[6]["green_of_active_wk_pct"] >= 60 and min(r[4]["total_return_pct"], r[5]["total_return_pct"], r[6]["total_return_pct"]) > 0
            and r[4]["worst_week_pct"] > -10]
    print(f"\nCONSISTENT (>=60% of active weeks green on ALL three, positive return on all, worst OOS week > -10%): {len(good)}")
    for r in good:
        print("  ", r[0], r[1])
    os.makedirs(os.path.join(BASE, "logs"), exist_ok=True)
    json.dump([{"family": r[0], "params": r[1], "spec": r[2], "in_sample": r[3], "oos_large": r[4], "mid": r[5], "unseen": r[6]}
               for r in results], open(os.path.join(BASE, "logs", "green_search.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
