"""Independent-evidence test: run each champion on coins that were NEVER part of
the basket used to select/tune/validate it. Same strategy file, real fee tier,
same regime filter, no re-tuning. A real edge should transfer to unseen coins;
an overfit one will not.   python3 holdout_coins.py
Survivorship caveat: only coins that still exist and trade on Kraken today."""
import json, os, sys, time
from . import data, engine, kraken_fees, metrics, regime
from .broker_kraken import PAIR_MAP
from .portfolio_optimize import DEFAULT_BASKET
from .strategy import Strategy
from .walkforward_v2 import _regime_hold_return

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIN_TRIPS, MIN_COINS = 30, 8


def holdout_products():
    inb = {f"{c}-USD" for c in DEFAULT_BASKET}
    return [p for p in PAIR_MAP if p not in inb]


def load(products):
    out = {}
    for p in products:
        try:
            b = data.get_bars(p, granularity=86400, max_bars=1500)
            if len(b) >= 400:
                out[p] = b
        except Exception:
            pass
    return out


def evaluate(spec, coin_bars, allow):
    spec, _ = kraken_fees.apply_to_spec(spec)
    per = 50.0 / len(coin_bars)
    trips, end, hold, rh = [], 0.0, 0.0, 0.0
    wins_coins = 0
    for b in coin_bars.values():
        st = Strategy(spec)
        r = engine.run(st, b, cash=per, allow_buy_ts=allow)
        t = [n for _, n in metrics.roundtrips(r, st.side_rate)]
        trips += t
        end += r.equity[-1]
        rhold = _regime_hold_return(b, allow, st.side_rate)
        rh += per * (1 + rhold)
        wins_coins += (r.equity[-1] / per) > (1 + rhold)
    n = len(trips)
    w = sum(x for x in trips if x > 0); l = -sum(x for x in trips if x <= 0)
    return {"coins": len(coin_bars), "trips": n, "avg_net_pct": round(100 * sum(trips) / n, 2) if n else None,
            "pf": round(w / l, 2) if l > 0 else (999 if w > 0 else 0),
            "return_pct": round((end / 50 - 1) * 100, 1), "regime_hold_pct": round((rh / 50 - 1) * 100, 1),
            "excess_pts": round((end - rh) / 50 * 100, 1), "coins_beating_hold": wins_coins}


def verdict(r):
    if r["coins"] < MIN_COINS or r["trips"] < MIN_TRIPS:
        return "INSUFFICIENT", f"{r['coins']} coins / {r['trips']} trips"
    ok = r["return_pct"] > 0 and r["pf"] > 1 and r["excess_pts"] > 0
    return ("PASS" if ok else "FAIL",
            f"net {r['return_pct']:+.1f}%, PF {r['pf']}, {r['excess_pts']:+.1f}pts vs regime-hold, "
            f"{r['coins_beating_hold']}/{r['coins']} coins beat it ({r['trips']} trips)")


def main():
    coin_bars = load(holdout_products())
    n = min(len(b) for b in coin_bars.values())
    allow = regime.risk_on_timestamps("BTC-USD", granularity=86400, max_bars=1500)
    print(f"{len(coin_bars)} hold-out coins: {', '.join(p[:-4] for p in coin_bars)}")
    rows = {}
    from .learner import CHAMPIONS
    for account, sname in CHAMPIONS.items():
        spec = json.load(open(os.path.join(BASE, "strategies", f"{sname}.json")))
        r = evaluate(spec, coin_bars, allow)
        v, why = verdict(r)
        rows[account] = {**r, "verdict": v, "why": why}
        print(f"{account:16s} {v:12s} {why}")
    os.makedirs(os.path.join(BASE, "logs", "holdout"), exist_ok=True)
    out = os.path.join(BASE, "logs", "holdout", f"holdout_{time.strftime('%Y%m%d_%H%M')}.json")
    json.dump({"coins": list(coin_bars), "rows": rows}, open(out, "w"), indent=1)
    json.dump({"coins": list(coin_bars), "rows": rows, "generated": time.strftime("%Y-%m-%d %H:%M")},
              open(os.path.join(BASE, "logs", "holdout", "latest.json"), "w"), indent=1)
    print("->", out)


if __name__ == "__main__":
    main()
