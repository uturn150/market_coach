"""marketcoach — a trigger/effects backtest engine for markets, sibling to deckcoach.

sim-first discipline: nothing touches real money until it beats buy-and-hold, after
costs, on out-of-sample data. Backtest (bench) -> paper (databorn) -> tiny live (duels).
"""
from . import data, indicators, strategy, engine, metrics  # noqa: F401
