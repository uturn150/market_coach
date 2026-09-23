"""Fill-quality report for the $50 slow trader (slow50_v1). PAPER ONLY: reads Kraken's PUBLIC order book (Depth) and re-uses
paper_exec's conservative book-walk. No orders are placed and no private endpoint is called (fee tier is the cached TradeVolume value).

For each coin the bot trades and each order size it could use ($7 = Kraken minimum here, $17 = one third of the account, $50 = all in):
  half-spread, slippage, fee and total round-trip cost as % of the money at risk, and the price move needed just to break even.
Compared with the assumption baked into the backtests (taker 80bps + spread 5 + slippage 3 = 88bps per side).
Also lists any actual slow50 fills once they exist.  python3 -m marketcoach.slow50_fill_report"""
import json, os, time
from . import paper_exec as pe
from .broker_kraken import Kraken

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "logs", "slow50_fill_report.json")
ASSUMED_SIDE_BPS = 80 + 5 + 3
SIZES = (7.0, 17.0, 50.0)


def _state():
    return json.load(open(os.path.join(BASE, "paper_state", "slow50_v1.kraken.json")))


def measure():
    st = _state(); broker = Kraken(dry_run=True); coins = list(st["sleeves"])
    fee = pe._fee_rate()
    rows = []
    for c in coins:
        try:
            asks, bids = pe.fetch_book(broker, c)
        except Exception as e:
            rows.append({"coin": c, "error": type(e).__name__}); continue
        mid = (asks[0][0] + bids[0][0]) / 2
        row = {"coin": c, "mid": mid, "half_spread_bps": round((asks[0][0] - bids[0][0]) / 2 / mid * 1e4, 2), "sizes": []}
        for n in SIZES:
            b = pe.simulate_market_buy(broker, c, n, signal_price=mid)
            if not b.get("filled_units"):
                row["sizes"].append({"notional": n, "error": b.get("reason", "no fill")}); continue
            s = pe.simulate_market_sell(broker, c, b["filled_units"], signal_price=mid)
            spent = b["total_cost_usd"]; got = s.get("net_proceeds_usd", 0.0)
            rt = (spent - got) / spent
            row["sizes"].append({"notional": n, "buy_filled_pct": round(b["filled_fraction"] * 100, 1), "sell_filled_pct": round(s["filled_fraction"] * 100, 1),
                                 "buy_slip_bps": round(b["slippage_cost_usd"] / spent * 1e4, 2), "sell_slip_bps": round(s["slippage_cost_usd"] / spent * 1e4, 2),
                                 "fee_usd": round(b["fee_usd"] + s["fee_usd"], 4), "round_trip_cost_pct": round(rt * 100, 3),
                                 "breakeven_move_pct": round(rt * 100, 3)})
        rows.append(row)
    all_rt = [z["round_trip_cost_pct"] for r in rows for z in r.get("sizes", []) if "round_trip_cost_pct" in z]
    st_fills = st.get("fills", [])
    rep = {"t": int(time.time()), "iso": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "fee_taker_pct": round(fee * 100, 3),
           "assumed_round_trip_pct": ASSUMED_SIDE_BPS * 2 / 100, "measured_round_trip_pct_range": [min(all_rt), max(all_rt)] if all_rt else None,
           "maker_round_trip_pct_if_filled": 0.80, "min_order_usd": st.get("min_order"), "coins": rows,
           "slow50_fills": len(st_fills), "slow50_equity_note": "no fills yet: F1 trend entries are rare" if not st_fills else f"{len(st_fills)} fills recorded"}
    rep["verdict"] = _verdict(rep)
    return rep


def _verdict(r):
    rng = r["measured_round_trip_pct_range"]
    if not rng: return "Could not read the order book."
    a = r["assumed_round_trip_pct"]
    msg = f"Measured round-trip cost {rng[0]:.2f}%–{rng[1]:.2f}% vs {a:.2f}% assumed in backtests. "
    msg += "Backtests are CONSERVATIVE for these liquid coins." if rng[1] <= a else "Backtests UNDERSTATE costs on some sizes — review."
    msg += " A trade must gain about that much before it earns anything; a limit (maker) entry+exit would cost ~0.80% if it fills, but may not fill."
    return msg


def run():
    rep = measure()
    hist = []
    try: hist = json.load(open(OUT)).get("history", [])
    except Exception: pass
    hist.append({"t": rep["t"], "range": rep["measured_round_trip_pct_range"]}); hist = hist[-200:]
    json.dump({"latest": rep, "history": hist}, open(OUT, "w"), indent=1)
    return rep


def latest():
    try: return json.load(open(OUT))
    except Exception: return None


if __name__ == "__main__":
    r = run()
    print(r["iso"], r["verdict"])
    for c in r["coins"]:
        print(c["coin"], "half-spread", c.get("half_spread_bps"), "bps", [(z["notional"], z.get("round_trip_cost_pct")) for z in c.get("sizes", [])])
