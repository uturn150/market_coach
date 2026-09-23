#!/usr/bin/env python3
"""The missing feedback loop: is a live paper account actually behaving the way
its validated backtest predicted? Re-runs the SAME strategy (with the SAME
regime filter, if used) over the exact historical window the account has been
live for, and compares trade counts coin-by-coin against what actually happened.

This is how you catch strategy decay, a missed/broken cron tick, or a real bug —
early, instead of noticing three weeks later that an account quietly stopped
trading or started trading very differently than expected.

Usage:
  python3 reconcile.py smart
  python3 reconcile.py multi
"""
import os, sys, time
from marketcoach import data, engine, regime, alerts
from marketcoach import paper_portfolio as pp
from marketcoach import kraken_portfolio as kp
from marketcoach.strategy import Strategy


def _load_any(account):
    """Auto-detect which account type this is — a Kraken-connected paper
    account or the original internal-simulator one — and load it with the
    matching loader. Both share the same {strategy_path, granularity, created,
    sleeves, fills, regime_filter, start_cash} shape, so everything downstream
    works unchanged either way."""
    if os.path.exists(kp._path(account)):
        return kp.load(account)
    return pp.load(account)

# IMPORTANT: use the stable, paginated long-history fetch here, NOT fetch_recent().
# fetch_recent() returns "whatever the most recent ~300-bar page is right now",
# which can shift slightly between two separate calls at different moments —
# comparing two independent live snapshots can show a spurious mismatch that
# looks like drift but is really just two different fetches. get_bars() paginates
# a stable, cacheable range, giving a reproducible ground truth to check against.


def reconcile_account(account, verbose=True):
    """Returns mismatches (int). Prints a full report unless verbose=False."""
    _print = print if verbose else (lambda *a, **k: None)
    state = _load_any(account)
    strat_path = state["strategy_path"]
    gran = state["granularity"]
    created = state["created"]
    now = int(time.time())
    days = (now - created) / 86400

    _print(f"\nReconciling '{account}' vs its own backtest replay since "
          f"{time.strftime('%Y-%m-%d', time.gmtime(created))} ({days:.1f} days live)\n")

    allow_buy_ts = None
    if state.get("regime_filter"):
        allow_buy_ts = regime.risk_on_timestamps("BTC-USD", granularity=gran)

    _print(f"{'coin':10} {'live buys':>10} {'backtest buys':>14}  verdict")
    _print("-" * 60)
    mismatches, too_new = 0, 0
    per = state["start_cash"] / len(state["sleeves"])
    for product in state["sleeves"]:
        try:
            bars = data.get_bars(product, granularity=gran, use_cache=False)
        except Exception:
            continue
        if not any(b.ts >= created for b in bars):
            _print(f"{product:10} {'--':>10} {'--':>14}  not enough history yet")
            too_new += 1
            continue
        # Run the backtest over the FULL fetched history (same warmup context the
        # live account has — its indicators aren't cold-started at `created`),
        # then only count fills that happened after the account actually opened.
        # Comparing on truncated bars would give indicators zero warmup and
        # produce a spurious mismatch that looks like decay but isn't.
        res = engine.run(Strategy.load(strat_path), bars, cash=per, allow_buy_ts=allow_buy_ts)
        bt_buys = sum(1 for f in res.fills if f.side == "buy" and f.ts >= created)
        live_buys = sum(1 for f in state["fills"] if f["coin"] == product and f["side"] == "buy")
        if live_buys == bt_buys:
            verdict = "match"
        elif live_buys < bt_buys:
            verdict = "live BEHIND"
            mismatches += 1
        else:
            verdict = "live AHEAD"
            mismatches += 1
        _print(f"{product:10} {live_buys:>10} {bt_buys:>14}  {verdict}")

    _print("-" * 60)
    if mismatches == 0:
        _print("All coins match their backtest replay over this window. Paper trading "
              "is behaving exactly as its validated backtest predicted (note: vol/"
              "correlation sizing can still change how MUCH was bought each time "
              "without changing the trade count — that's expected).")
    else:
        _print(f"{mismatches} coin(s) diverge from the backtest replay. Before assuming "
              "decay, check: a missed/failed cron tick (see paper_state/*.log), the "
              "circuit breaker having halted new buys (see logs/<account>.jsonl for "
              "'circuit_breaker'), or a real bug in the live tick logic. A genuine, "
              "unexplained divergence after ruling those out IS a real signal to "
              "re-examine whether the edge still holds.")
        alerts.notify(f"market_coach: {account} reconciliation mismatch",
                     f"{mismatches} coin(s) diverge from their backtest replay over the "
                     f"account's live window. Could be a missed cron tick, the circuit "
                     f"breaker, or a real bug — worth a look before assuming decay.",
                     priority="high", tags=["mag", "warning"])
    if too_new:
        _print(f"({too_new} coin(s) too new to this account to compare yet.)")
    return mismatches


# The two Kraken sleeve-based accounts — the system is now consolidated onto
# Kraken, so these are the canonical accounts to watch. krakenmoon (a funnel,
# not a sleeve-per-coin strategy) isn't compatible with this tool's
# backtest-replay comparison, same as the original 'moon' account never was.
SLEEVE_ACCOUNTS = ["realpaper", "krakenmulti", "krakenfast", "krakenslow",
                   "krakenrsi", "krakenmacd", "krakenbbreak", "krakenturtle",
                   "krakencci", "krakenkeltner", "krakenichimoku"]

# Arena (hourly, experimental) accounts get their own health-check group since
# they're built on a different granularity and expected to behave differently —
# kept separate so "all" doesn't conflate proven daily bots with the arena.
ARENA_ACCOUNTS = ["arena_smart_swing", "arena_multi_signal", "arena_fast_swing",
                  "arena_slow_swing", "arena_rsi_meanrev", "arena_macd_cross",
                  "arena_bollinger_meanrev", "arena_bollinger_breakout",
                  "arena_stochastic_cross", "arena_turtle_donchian",
                  "arena_williams_r", "arena_cci_momentum",
                  "arena_keltner_breakout", "arena_ichimoku_cloud"]


def main():
    if len(sys.argv) < 2:
        print(__doc__); return
    if sys.argv[1] == "all":
        for account in SLEEVE_ACCOUNTS:
            reconcile_account(account)
            print()
        return
    reconcile_account(sys.argv[1])


if __name__ == "__main__":
    main()
