"""One-off: compare slow50_v1 and slow50_challenger_a after a few days of live paper trading.
Writes logs/slow50_pair_check.md.  Paper only; reads state kept up to date by the existing hourly cron ticks.
No broker calls of its own.   python3 slow50_pair_check.py"""
import os, time
import dashboard as db
from marketcoach import kraken_portfolio as kp

BASE = os.path.dirname(os.path.abspath(__file__))
ACCTS = ["slow50_v1", "slow50_challenger_a", "slow50_challenger_b"]


def main():
    rows = []
    for n in ACCTS:
        s = kp.load(n)
        eq, _ = db._equity_sleeves(s)
        held = [c for c, sl in s["sleeves"].items() if sl["units"] > 0]
        rows.append({"name": n, "equity": round(eq, 2), "ret_pct": round((eq / s["start_cash"] - 1) * 100, 2),
                     "trades": len(s["fills"]), "held": held, "days_live": round((time.time() - s["created"]) / 86400, 1),
                     "halted": s.get("halted", False)})
    lines = [f"# slow50_v1 vs slow50_challenger_a vs slow50_challenger_b — {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}", "",
             "| account | days live | equity | return | trades | holding |", "|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['name']} | {r['days_live']} | ${r['equity']:.2f} | {r['ret_pct']:+.2f}% | {r['trades']} | {', '.join(r['held']) or 'all cash'} |")
    lines.append("")
    lines.append("Caveat: only a few days of evidence either way — not enough to declare a winner. Backtest expectation: challenger_a "
                 "(+14.7%/yr held-out, +10.5%/yr unseen) ahead of slow50_v1 (+9.4%/+8.3%), but live noise dominates over days.")
    open(os.path.join(BASE, "logs", "slow50_pair_check.md"), "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
