#!/usr/bin/env python3
"""Evidence-based graduation report (see marketcoach/graduation.py and
GRADUATION.md). REPORT ONLY: it can never enable real trading — the strongest
verdict it can produce is ELIGIBLE_FOR_HUMAN_REVIEW.

  python3 graduation_report.py                  # every champion + 4h/learner challenger
  python3 graduation_report.py realpaper c4h_multi
"""
import sys
from marketcoach import graduation

if __name__ == "__main__":
    graduation.report(only=set(sys.argv[1:]) or None)
