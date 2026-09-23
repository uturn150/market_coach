#!/usr/bin/env python3
"""Moon funnel: paper sleeve that rotates idle cash into the strongest breakouts.

  python3 moon_portfolio.py open moon 50            # $50, default wide coin list
  python3 moon_portfolio.py open moon 50 SOL XRP DOGE AVAX INJ SUI
  python3 moon_portfolio.py tick moon
  python3 moon_portfolio.py status moon
  python3 moon_portfolio.py log moon                # tail the journal

Run 'tick' on a schedule. It buys only coins passing all moon triggers, caps
positions, and cuts losers with trailing/hard stops. Fake money, fully logged.
"""
import sys
from marketcoach import moon_portfolio as mp, journal

WIDE = ["BTC", "ETH", "SOL", "ADA", "AVAX", "LINK", "LTC", "XRP", "DOT", "DOGE",
        "ATOM", "UNI", "AAVE", "MKR", "LDO", "APT", "ARB", "OP", "INJ", "SUI",
        "NEAR", "FIL", "ICP", "RNDR", "GRT", "AERO", "SEI", "TIA", "JTO", "PYTH"]


def main():
    if len(sys.argv) < 2:
        print(__doc__); return
    cmd = sys.argv[1]
    if cmd == "open":
        acct = sys.argv[2]
        cash = float(sys.argv[3]) if len(sys.argv) > 3 else 50.0
        coins = sys.argv[4:] or WIDE
        mp.open_account(acct, coins, cash=cash)
    elif cmd == "tick":
        mp.tick(sys.argv[2])
    elif cmd == "status":
        mp.status(mp.load(sys.argv[2]))
    elif cmd == "reset":
        mp.reset_breaker(sys.argv[2])
    elif cmd == "log":
        journal.tail(sys.argv[2], n=int(sys.argv[3]) if len(sys.argv) > 3 else 25)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
