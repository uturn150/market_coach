#!/usr/bin/env python3
"""Paper-trade a multi-coin portfolio on fake money (diversified databorn stage).

  python3 paper_portfolio.py open smart strategies/smart_swing.json 86400 50
  python3 paper_portfolio.py open smart strategies/smart_swing.json 86400 50 BTC ETH SOL
  python3 paper_portfolio.py tick smart
  python3 paper_portfolio.py status smart

Default basket is the liquid 12. Run 'tick' on a schedule; it trades only when a
bar closes and the strategy's rules (or a trailing stop) fire.
"""
import sys
from marketcoach import paper_portfolio as pp

BASKET = ["BTC", "ETH", "SOL", "ADA", "AVAX", "LINK", "LTC", "XRP", "DOT", "DOGE", "ATOM", "UNI"]


def main():
    if len(sys.argv) < 2:
        print(__doc__); return
    cmd = sys.argv[1]
    if cmd == "open":
        acct, strat = sys.argv[2], sys.argv[3]
        gran = int(sys.argv[4]) if len(sys.argv) > 4 else 86400
        cash = float(sys.argv[5]) if len(sys.argv) > 5 else 50.0
        coins = sys.argv[6:] or BASKET
        pp.open_account(acct, strat, coins, granularity=gran, cash=cash)
    elif cmd == "tick":
        pp.tick(sys.argv[2])
    elif cmd == "status":
        pp.status(sys.argv[2])
    elif cmd == "reset":
        pp.reset_breaker(sys.argv[2])
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
