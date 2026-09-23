#!/usr/bin/env python3
"""Backtest a strategy against data and print metrics vs the buy-and-hold benchmark.

Usage:
  python3 backtest.py strategies/sma_cross.json                 # BTC-USD daily (cached)
  python3 backtest.py strategies/sma_cross.json ETH-USD         # other Coinbase product
  python3 backtest.py strategies/sma_cross.json synthetic       # offline, deterministic
  python3 backtest.py strategies/sma_cross.json BTC-USD 50      # starting cash $50
"""
import sys
from marketcoach import data, engine, metrics
from marketcoach.strategy import Strategy


def main():
    if len(sys.argv) < 2:
        print(__doc__); return
    strat = Strategy.load(sys.argv[1])
    source = sys.argv[2] if len(sys.argv) > 2 else "BTC-USD"
    cash = float(sys.argv[3]) if len(sys.argv) > 3 else 50.0
    gran = int(sys.argv[4]) if len(sys.argv) > 4 else 86400

    bars = data.get_bars(source, granularity=gran)
    res = engine.run(strat, bars, cash=cash)
    rep = metrics.summarize(res)

    span = f"{len(bars)} bars"
    print(f"\n{strat.name}   [{source}, {span}, start ${cash:.0f}]")
    print(metrics.fmt(rep))
    print(metrics.fmt_fees(rep, strat))
    gate = metrics.fee_gate(res, strat)
    print(metrics.fmt_gate(gate))
    verdict = "BEATS hold" if rep.excess > 0 else "loses to hold"
    print(f"  -> {verdict} by {abs(rep.excess)*100:.1f}%")
    if not gate.ok:
        print("  -> REJECTED by fee gate: do not deploy this; it burns money on fees.\n")
    else:
        print()


if __name__ == "__main__":
    main()
