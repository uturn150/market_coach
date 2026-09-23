#!/usr/bin/env python3
"""Execute a strategy on YOUR OWN Kraken Pro account. This is the "tiny live"
graduation stage from GRADUATION.md — check `python3 graduation_check.py all`
says READY before this is more than dry-run curiosity.

Setup (only when you're actually ready — read GRADUATION.md first):
  1. Kraken Pro > Settings > API > Create API Key
  2. Permissions: "Query Funds" + "Create & Modify Orders" ONLY.
     Leave "Withdraw Funds" OFF — this code never calls that endpoint anyway,
     but disable it on Kraken's side too (defense in depth).
  3. Save the key/secret:
       paper_state/kraken.keys.json = {"api_key": "...", "api_secret": "..."}
     (or export KRAKEN_API_KEY / KRAKEN_API_SECRET)

Usage:
  python3 kraken_portfolio.py check                       # verify keys / balance
  python3 kraken_portfolio.py open small strategies/smart_swing.json 86400 10
  python3 kraken_portfolio.py tick small
  python3 kraken_portfolio.py status small
  python3 kraken_portfolio.py reset small

Runs DRY-RUN (logs intended orders, places nothing) until keys are present.
Not cron'd automatically — you run it yourself, deliberately, every time,
until you're confident enough to schedule it.
"""
import sys
from marketcoach import kraken_portfolio as kp
from marketcoach.broker_kraken import Kraken, PAIR_MAP

BASKET = list(k[:-4] for k in PAIR_MAP)  # every coin we have a Kraken mapping for


def main():
    if len(sys.argv) < 2:
        print(__doc__); return
    cmd = sys.argv[1]
    if cmd == "check":
        b = Kraken()
        if not b.live:
            print("No keys found -> DRY-RUN mode. Add paper_state/kraken.keys.json to go live.")
            return
        try:
            bal = b.balance()
            print(f"Connected to Kraken. Balance: {bal}")
        except Exception as e:
            print(f"Connection failed: {e}")
    elif cmd == "open":
        acct, strat = sys.argv[2], sys.argv[3]
        gran = int(sys.argv[4]) if len(sys.argv) > 4 else 86400
        cash = float(sys.argv[5]) if len(sys.argv) > 5 else 10.0
        coins = sys.argv[6:] or BASKET
        kp.open_account(acct, strat, coins, granularity=gran, cash=cash)
    elif cmd == "tick":
        kp.tick(sys.argv[2])
    elif cmd == "status":
        kp.status(sys.argv[2])
    elif cmd == "reset":
        kp.reset_breaker(sys.argv[2])
    elif cmd == "revalidate":
        sub = sys.argv[2]
        if sub == "clear":
            kp.clear_revalidation(sys.argv[3])
        else:
            reason = " ".join(sys.argv[3:]) or "failed re-validation at current fee tier"
            kp.mark_needs_revalidation(sub, reason)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
