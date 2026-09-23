#!/usr/bin/env python3
"""Autonomous learning loop (see marketcoach/learner.py). Default is a DRY RUN
that reports what it WOULD do; pass --act to let it demote failing champions
and start challenger paper accounts. Paper-only either way.

  python3 learner.py          # dry run
  python3 learner.py --act    # cron mode
"""
import sys
from marketcoach import learner

if __name__ == "__main__":
    learner.run(act="--act" in sys.argv)
