#!/usr/bin/env python3
"""Rolling walk-forward validation: TRAIN -> TEST -> ADVANCE -> repeat, across
the full available history, pooled across the 24-coin basket. Answers "does
the edge persist across different market periods," not just "did it beat hold
on the one split we happened to check."

Usage:
  python3 walkforward_v2.py strategies/smart_swing.json
  python3 walkforward_v2.py strategies/smart_swing.json 365 120
     (365 = train days per window, 120 = test days per window)
"""
import sys
from marketcoach import walkforward_v2 as wf2


def main():
    if len(sys.argv) < 2:
        print(__doc__); return
    path = sys.argv[1]
    train_days = int(sys.argv[2]) if len(sys.argv) > 2 else 365
    test_days = int(sys.argv[3]) if len(sys.argv) > 3 else 120
    wf2.run(path, train_days=train_days, test_days=test_days)


if __name__ == "__main__":
    main()
