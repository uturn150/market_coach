#!/usr/bin/env python3
"""The honesty gate. Splits history into an in-sample (train) and out-of-sample
(test) segment and reports both. The ONLY number that earns real money is the
out-of-sample excess-vs-hold. In-sample looking good means nothing — that's just
the strategy memorizing the past.

This is marketcoach's databorn/duels gate: a strategy graduates toward paper
trading only when its out-of-sample excess is positive after costs.

Usage:
  python3 walkforward.py strategies/sma_cross.json            # BTC-USD daily
  python3 walkforward.py strategies/sma_cross.json ETH-USD 0.7
     (0.7 = fraction of history used for in-sample)
"""
import sys
from marketcoach import data, engine, metrics
from marketcoach.strategy import Strategy


def segment(strat, bars, cash):
    res = engine.run(strat, bars, cash=cash)
    return metrics.summarize(res)


def main():
    if len(sys.argv) < 2:
        print(__doc__); return
    path = sys.argv[1]
    source = sys.argv[2] if len(sys.argv) > 2 else "BTC-USD"
    split = float(sys.argv[3]) if len(sys.argv) > 3 else 0.6
    cash = 50.0

    bars = data.get_bars(source)
    cut = int(len(bars) * split)
    in_bars, out_bars = bars[:cut], bars[cut:]

    strat = Strategy.load(path)
    print(f"\n{strat.name}   [{source}]   split {split:.0%} in / {1-split:.0%} out")
    print("-" * 72)

    strat._series_cache = {}
    r_in = segment(Strategy.load(path), in_bars, cash)
    print(f"IN-SAMPLE  ({len(in_bars)} bars):  {metrics.fmt(r_in)}")

    r_out = segment(Strategy.load(path), out_bars, cash)
    print(f"\nOUT-SAMPLE ({len(out_bars)} bars):  {metrics.fmt(r_out)}")

    print("-" * 72)
    if r_out.low_confidence:
        print(f"LOW CONFIDENCE: only {r_out.n_round_trips} out-of-sample round trips "
              f"(<{metrics.MIN_TRADES_FOR_CONFIDENCE}). Any verdict below is mostly noise "
              f"— treat it as a hint, not proof.")
    if r_out.excess > 0:
        print(f"GATE PASS: out-of-sample excess {r_out.excess*100:+.1f}% after costs. "
              f"Candidate for paper trading.\n")
    else:
        print(f"GATE FAIL: out-of-sample excess {r_out.excess*100:+.1f}%. No edge over "
              f"holding. Do NOT risk money on this.\n")


if __name__ == "__main__":
    main()
