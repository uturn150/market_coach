"""Market making at different fee tiers (Hummingbot's pure-market-making / Avellaneda idea, spot long-only): quote a bid below and an ask above the
last close every R minutes; a quote fills only if the next minute trades THROUGH it; inventory capped, no shorting; maker fee both sides.
The question: at what maker fee (and spread) does it pay?   1-minute data, ~10 months, 6 coins.   python3 mm_lab.py"""
import glob, itertools, json, os
from marketcoach import minute_data as md
from refports_benchmark2 import minute_bars

BASE = os.path.dirname(os.path.abspath(__file__))
FEES = {"yours 0.40%": 0.0040, "Pro 0.25%": 0.0025, "0.16%": 0.0016, "0.10%": 0.0010, "0.05%": 0.0005, "0.02%": 0.0002, "0% (VIP)": 0.0}


def simulate(bars, spread, refresh, fee, cap=0.5, through=0.0001, order_frac=0.1):
    cash, inv, start = 1000.0, 0.0, 1000.0
    n = len(bars); bid = ask = None; fills = 0
    for i in range(1, n):
        b = bars[i]
        if bid is not None:
            if b.low <= bid * (1 - through) and cash > 0:                       # bid filled
                qty = min(order_frac * start / bid, cash / (bid * (1 + fee)))
                if inv * bid < cap * start:
                    cash -= qty * bid * (1 + fee); inv += qty; fills += 1
            if b.high >= ask * (1 + through) and inv > 0:                        # ask filled
                qty = min(order_frac * start / ask, inv)
                cash += qty * ask * (1 - fee); inv -= qty; fills += 1
        if i % refresh == 0:
            m = b.close
            bid, ask = m * (1 - spread), m * (1 + spread)
        # quotes persist until the next refresh
    end = cash + inv * bars[-1].close
    return (end / start - 1) * 100, fills, inv * bars[-1].close / start * 100


def main():
    coins = [p for p in md.universe() if len(glob.glob(os.path.join(md.DIR, p, "*.bin.z"))) >= 3][:6]
    data = {p: minute_bars(p, 1) for p in coins}
    n = min(len(b) for b in data.values()); data = {p: b[-n:] for p, b in data.items()}
    days = n / 1440
    hold = sum(b[-1].close / b[0].close - 1 for b in data.values()) / len(data) * 100
    print(f"{len(data)} coins x {days:.0f} days of 1-minute data; coins held {hold:+.1f}% on average\n")
    print(f"{'spread refresh':16s}" + "".join(f"{k:>14s}" for k in FEES) + "   (avg P&L % of capital over the whole period, fills/day)")
    res = {}
    for spread, refresh in itertools.product((0.001, 0.002, 0.004, 0.008), (1, 5)):
        row = []
        for name, fee in FEES.items():
            rs = [simulate(b, spread, refresh, fee) for b in data.values()]
            row.append((sum(r[0] for r in rs) / len(rs), sum(r[1] for r in rs) / len(rs) / days))
        res[f"{spread*100:g}% / {refresh}m"] = row
        print(f"{spread*100:>4.1f}% / {refresh}m      " + "".join(f"{a:>+8.1f}%({f:3.0f})" for a, f in row), flush=True)
    json.dump(res, open(os.path.join(BASE, "logs", "mm_lab.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
