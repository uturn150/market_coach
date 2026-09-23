#!/usr/bin/env python3
"""Search a strategy template for good parameters, honestly (in-sample select,
out-of-sample judge), and save the winner as a runnable strategy.

Usage:
  python3 optimize.py strategies/sma_cross.tpl.json                 # BTC-USD
  python3 optimize.py strategies/sma_cross.tpl.json ETH-USD 0.6     # split 60/40
  python3 optimize.py strategies/sma_cross.tpl.json BTC-USD 0.6 sharpe
"""
import json, sys
from marketcoach import data, optimize as opt


def main():
    if len(sys.argv) < 2:
        print(__doc__); return
    template = json.load(open(sys.argv[1]))
    source = sys.argv[2] if len(sys.argv) > 2 else "BTC-USD"
    split = float(sys.argv[3]) if len(sys.argv) > 3 else 0.6
    objective = sys.argv[4] if len(sys.argv) > 4 else "excess"

    bars = data.get_bars(source)
    best, ranked, frac = opt.optimize(template, bars, split=split, objective=objective)

    if best is None:
        print("\nNothing to save — no candidate cleared the fee gate.")
        return
    winner_spec = best[2]
    winner_spec["name"] = f"{template.get('name','opt')} [{source}] {best[1]}"
    out_path = sys.argv[1].replace(".tpl.json", "") + f".optimized.{source}.json"
    with open(out_path, "w") as f:
        json.dump(winner_spec, f, indent=2)
    print(f"\nSaved winner -> {out_path}")
    print(f"Re-check it: python3 walkforward.py {out_path} {source} {split}")


if __name__ == "__main__":
    main()
