#!/usr/bin/env python3
"""Superseded. The old check leaned on a calendar rule (60+ days live) and tested
only 4 sampled coins. Graduation is now an evidence chain — see
graduation_report.py / GRADUATION.md. This wrapper just runs the new report so
there is exactly one verdict, never two that can disagree.

  python3 graduation_check.py all
  python3 graduation_check.py realpaper
"""
import sys
from marketcoach import graduation

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "all"]
    print("(graduation_check.py now runs the evidence-based report; days-live is no longer a criterion)")
    graduation.report(only=set(args) or None)
