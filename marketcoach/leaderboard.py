"""Leaderboard: rank the running paper accounts by how they're actually doing.
Equity HISTORY comes from each account's own hourly tick log (already recorded
to logs/<account>.jsonl every cron run — no new data collection needed); trade
count and halted/paused status come straight from the account's own state file.
"""
import os, time
from . import journal
from . import kraken_portfolio as kp
from . import kraken_moon as km
from .broker_kraken import Kraken

ACCOUNTS = [
    ("realpaper", "sleeve"), ("krakenmulti", "sleeve"),
    ("krakenfast", "sleeve"), ("krakenmoon", "moon"),
    ("krakenslow", "sleeve"),
    ("krakenrsi", "sleeve"), ("krakenmacd", "sleeve"),
    ("krakenbbreak", "sleeve"), ("krakenturtle", "sleeve"),
    ("krakencci", "sleeve"), ("krakenkeltner", "sleeve"),
    ("krakenichimoku", "sleeve"),
]
# krakendemo / krakendemo_bbrev / krakendemo_stoch / krakendemo_willr are
# deliberately excluded: each one FAILED the fee gate before deployment
# (kept live only so the user can watch frequent trading + fee bleed happen),
# so ranking them would let a lucky short-term pump contaminate "who's
# actually winning" — same reasoning as the original krakendemo exclusion.

# ARENA: all 14 strategies re-deployed on HOURLY bars with maker/limit orders
# (the best fair shot per a real test — see project memory), so the user can
# watch frequent live trading happen. Kept in a SEPARATE leaderboard from
# ACCOUNTS above: a real pooled backtest of all 14 at hourly frequency showed
# only 1/28 taker+maker combinations survives the fee+edge gates at all (and
# only barely), so mixing arena results into the real leaderboard would let
# short-term hourly noise contaminate "who's actually winning" on the
# strategies that HAVE proven they survive costs.
ARENA_ACCOUNTS = [
    ("arena_smart_swing", "sleeve"), ("arena_multi_signal", "sleeve"),
    ("arena_fast_swing", "sleeve"), ("arena_slow_swing", "sleeve"),
    ("arena_rsi_meanrev", "sleeve"), ("arena_macd_cross", "sleeve"),
    ("arena_bollinger_meanrev", "sleeve"), ("arena_bollinger_breakout", "sleeve"),
    ("arena_stochastic_cross", "sleeve"), ("arena_turtle_donchian", "sleeve"),
    ("arena_williams_r", "sleeve"), ("arena_cci_momentum", "sleeve"),
    ("arena_keltner_breakout", "sleeve"), ("arena_ichimoku_cloud", "sleeve"),
]

def challenger_accounts():
    """Auto-discovered: learner-spawned (chal_*) and 4h fleet (c4h_*) paper
    accounts. Kept on their OWN leaderboard, never mixed with champions."""
    import glob
    files = (glob.glob(os.path.join(kp.STATE_DIR, "chal_*.kraken.json")) +
             glob.glob(os.path.join(kp.STATE_DIR, "c4h_*.kraken.json")) +
             glob.glob(os.path.join(kp.STATE_DIR, "bot_*.kraken.json")))
    return [(os.path.basename(f)[:-len(".kraken.json")], "sleeve") for f in sorted(files)]


MIN_TRADES_MEANINGFUL = 5  # below this, a "leader" is just noise — say so


def _load(name, kind):
    return km.load(name) if kind == "moon" else kp.load(name)


# One shared broker for live price lookups: ticker_price() is a public,
# unauthenticated, always-safe call regardless of dry_run — reused across
# every account in a build() call instead of constructing one per account.
_LIVE_BROKER = None


def _live_broker():
    global _LIVE_BROKER
    if _LIVE_BROKER is None:
        _LIVE_BROKER = Kraken(dry_run=True)
    return _LIVE_BROKER


def _live_equity(state, kind):
    """Current equity computed from LIVE ticker prices for anything actually
    held, instead of whatever was true at the last logged hourly tick — so the
    leaderboard reflects the market right now, not up to ~an hour ago. Falls
    back to each position's own last-known price if a live fetch fails (rate
    limit, network hiccup) rather than showing nothing."""
    broker = _live_broker()
    if kind == "moon":
        cash = state.get("cash", state.get("start_cash", 50.0))
        total = cash
        for c, pos in state.get("positions", {}).items():
            px = pos.get("avg_cost", 0.0)
            try:
                live_px = broker.ticker_price_cached(c)
                if live_px:
                    px = live_px
            except Exception:
                pass
            total += pos.get("units", 0.0) * px
        return total
    total = 0.0
    for c, sl in state.get("sleeves", {}).items():
        total += sl.get("cash", 0.0)
        if sl.get("in_position"):
            px = sl.get("entry_px", 0.0)
            try:
                live_px = broker.ticker_price_cached(c)
                if live_px:
                    px = live_px
            except Exception:
                pass
            total += sl.get("units", 0.0) * px
    return total


def build(accounts=None):
    accounts = accounts or ACCOUNTS
    rows = []
    for name, kind in accounts:
        try:
            state = _load(name, kind)
        except FileNotFoundError:
            continue
        ticks = journal.read(name, event="tick")
        start = state.get("start_cash", 50.0)
        historical = [t["equity"] for t in ticks if "equity" in t]
        current = _live_equity(state, kind) if not state.get("halted") else (historical[-1] if historical else start)
        equities = historical + [current]
        peak, max_dd = start, 0.0
        for e in equities:
            peak = max(peak, e)
            max_dd = min(max_dd, (e / peak - 1) if peak else 0.0)
        trades = len(state.get("fills", []))
        days = (time.time() - state.get("created", time.time())) / 86400
        strat_path = state.get("strategy_path", "")
        strategy = os.path.basename(strat_path).replace(".json", "").replace("_", " ") \
            if strat_path else "breakout funnel"
        rows.append({
            "name": name, "strategy": strategy, "equity": round(current, 2),
            "start": start, "ret_pct": round((current / start - 1) * 100, 2),
            "max_dd_pct": round(max_dd * 100, 1), "trades": trades,
            "days_live": round(days, 1), "halted": bool(state.get("halted", False)),
            "paused": state.get("paused_until", 0) > time.time(),
            "needs_revalidation": bool(state.get("needs_revalidation", False)),
            "revalidation_reason": state.get("revalidation_reason"),
            "ticks_logged": len(ticks),
        })
    # Accounts with real trade activity float above still-flat ones regardless
    # of return — a -2% account that's actually done something is more
    # interesting to watch than a 0.00% account that hasn't traded yet, and
    # sorting by return alone would bury the former below the latter.
    rows.sort(key=lambda r: (r["trades"] > 0, r["ret_pct"]), reverse=True)
    return rows


def fmt(rows):
    header = (f"{'rank':4} {'account':12} {'strategy':18} {'equity':>9} {'return':>8} "
              f"{'maxDD':>7} {'trades':>6} {'days':>5}  status")
    lines = [header, "-" * len(header)]
    for i, r in enumerate(rows, 1):
        status = ("HALTED" if r["halted"] else
                  "REVALIDATE" if r["needs_revalidation"] else
                  "paused" if r["paused"] else "active")
        lines.append(f"{i:>3}. {r['name']:12} {r['strategy']:18} ${r['equity']:>7.2f} "
                     f"{r['ret_pct']:>+6.2f}% {r['max_dd_pct']:>+6.1f}% {r['trades']:>6} "
                     f"{r['days_live']:>4.1f}d  {status}")
    return "\n".join(lines)


def verdict(rows):
    if not rows:
        return "No accounts found."
    total_trades = sum(r["trades"] for r in rows)
    n = len(rows)
    flagged = [r["name"] for r in rows if r["needs_revalidation"]]
    flag_note = (f" ({len(flagged)} account(s) failed re-validation and are excluded from "
                f"'leading': {', '.join(flagged)})" if flagged else "")
    # A flagged account never gets to be "leading," even if its stale return
    # looks good — it failed the gate that makes a ranking meaningful at all.
    ranked = [r for r in rows if not r["needs_revalidation"]] or rows
    if total_trades == 0:
        return (f"All accounts are still completely flat (0 trades total) — there is NO "
                f"leader yet, just {n} bots waiting for their first signal. Ranking by "
                f"0.00% vs 0.00% would be meaningless noise, not a result.{flag_note}")
    if total_trades < MIN_TRADES_MEANINGFUL:
        top = ranked[0]
        return (f"{top['name']} is nominally ahead ({top['ret_pct']:+.2f}%), but only "
                f"{total_trades} trade(s) have happened across all {n} accounts combined — "
                f"far too few to call a real winner. Treat this as 'nothing meaningful yet', "
                f"not a result.{flag_note}")
    top = ranked[0]
    return f"Leading: {top['name']} ({top['strategy']}) at {top['ret_pct']:+.2f}%, {top['trades']} trades.{flag_note}"
