#!/usr/bin/env python3
"""Rank the running paper accounts by actual performance — return, drawdown,
trade count — using each account's own hourly tick history. Refuses to declare
a fake "winner" when there isn't enough real activity to mean anything yet.

Usage: python3 leaderboard.py
"""
from marketcoach import leaderboard as lb


def main():
    rows = lb.build()
    if not rows:
        print("No accounts found.")
        return
    print("\n" + lb.fmt(rows) + "\n")
    print(lb.verdict(rows) + "\n")


if __name__ == "__main__":
    main()
