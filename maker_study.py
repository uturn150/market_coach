"""Does maker (limit-order) execution rescue the hourly arena strategies once
fills are modelled realistically? Three scenarios per strategy, same bars, same
signals, same regime filter:
  A legacy    strategy file's own assumed costs, limit orders always fill
  B taker     everything crosses the spread at the REAL taker fee (what paper accounts do today)
  C realistic resting limit orders: real maker fee, trade-through fills, missed
              entries, chased exits, taker stops (marketcoach/maker_fill.py)
Report only; nothing here touches an account.   python3 maker_study.py [--coins N]
"""
import copy, glob, json, os, sys, time
from marketcoach import data, engine, maker_fill, regime
from marketcoach.strategy import Strategy
from marketcoach.portfolio_optimize import DEFAULT_BASKET

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "maker_study")


def load_bars(coins):
    out = {}
    for c in coins:
        p = c if "-" in c else f"{c}-USD"
        try:
            out[p] = data.fetch_coinbase_history(p, 3600, 6008, True, cache_tag="_deep")
        except Exception as e:
            print("skip", p, e)
    return out


def hourly_regime(bars):
    daily = regime.risk_on_timestamps("BTC-USD", 86400, 200, 1500)
    return {b.ts for b in bars if ((b.ts // 86400) * 86400 - 86400) in daily}


def flat_trips(res, rate):
    from marketcoach import metrics
    return [n for _, n in metrics.roundtrips(res, rate)]


def pf(trips):
    w = sum(t for t in trips if t > 0); l = -sum(t for t in trips if t < 0)
    return round(w / l, 2) if l > 0 else (999.0 if w > 0 else 0.0)


def run_one(spec, coin_bars, allow, scenario, cfg):
    per = 50.0 / len(coin_bars)
    trips, final, agg = [], 0.0, {}
    for bars in coin_bars.values():
        s = copy.deepcopy(spec)
        if scenario == "B":
            s["costs"] = {"order_type": "taker", "taker_fee_bps": cfg["taker"] * 1e4,
                          "half_spread_bps": cfg["hs"] * 1e4, "slippage_bps": cfg["slip"] * 1e4}
        st = Strategy(s)
        if scenario == "C":
            stats = {}
            res = engine.run(st, bars, cash=per, allow_buy_ts=allow, maker=cfg, stats=stats)
            trips += maker_fill.trips_from_stats(stats)
            for k, v in stats.items():
                if k != "eff_fills":
                    agg[k] = agg.get(k, 0) + v
        else:
            res = engine.run(st, bars, cash=per, allow_buy_ts=allow)
            trips += flat_trips(res, st.side_rate)
        final += res.equity[-1]
    n = len(trips)
    return {"trips": n, "avg_net_pct": round(100 * sum(trips) / n, 3) if n else None,
            "pf": pf(trips), "return_pct": round((final / 50.0 - 1) * 100, 1), "exec": agg}


def main():
    ncoins = int(sys.argv[sys.argv.index("--coins") + 1]) if "--coins" in sys.argv else 12
    coin_bars = load_bars(DEFAULT_BASKET[:ncoins])
    n = min(len(b) for b in coin_bars.values())
    coin_bars = {k: v[-n:] for k, v in coin_bars.items()}   # tail-align calendars
    first = next(iter(coin_bars.values()))
    print(f"{len(coin_bars)} coins x {n} hourly bars ({n/24:.0f} days)")
    allow = hourly_regime(first)
    cfg = maker_fill.config()
    hold = sum(50.0 / len(coin_bars) * (b[-1].close / b[0].close) for b in coin_bars.values()) / 50 - 1
    rows = {}
    for path in sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), "strategies", "arena_*.json"))):
        name = os.path.basename(path)[:-5]
        spec = json.load(open(path))
        rows[name] = {sc: run_one(spec, coin_bars, allow, sc, cfg) for sc in "ABC"}
        r = rows[name]
        c = r["C"]["exec"]
        fr = c.get("buy_fills", 0) / c["buy_attempts"] if c.get("buy_attempts") else 0
        print(f"{name:26s} A {r['A']['avg_net_pct']}%/{r['A']['trips']}t  B {r['B']['avg_net_pct']}%  "
              f"C {r['C']['avg_net_pct']}% PF{r['C']['pf']} ret {r['C']['return_pct']}%  buy-fill {fr:.0%}")
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, f"study_{time.strftime('%Y%m%d_%H%M')}.json")
    json.dump({"bars": n, "coins": list(coin_bars), "hold_return_pct": round(hold * 100, 1),
               "cfg": cfg, "rows": rows}, open(path, "w"), indent=1)
    print("hold (unfiltered) return", round(hold * 100, 1), "% ->", path)


if __name__ == "__main__":
    main()
