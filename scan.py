#!/usr/bin/env python3
"""Run a strategy across MANY coins and rank where it actually survives both gates.

For each coin: fee gate (survives costs?) + walk-forward (beats hold out-of-sample?).
A coin only 'qualifies' if it passes BOTH. This is how you find what a strategy is
really good for instead of staring at BTC alone.

Usage:
  python3 scan.py strategies/swing_btc.json                 # default liquid basket
  python3 scan.py strategies/swing_btc.json 0.6 BTC ETH SOL # custom split + coins
"""
import sys
from marketcoach import data, engine, metrics
from marketcoach.strategy import Strategy

# Liquid, established USD pairs on Coinbase (deep history, tradable elsewhere too).
BASKET = ["BTC", "ETH", "SOL", "ADA", "AVAX", "LINK", "LTC", "XRP", "DOT", "DOGE", "ATOM", "UNI"]


def scan(path, split=0.6, coins=None, cash=50.0):
    coins = coins or BASKET
    rows = []
    for sym in coins:
        product = sym if "-" in sym else f"{sym}-USD"
        try:
            bars = data.get_bars(product)
        except Exception as e:
            print(f"  skip {product}: {type(e).__name__}")
            continue
        if len(bars) < 200:
            print(f"  skip {product}: only {len(bars)} bars")
            continue
        strat = Strategy.load(path)
        full = engine.run(strat, bars, cash=cash)
        gate = metrics.fee_gate(full, strat)
        cut = int(len(bars) * split)
        out = metrics.summarize(engine.run(Strategy.load(path), bars[cut:], cash=cash))
        qualifies = gate.ok and out.excess > 0
        rows.append((product, gate, out, qualifies))

    # rank: qualifiers first, then by out-of-sample excess
    rows.sort(key=lambda r: (r[3], r[2].excess), reverse=True)

    strat = Strategy.load(path)
    print(f"\nScan: {strat.name}")
    print(f"split {split:.0%} in / {1-split:.0%} out   break-even wall "
          f"{strat.breakeven_move*100:.2f}%/trade   [{strat.costs['order_type']}]")
    print("=" * 78)
    print(f"{'coin':9} {'fee gate':10} {'OOS ret':>9} {'OOS vs hold':>12} "
          f"{'trades':>7}  verdict")
    print("-" * 78)
    for product, gate, out, ok in rows:
        fg = "PASS" if gate.ok else "FAIL"
        verdict = "QUALIFIES" if ok else ("no edge" if gate.ok else "fee-burner")
        print(f"{product:9} {fg:10} {out.total_return*100:+8.1f}% "
              f"{out.excess*100:+11.1f}% {out.n_trades:7d}  {verdict}")
    print("-" * 78)
    q = [r for r in rows if r[3]]
    if q:
        print(f"{len(q)} coin(s) pass BOTH gates: " + ", ".join(r[0] for r in q))
        print("These are forward-test candidates — paper-trade before real money.")
    else:
        print("No coin passes both gates with this strategy. That's a real answer: "
              "this strategy has no reliable edge on this basket. Don't deploy it.")
    return rows


def main():
    if len(sys.argv) < 2:
        print(__doc__); return
    args = sys.argv[2:]
    split = 0.6
    if args and _isfloat(args[0]):
        split = float(args[0]); args = args[1:]
    coins = args or None
    scan(sys.argv[1], split=split, coins=coins)


def _isfloat(s):
    try:
        float(s); return True
    except ValueError:
        return False


if __name__ == "__main__":
    main()
