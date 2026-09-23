"""One-off: after the first tradable daily bar closes (2026-09-22 00:00 UTC) report what the community-style paper bots did.
Writes logs/first_bot_check.md.  Paper only; reads state files, re-simulates via bot_live.tick (deterministic, no broker calls)."""
import glob, json, os, time
from marketcoach import bot_live as bl
BASE = os.path.dirname(os.path.abspath(__file__))
lines = [f"# First live read of community paper bots — {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}", "",
         "| account | equity | ret | coins held | resting orders | fills since anchor |", "|---|---|---|---|---|---|"]
tot0 = tot1 = 0.0; nf = 0
for f in sorted(glob.glob(os.path.join(BASE, "paper_state", "bot_*.kraken.json"))):
    name = os.path.basename(f).replace(".kraken.json", "")
    try:
        s = bl.tick(name, verbose=False)
    except Exception as e:
        lines.append(f"| {name} | ERROR {e} | | | | |"); continue
    held = sum(1 for v in s["sleeves"].values() if v.get("in_position"))
    eq = sum(v["cash"] for v in s["sleeves"].values()) + sum(v["units"] * v["last_px"] for v in s["sleeves"].values())
    tot0 += s["start_cash"]; tot1 += eq; nf += len(s["fills"])
    lines.append(f"| {name} | ${eq:,.2f} | {(eq/s['start_cash']-1)*100:+.2f}% | {held}/{len(s['coins'])} | {len(s['orders'])} | {len(s['fills'])} |")
lines += ["", f"Total: ${tot1:,.2f} of ${tot0:,.2f} ({(tot1/tot0-1)*100:+.2f}%), {nf} fills.",
          "Caveat: 1-2 days of evidence; only useful to confirm the bots place/fill orders as the backtest assumed."]
open(os.path.join(BASE, "logs", "first_bot_check.md"), "w").write("\n".join(lines) + "\n")
print("\n".join(lines))
