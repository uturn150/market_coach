#!/usr/bin/env python3
"""Paper-trade a strategy live on fake money (the 'databorn' stage).

  python3 paper.py open btc1 strategies/sma_cross.json BTC-USD 50   # open account
  python3 paper.py tick btc1                                        # process closed bars once
  python3 paper.py status btc1                                      # show equity/position
  python3 paper.py loop btc1 3600                                   # tick every hour, forever

Designed to also be driven by a scheduler (cron / the schedule skill): a periodic
'tick' is all it needs. It only trades when a new bar actually closes.
"""
import sys, time
from marketcoach import paper


def main():
    if len(sys.argv) < 2:
        print(__doc__); return
    cmd = sys.argv[1]

    if cmd == "open":
        _, _, acct, strat, source, cash = (sys.argv + ["50"])[:6]
        paper.open_account(acct, strat, source, cash=float(cash))
    elif cmd == "tick":
        paper.tick(sys.argv[2])
    elif cmd == "status":
        st = paper.load(sys.argv[2])
        from marketcoach import data
        last = data.fetch_recent(st["source"], st["granularity"])[-1].close
        paper._print_status(st, last)
    elif cmd == "loop":
        acct = sys.argv[2]
        every = int(sys.argv[3]) if len(sys.argv) > 3 else 3600
        print(f"Looping '{acct}' every {every}s (Ctrl-C to stop)")
        while True:
            paper.tick(acct)
            time.sleep(every)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
