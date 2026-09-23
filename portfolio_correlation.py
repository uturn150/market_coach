#!/usr/bin/env python3
"""Cross-strategy correlation + coin-concentration + combined-portfolio
analysis across the 10 currently-validated real strategies. Read-only —
touches no live account, changes no strategy file.

Usage:
  python3 portfolio_correlation.py
"""
from marketcoach import portfolio_correlation as pc


if __name__ == "__main__":
    pc.analyze()
