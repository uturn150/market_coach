"""How much money does each community bot need to trade with Kraken's REAL per-coin rules (minimum size, lot, tick)?
Runs every live bot design at several balances with rules ON and reports return, rejected orders and trades.
    python3 real_size_lab.py  -> logs/real_size_lab_50.json"""
import glob, json, os
from marketcoach import bot_live as bl
BASE = os.path.dirname(os.path.abspath(__file__))
BAL = (50,)
out = {}
for f in sorted(glob.glob(os.path.join(BASE, "paper_state", "bot_*.kraken.json"))):
    st = json.load(open(f)); n = st["account"]
    bars = bl._closed_bars(st["coins"])
    st = dict(st); st["anchor_ts"] = 0
    print(f"\n{n} ({st['bot_kind']}, {len(st['coins'])} coins)", flush=True)
    rows = []
    for b in BAL:
        s2 = dict(st, start_cash=float(b)); s2["kraken_rules"] = False
        ideal = bl.simulate(s2, bars); s2["kraken_rules"] = True; s2["order_step"] = 10.0
        r = bl.simulate(s2, bars)
        ri = (ideal.equity[-1] / b - 1) * 100; rr = (r.equity[-1] / b - 1) * 100
        rej = r.stats.get("below_min", 0) + r.stats.get("dust_sell", 0); placed = r.stats.get("placed", 0) + r.stats.get("market_fills", 0)
        rows.append({"balance": b, "ideal_ret": ri, "real_ret": rr, "rejected": rej, "placed": placed, "trips_real": len(r.trips), "trips_ideal": len(ideal.trips)})
        print(f"  ${b:>6,}: ideal {ri:+7.2f}% ({len(ideal.trips):4d} trips) | $10-STEP+RULES {rr:+7.2f}% ({len(r.trips):4d} trips), rejected {rej:5d} of {placed + rej:5d} orders", flush=True)
    out[n] = rows
json.dump(out, open(os.path.join(BASE, "logs", "real_size_lab_50.json"), "w"), indent=1)
