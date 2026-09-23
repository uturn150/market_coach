"""Bar watcher: shrinks the gap between "a new bar actually closed in the real
market" and "a bot notices and acts on it" from up to ~59 minutes (waiting for
its one fixed cron minute each hour) down to under ~1 minute.

Does NOT change what a strategy decides or how often it decides — that's still
governed by the strategy's own granularity (a daily strategy still only fires
on a genuinely new daily close, an hourly one on a genuinely new hourly close).
This only removes the WAITING between "the bar closed" and "the bot checked."
No new trades, no new fee exposure beyond what was already validated — see
project memory on why reacting to every price tick (not just closed bars) was
tested and rejected.

Cheap by design: checks ONE reference coin (BTC-USD) per granularity per run
(2 lightweight calls/minute) and only triggers the full per-account sweep (each
of which does its own real fetch/trade work) on the minute a bar genuinely
rolled over — not every minute regardless.
"""
import json, os, time
from . import kraken_portfolio as kp

STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "paper_state")
WATCH_FILE = os.path.join(STATE_DIR, "bar_watch.json")
LOCK_FILE = os.path.join(STATE_DIR, "bar_watch.lock")
LOCK_STALE_SEC = 180  # a previous run stuck > 3min is treated as dead, not "still running"

DAILY_ACCOUNTS = ["realpaper", "krakenmulti", "krakenfast", "krakenslow",
                  "krakenrsi", "krakenmacd", "krakenbbreak", "krakenturtle",
                  "krakencci", "krakenkeltner", "krakenichimoku",
                  "krakendemo_bbrev", "krakendemo_stoch", "krakendemo_willr"]

HOURLY_ACCOUNTS = ["krakendemo", "arena_smart_swing", "arena_multi_signal",
                   "arena_fast_swing", "arena_slow_swing", "arena_rsi_meanrev",
                   "arena_macd_cross", "arena_bollinger_meanrev",
                   "arena_bollinger_breakout", "arena_stochastic_cross",
                   "arena_turtle_donchian", "arena_williams_r",
                   "arena_cci_momentum", "arena_keltner_breakout",
                   "arena_ichimoku_cloud"]
# krakenmoon intentionally excluded: separate funnel module/tick, own cron.


def _four_hour_accounts():
    import glob
    return [os.path.basename(f)[:-len(".kraken.json")]
            for f in sorted(glob.glob(os.path.join(STATE_DIR, "c4h_*.kraken.json")))]


def _latest_closed_ts(granularity):
    """The most recent bar boundary that has fully closed, by wall-clock time —
    matches how every account's own tick() already decides 'closed' (b.ts +
    granularity <= now). Pure time math, no API call (Kraken or otherwise)
    needed just to detect a rollover — one less thing to depend on."""
    now = int(time.time())
    return (now // granularity) * granularity - granularity


def _sweep(accounts, verbose):
    fired = 0
    for name in accounts:
        try:
            kp.tick(name, verbose=False)
            fired += 1
        except Exception as e:
            if verbose:
                print(f"  [{name}] sweep error (skipped): {type(e).__name__}: {e}")
    return fired


def run(verbose=True):
    # Skip if a previous run is still going (or crashed mid-run and left a
    # stale lock) — never pile up overlapping sweeps.
    if os.path.exists(LOCK_FILE):
        age = time.time() - os.path.getmtime(LOCK_FILE)
        if age < LOCK_STALE_SEC:
            if verbose:
                print(f"skip: previous run still active ({age:.0f}s old lock)")
            return
    open(LOCK_FILE, "w").close()
    try:
        seen = {}
        if os.path.exists(WATCH_FILE):
            try:
                seen = json.load(open(WATCH_FILE))
            except Exception:
                seen = {}

        daily_ts = _latest_closed_ts(86400)
        hourly_ts = _latest_closed_ts(3600)
        fourh_ts = _latest_closed_ts(14400)

        if daily_ts is not None and daily_ts != seen.get("daily"):
            if verbose:
                print(f"new daily bar detected (ts={daily_ts}) -> sweeping {len(DAILY_ACCOUNTS)} daily accounts")
            n = _sweep(DAILY_ACCOUNTS, verbose)
            seen["daily"] = daily_ts
            if verbose:
                print(f"  swept {n}/{len(DAILY_ACCOUNTS)} accounts")

        if hourly_ts is not None and hourly_ts != seen.get("hourly"):
            if verbose:
                print(f"new hourly bar detected (ts={hourly_ts}) -> sweeping {len(HOURLY_ACCOUNTS)} hourly accounts")
            n = _sweep(HOURLY_ACCOUNTS, verbose)
            seen["hourly"] = hourly_ts
            if verbose:
                print(f"  swept {n}/{len(HOURLY_ACCOUNTS)} accounts")

        fh = _four_hour_accounts()
        if fh and fourh_ts != seen.get("fourh"):
            if verbose:
                print(f"new 4h bar detected (ts={fourh_ts}) -> sweeping {len(fh)} 4h challenger accounts")
            _sweep(fh, verbose)
        seen["fourh"] = fourh_ts

        json.dump(seen, open(WATCH_FILE, "w"))
    finally:
        try:
            os.remove(LOCK_FILE)
        except OSError:
            pass
