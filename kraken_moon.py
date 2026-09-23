#!/usr/bin/env python3
"""Kraken-connected moon funnel — the offense sleeve, priced off your real
Kraken account, orders simulated (paper=True baked into the account itself,
same safety model as kraken_portfolio.py).

Usage:
  python3 kraken_moon.py open krakenmoon 50
  python3 kraken_moon.py tick krakenmoon
  python3 kraken_moon.py status krakenmoon
  python3 kraken_moon.py reset krakenmoon
"""
import sys
from marketcoach import kraken_moon as km
from marketcoach.broker_kraken import PAIR_MAP

WIDE = [c[:-4] for c in PAIR_MAP]  # every coin with a real Kraken pair mapping


def main():
    if len(sys.argv) < 2:
        print(__doc__); return
    cmd = sys.argv[1]
    if cmd == "open":
        acct = sys.argv[2]
        cash = float(sys.argv[3]) if len(sys.argv) > 3 else 50.0
        coins = sys.argv[4:] or WIDE
        km.open_account(acct, coins, cash=cash)
    elif cmd == "tick":
        km.tick(sys.argv[2])
    elif cmd == "status":
        km._status(km.load(sys.argv[2]))
    elif cmd == "reset":
        km.reset_breaker(sys.argv[2])
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
