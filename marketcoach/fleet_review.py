"""Weekly review of the live-paper community bots (bot_*): what really happened vs what the backtests
promised. Read-only. Flags a bot when its live drawdown is worse than the backtest's worst case, or when it has
enough trades to judge and its win rate is far below the backtest win rate. Writes logs/fleet_review/latest.md
and sends one short Telegram message.   Cron: Mondays 14:00 UTC."""
import json, os, time
from . import alerts, journal

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(BASE, "paper_state")
OUT = os.path.join(BASE, "logs", "fleet_review")
# backtest reference per bot family (from bot_lab / bot_universes): (expected win%, worst maxDD%)
REF = {"bot_grid": (64, -12.0), "bot_dca": (92, -14.0)}


def review():
    rows, flags = [], []
    for f in sorted(os.listdir(STATE)):
        if not (f.startswith("bot_") and f.endswith(".kraken.json")):
            continue
        st = json.load(open(os.path.join(STATE, f)))
        n = st["account"]
        ticks = [t["equity"] for t in journal.read(n, event="tick") if "equity" in t]
        eq = ticks[-1] if ticks else st["start_cash"]
        peak, dd = st["start_cash"], 0.0
        for v in ticks:
            peak = max(peak, v); dd = min(dd, v / peak - 1)
        sells = [x for x in st.get("fills", []) if x["side"] == "sell"]
        buys = {}
        wins = 0
        for x in st.get("fills", []):
            pass
        # win rate from FIFO per coin
        lots = {}
        n_t = w = 0
        for x in st.get("fills", []):
            if x["side"] == "buy":
                lots.setdefault(x["coin"], []).append([x["units"], x["notional"]])
            else:
                rem, proceeds, units = x["units"], x["proceeds"], x["units"]
                while rem > 1e-12 and lots.get(x["coin"]):
                    l = lots[x["coin"]][0]; take = min(l[0], rem); part = l[1] * take / l[0]
                    n_t += 1; w += (proceeds * take / units) > part
                    l[1] -= part; l[0] -= take; rem -= take
                    if l[0] <= 1e-12: lots[x["coin"]].pop(0)
        days = (time.time() - st["created"]) / 86400
        fam = "bot_grid" if "grid" in n else "bot_dca"
        exp_win, worst_dd = REF[fam]
        flag = ""
        if dd * 100 < worst_dd:
            flag = f"drawdown {dd*100:.1f}% worse than backtest worst {worst_dd}%"
        elif n_t >= 30 and 100 * w / n_t < exp_win - 20:
            flag = f"win rate {100*w/n_t:.0f}% far below backtest {exp_win}%"
        rows.append({"bot": n, "days": round(days, 1), "equity": round(eq, 2), "ret_pct": round((eq / st["start_cash"] - 1) * 100, 2),
                     "max_dd_pct": round(dd * 100, 2), "trips": n_t, "win_pct": round(100 * w / n_t, 1) if n_t else None, "flag": flag})
        if flag:
            flags.append(f"{n}: {flag}")
    return rows, flags


def run(push=True):
    rows, flags = review()
    os.makedirs(OUT, exist_ok=True)
    lines = [f"# Community-bot review {time.strftime('%Y-%m-%d')}", "", "| bot | days | return | max DD | trips | win% | flag |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['bot']} | {r['days']} | {r['ret_pct']:+.2f}% | {r['max_dd_pct']}% | {r['trips']} | {r['win_pct'] if r['win_pct'] is not None else '-'} | {r['flag']} |")
    text = "\n".join(lines)
    open(os.path.join(OUT, "latest.md"), "w").write(text + "\n")
    if push:
        msg = f"{len(rows)} community bots. " + ("FLAGS: " + "; ".join(flags[:4]) if flags else "no flags") + "\n" + \
              "\n".join(f"{r['bot'].replace('bot_','')}: {r['ret_pct']:+.1f}% ({r['trips']} trips)" for r in rows[:10])
        alerts.notify("market_coach weekly bot review", msg[:900], priority="low", tags=["bar_chart"])
    print(text)
    return rows, flags


if __name__ == "__main__":
    run(push=False)
