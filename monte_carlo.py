#!/usr/bin/env python3
"""Monte Carlo robustness testing: bootstrap-resample the strategy's real
historical trades (thousands of simulations) plus a handful of explicit
harsher-cost/perturbed-parameter re-simulations. Answers "does this survive
reality being a bit worse than the backtest," not "what's the best case."

Usage:
  python3 monte_carlo.py strategies/smart_swing.json
  python3 monte_carlo.py strategies/smart_swing.json 5000   (n_sims)
"""
import sys
from marketcoach import monte_carlo as mc


def main():
    if len(sys.argv) < 2:
        print(__doc__); return
    path = sys.argv[1]
    n_sims = int(sys.argv[2]) if len(sys.argv) > 2 else 2000
    mc.run(path, n_sims=n_sims)


if __name__ == "__main__":
    main()
