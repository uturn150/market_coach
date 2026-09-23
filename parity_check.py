"""Code-parity check: replay history through the ACTUAL live tick code (kraken_portfolio.tick) with a
simulated clock and data feed, then compare with the backtest engine on the same bars.

Isolation: temp state dir, fake broker (no network, no orders), notifications stubbed, fills priced
by the SAME flat cost model the backtest uses, so any difference is LOGIC/TIMING, not cost model.
Run twice: neutral sizing (live vol-weighting/correlation sizing off) to isolate signal timing, and
with the real sizing to measure its effect.   python3 parity_check.py [strategy_file] [n_coins] [n_bars]"""
import json, os, shutil, sys, tempfile, time
from marketcoach import data, engine, regime, kraken_portfolio as kp, kraken_data, paper_exec, risk, alerts
from marketcoach.strategy import Strategy
from marketcoach.portfolio_optimize import DEFAULT_BASKET

BASE = os.path.dirname(os.path.abspath(__file__))
GRAN = 86400


class FakeBroker:
    live, dry_run = False, True
    px = {}
    def __init__(self, dry_run=True): pass
    def ticker_price(self, product): return FakeBroker.px[product]
    ticker_price_cached = ticker_price
    def market_order(self, *a, **k): return {"dry_run": True}
    def balance(self): return {}
    def prefetch_prices(self, *a): pass


def _fake_exec(rate):
    def buy(broker, product, notional, signal_price=None):
        px = broker.ticker_price(product)
        units = notional / (px * (1 + rate))
        return {"side": "buy", "source": "parity", "levels_used": 0, "signal_price": signal_price, "mid": px, "expected_fill": px,
                "sim_fill": px * (1 + rate), "filled_units": units, "filled_fraction": 1.0, "unfilled_notional": 0.0,
                "spread_cost_usd": 0.0, "slippage_cost_usd": 0.0, "fee_usd": notional * rate / (1 + rate),
                "total_exec_cost_usd": notional * rate / (1 + rate), "total_cost_usd": notional}
    def sell(broker, product, units, signal_price=None):
        px = broker.ticker_price(product)
        gross = units * px
        return {"side": "sell", "source": "parity", "levels_used": 0, "signal_price": signal_price, "mid": px, "expected_fill": px,
                "sim_fill": px * (1 - rate), "filled_units": units, "filled_fraction": 1.0, "unfilled_notional": 0.0,
                "spread_cost_usd": 0.0, "slippage_cost_usd": 0.0, "fee_usd": gross * rate, "total_exec_cost_usd": gross * rate,
                "gross_proceeds_usd": gross, "net_proceeds_usd": gross * (1 - rate)}
    return buy, sell


def replay(spec_path, coins, n_bars, neutral_sizing, verbose=False):
    strat = Strategy.load(spec_path)
    rate = strat.side_rate
    allbars = {f"{c}-USD": data.get_bars(f"{c}-USD", GRAN, max_bars=1500) for c in coins}
    n = min(len(b) for b in allbars.values())
    N = min(n_bars, n)
    series = {p: b[-N:] for p, b in allbars.items()}
    ts = [b.ts for b in series["BTC-USD"]]
    f0 = regime.risk_on_timestamps("BTC-USD", GRAN, max_bars=1500)
    per = 50.0 / len(series)

    # ---- backtest side ----
    bt = {p: engine.run(Strategy.load(spec_path), b, cash=per, allow_buy_ts=f0) for p, b in series.items()}

    # ---- live side: real tick(), simulated clock/feed ----
    tmp = tempfile.mkdtemp(prefix="parity_")
    saved = (kp.STATE_DIR, kp.data.fetch_recent, kp.Kraken, kp.paper_exec.simulate_market_buy, kp.paper_exec.simulate_market_sell,
             kp.regime.check_and_alert, kp.alerts.notify, time.sleep, kp.risk.vol_target_weights, kp.risk.correlation_size_multiplier)
    cur = {"d": 0}
    try:
        kp.STATE_DIR = tmp
        kp.data.fetch_recent = lambda p, g=GRAN: series[p][:cur["d"] + 1]
        kp.Kraken = FakeBroker
        kp.paper_exec.simulate_market_buy, kp.paper_exec.simulate_market_sell = _fake_exec(rate)
        kp.regime.check_and_alert = lambda *a, **k: ts[cur["d"]] in f0
        kp.alerts.notify = lambda *a, **k: True
        time.sleep = lambda *_: None
        if neutral_sizing:
            kp.risk.vol_target_weights = lambda cv, **k: {p: 1.0 for p in cv}
            kp.risk.correlation_size_multiplier = lambda *a, **k: 1.0
        name = "zz_parity"
        sleeves = {p: {"cash": per, "units": 0.0, "last_px": series[p][0].close, "last_bar_ts": 0, "pending": None,
                       "peak": 0.0, "in_position": False, "entry_px": 0.0} for p in series}
        kp._save({"account": name, "strategy_path": spec_path, "granularity": GRAN, "start_cash": 50.0, "sleeves": sleeves,
                  "created": 0, "regime_filter": True, "max_drawdown": 0.99, "halted": False, "fills": [], "paper": True,
                  "peak_equity": 50.0, "avoid_regimes": [], "market_filter": None})
        for d in range(N):
            cur["d"] = d
            for p in series:
                FakeBroker.px[p] = series[p][d].close      # price "now" = just after bar d closed
            kp.tick(name, now=ts[d] + GRAN + 30, verbose=False)
        st = kp.load(name)
    finally:
        (kp.STATE_DIR, kp.data.fetch_recent, kp.Kraken, kp.paper_exec.simulate_market_buy, kp.paper_exec.simulate_market_sell,
         kp.regime.check_and_alert, kp.alerts.notify, time.sleep, kp.risk.vol_target_weights, kp.risk.correlation_size_multiplier) = saved
        shutil.rmtree(tmp, ignore_errors=True)
        for f in (os.path.join(BASE, "logs", "zz_parity.jsonl"),):
            if os.path.exists(f): os.remove(f)

    idx = {t: i for i, t in enumerate(ts)}
    rep = {"coins": len(series), "bars": N, "neutral_sizing": neutral_sizing, "per_coin": {}}
    lags, bt_n, live_n = [], 0, 0
    for p in series:
        b_fills = [(idx[f.ts], f.side) for f in bt[p].fills]
        # live fill ts is the bar being processed (bar i, closed); it executes at that bar's close = open of bar i+1
        l_fills = [(idx[f["ts"]] + 1, f["side"]) for f in st["fills"] if f["coin"] == p]
        bt_n += len(b_fills); live_n += len(l_fills)
        pair = list(zip(b_fills, l_fills))
        for (bi, bs), (li, ls) in pair:
            if bs == ls:
                lags.append(li - bi)
        eq_l = st["sleeves"][p]["cash"] + st["sleeves"][p]["units"] * st["sleeves"][p]["last_px"]
        rep["per_coin"][p] = {"bt_trades": len(b_fills), "live_trades": len(l_fills), "bt_final": round(bt[p].equity[-1], 4),
                              "live_final": round(eq_l, 4), "first_bt": b_fills[:3], "first_live": l_fills[:3]}
    rep["bt_fills"], rep["live_fills"] = bt_n, live_n
    rep["lag_bars_hist"] = {str(k): lags.count(k) for k in sorted(set(lags))}
    rep["bt_equity"] = round(sum(bt[p].equity[-1] for p in series), 3)
    rep["live_equity"] = round(sum(v["live_final"] for v in rep["per_coin"].values()), 3)
    return rep


def main():
    spec = sys.argv[1] if len(sys.argv) > 1 else "strategies/fast_swing.json"
    ncoins = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    nbars = int(sys.argv[3]) if len(sys.argv) > 3 else 500
    coins = DEFAULT_BASKET[:ncoins]
    for neutral in (True, False):
        r = replay(spec, coins, nbars, neutral)
        print(f"\n== {spec} | {r['coins']} coins x {r['bars']} bars | sizing={'neutral' if neutral else 'real (vol/corr weighted)'}")
        print(f"   fills: backtest {r['bt_fills']} vs live {r['live_fills']}   | final equity: backtest ${r['bt_equity']} vs live ${r['live_equity']} (start $50)")
        print(f"   execution lag of live fills vs backtest fills (bars, matched by order): {r['lag_bars_hist']}")
        for p, v in list(r["per_coin"].items())[:3]:
            print(f"   {p}: bt {v['bt_trades']} trades / live {v['live_trades']} | first bt {v['first_bt']} live {v['first_live']}")
        json.dump(r, open(os.path.join(BASE, "logs", f"parity_{'neutral' if neutral else 'real'}.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
