#!/usr/bin/env python3
"""Paper-trade a multi-coin portfolio through Alpaca's PAPER account.

Setup (one time):
  1. Sign up free at https://alpaca.markets  (Paper account, no funding needed)
  2. In the dashboard, switch to 'Paper' and generate API keys.
  3. Save them:  paper_state/alpaca.keys.json  =
       {"key_id": "PK...", "secret_key": "..."}
     (or export ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY)

Usage:
  python3 alpaca_portfolio.py check                      # verify keys / connection
  python3 alpaca_portfolio.py open smartx strategies/smart_swing.json 86400 50
  python3 alpaca_portfolio.py tick smartx
  python3 alpaca_portfolio.py status smartx

Runs DRY-RUN (prints intended orders) until keys are present, then places real
PAPER orders. It never touches the live-money endpoint.
"""
import sys
from marketcoach import alpaca_portfolio as ap
from marketcoach.broker_alpaca import Alpaca

BASKET = ["BTC", "ETH", "SOL", "AVAX", "LINK", "LTC", "DOGE", "DOT", "UNI"]  # Alpaca-listed


def main():
    if len(sys.argv) < 2:
        print(__doc__); return
    cmd = sys.argv[1]
    if cmd == "check":
        b = Alpaca()
        if not b.live:
            print("No keys found -> DRY-RUN mode. Add paper_state/alpaca.keys.json to go live (paper).")
            return
        acct = b.account()
        print(f"Connected. Alpaca paper account status={acct.get('status')} "
              f"equity=${float(acct.get('equity',0)):,.2f} cash=${float(acct.get('cash',0)):,.2f}")
        tradable = b.tradable_crypto()
        print(f"{len(tradable)} tradable crypto symbols. Basket check:")
        for sym in BASKET:
            s = f"{sym}/USD"
            print(f"  {s:10} {'OK' if s in tradable else 'NOT tradable on Alpaca'}")
    elif cmd == "open":
        acct, strat = sys.argv[2], sys.argv[3]
        gran = int(sys.argv[4]) if len(sys.argv) > 4 else 86400
        cash = float(sys.argv[5]) if len(sys.argv) > 5 else 50.0
        coins = sys.argv[6:] or BASKET
        ap.open_account(acct, strat, coins, granularity=gran, cash=cash)
    elif cmd == "tick":
        ap.tick(sys.argv[2])
    elif cmd == "status":
        ap.status(sys.argv[2])
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
