#!/usr/bin/env python3
"""Shared-capital portfolio simulator: the 10 champions against ONE cash pool,
with exposure / concentration / cross-strategy risk measured. Read-only research
tool — the isolated $50 accounts are untouched.

  python3 portfolio_sim.py
"""
from marketcoach import portfolio_sim

if __name__ == "__main__":
    portfolio_sim.simulate()
