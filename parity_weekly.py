"""Weekly parity guard: replays history through the live tick code and compares with the backtest
engine (see parity_check.py). Alerts if live behaviour drifts from what the backtests assume.
Pass criteria per strategy (neutral sizing, fixed set): >=90% of matched fills on the same bar,
fill counts within 5%, final equity within 3% of the backtest's.   Cron: Sundays 03:20."""
import json, os, time
from parity_check import replay
from marketcoach import alerts
from marketcoach.portfolio_optimize import DEFAULT_BASKET

BASE = os.path.dirname(os.path.abspath(__file__))
STRATS = ["strategies/fast_swing.json", "strategies/bollinger_breakout.json", "strategies/multi_signal.json"]
N_COINS, N_BARS = 6, 500


def main():
    rows, bad = [], []
    for s in STRATS:
        try:
            r = replay(s, DEFAULT_BASKET[:N_COINS], N_BARS, neutral_sizing=True)
        except Exception as e:
            rows.append({"strategy": s, "error": f"{type(e).__name__}: {e}"[:200]}); bad.append(f"{s}: crashed ({type(e).__name__})"); continue
        lag = r["lag_bars_hist"]; tot = sum(lag.values()) or 1
        same = lag.get("0", 0) / tot
        fills_gap = abs(r["live_fills"] - r["bt_fills"]) / max(1, r["bt_fills"])
        eq_gap = abs(r["live_equity"] - r["bt_equity"]) / r["bt_equity"]
        ok = same >= 0.90 and fills_gap <= 0.05 and eq_gap <= 0.03
        rows.append({"strategy": s, "same_bar_share": round(same, 3), "fills_gap": round(fills_gap, 3),
                     "equity_gap": round(eq_gap, 4), "bt_equity": r["bt_equity"], "live_equity": r["live_equity"], "ok": ok})
        if not ok:
            bad.append(f"{s}: same-bar {same:.0%}, fills {r['live_fills']} vs {r['bt_fills']}, equity ${r['live_equity']} vs ${r['bt_equity']}")
    rec = {"t": int(time.time()), "iso": time.strftime("%Y-%m-%d %H:%M:%S"), "ok": not bad, "rows": rows}
    os.makedirs(os.path.join(BASE, "logs"), exist_ok=True)
    with open(os.path.join(BASE, "logs", "parity_weekly.jsonl"), "a") as f:
        f.write(json.dumps(rec) + "\n")
    print(json.dumps(rec, indent=1))
    if bad:
        alerts.notify("market_coach: PARITY DRIFT", "Live tick code no longer matches the backtest engine:\n" + "\n".join(bad),
                      priority="high", tags=["warning"])
    return not bad


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
