#!/usr/bin/env python3
"""Live risk monitor: checks OPEN POSITIONS against Kraken's real-time price and
exits on a stop-loss/trailing-stop/take-profit breach immediately, instead of
waiting for the next hourly cron tick. Entries are untouched — this only makes
EXITS on positions you already hold faster. Meant to be cron'd every minute;
cheap when flat (no ticker calls unless something is actually held).

Usage:
  python3 risk_monitor.py          # check every real + arena sleeve account once
  python3 risk_monitor.py <name>   # check just one account
"""
import sys
from marketcoach import risk_monitor as rm


def main():
    if len(sys.argv) > 1:
        n = rm.check_account(sys.argv[1], verbose=True)
    else:
        n = rm.check_all(verbose=True)
    if n:
        print(f"{n} live risk exit(s) fired.")


if __name__ == "__main__":
    main()
