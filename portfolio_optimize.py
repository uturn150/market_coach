#!/usr/bin/env python3
"""Tune a strategy template the way it's ACTUALLY deployed: pooled across the
basket, with the market-regime filter on. Closes the gap where optimize.py only
ever tuned on one coin at a time.

Usage:
  python3 portfolio_optimize.py strategies/multi_signal.tpl.json
  python3 portfolio_optimize.py strategies/multi_signal.tpl.json 0.6 no-regime
  python3 portfolio_optimize.py strategies/smart_swing.tpl.json 0.7
  python3 portfolio_optimize.py strategies/medium_swing.tpl.json 0.6 21600
     (3rd positional = granularity in seconds; defaults to 86400/daily if
     omitted — this used to silently default even when you meant otherwise,
     so always pass it explicitly for a non-daily test.)
"""
import json, sys
from marketcoach import portfolio_optimize as popt


def main():
    if len(sys.argv) < 2:
        print(__doc__); return
    template = json.load(open(sys.argv[1]))
    split = float(sys.argv[2]) if len(sys.argv) > 2 else 0.6
    use_regime = "no-regime" not in sys.argv
    gran_args = [a for a in sys.argv[3:] if a.isdigit()]
    granularity = int(gran_args[0]) if gran_args else 86400

    best, ranked, frac = popt.optimize(template, split=split, use_regime=use_regime,
                                       granularity=granularity)
    if best is None:
        print("\nNothing to save.")
        return
    spec = best[2]
    tag = "regime" if use_regime else "noregime"
    spec["name"] = f"{template.get('name','opt')} [portfolio+{tag}] {best[1]}"
    out_path = sys.argv[1].replace(".tpl.json", "") + f".portfolio_optimized.json"
    json.dump(spec, open(out_path, "w"), indent=2)
    print(f"\nSaved winner -> {out_path}")


if __name__ == "__main__":
    main()
