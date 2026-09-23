"""Live risk monitor: checks OPEN POSITIONS against Kraken's real-time ticker
price and exits immediately on a stop-loss/trailing-stop/take-profit breach,
instead of waiting for the next hourly cron tick.

Deliberately narrow scope: this does NOT change when strategies decide to BUY —
entries stay exactly as validated, on hourly/daily bar closes (going faster on
entries is the scalping trap that burned the user's old bot, and we already
proved it loses to fees at hourly). This only tightens the safety net on
positions already held: a real crash gets caught within ~1 minute instead of
up to ~1 hour. No new trades are created by this file that a normal tick
wouldn't eventually have placed anyway — it just times the SAME exit sooner.

Meant to be cron'd every minute. Cheap when flat (a fresh account with nothing
held costs zero ticker calls) and only spends Kraken API calls on coins a
sleeve is actually holding.
"""
import time
from . import journal, alerts, paper_exec, kraken_portfolio as kp, kraken_moon as km
from .strategy import Strategy
from .broker_kraken import Kraken
from .leaderboard import ACCOUNTS, ARENA_ACCOUNTS


def check_account(account, verbose=False):
    """Returns the number of risk exits fired for this account."""
    try:
        state = kp.load(account)
    except FileNotFoundError:
        return 0
    if state.get("halted"):
        return 0

    strat = Strategy.load(state["strategy_path"])
    stop = strat.risk.get("stop_loss_pct")
    take = strat.risk.get("take_profit_pct")
    trail = strat.risk.get("trailing_stop_pct")
    if stop is None and take is None and trail is None:
        return 0  # nothing this strategy defines to check early

    rate = strat.side_rate
    broker = Kraken(dry_run=state.get("paper", True))
    fired = 0

    for product, sl in state["sleeves"].items():
        if not sl.get("in_position"):
            continue
        try:
            live_px = broker.ticker_price(product)
        except Exception:
            continue
        if not live_px:
            continue

        sl["peak"] = max(sl.get("peak", 0.0), live_px) if sl.get("peak") else live_px
        chg = live_px / sl["entry_px"] - 1 if sl["entry_px"] else 0.0
        hit_trail = trail is not None and sl["peak"] > 0 and live_px <= sl["peak"] * (1 - abs(trail))
        hit_stop = stop is not None and chg <= -abs(stop)
        hit_take = take is not None and chg >= abs(take)
        if not (hit_trail or hit_stop or hit_take):
            continue

        reason = "trailing_stop" if hit_trail else ("stop_loss" if hit_stop else "take_profit")
        sim = paper_exec.simulate_market_sell(broker, product, sl["units"], signal_price=live_px)
        sold = sim.get("filled_units", 0.0)
        if sold <= 0:
            continue  # nothing obtainable this minute; the next minute's check retries
        broker.market_order(product, "sell", sold)
        proceeds = sim["net_proceeds_usd"]
        pnl_pct = (proceeds / sold) / sl["entry_px"] - 1 if sl["entry_px"] else 0
        sl["cash"] += proceeds
        ex = paper_exec.exec_fields(sim)
        state["fills"].append({"ts": int(time.time()), "coin": product, "side": "sell",
                               "price": sim["sim_fill"], "units": sold, "proceeds": proceeds,
                               "source": "risk_monitor", "reason": reason, "exec": ex})
        journal.log(account, "kraken_fill", coin=product, side="sell", price=round(sim["sim_fill"], 8),
                    units=round(sold, 8), proceeds=round(proceeds, 4),
                    pnl_pct=round(pnl_pct * 100, 2), live=broker.live,
                    source="risk_monitor", reason=reason, **ex)
        alerts.notify(
            f"market_coach: {account} LIVE RISK EXIT {product}" + (" (REAL)" if broker.live else " (paper)"),
            f"{reason} hit at ~${sim['sim_fill']:,.4f} ({pnl_pct*100:+.1f}%) — caught live, "
            f"not waiting for the next hourly tick", priority="high", tags=["rotating_light"])
        sl["units"] -= sold
        if sl["units"] <= 1e-12:
            sl["in_position"], sl["entry_px"], sl["peak"], sl["units"] = False, 0.0, 0.0, 0.0
            sl["pending"] = None  # a queued hourly-tick sell would now be redundant
        fired += 1
        if verbose:
            print(f"[{account}] LIVE EXIT {product}: {reason} at ${sim['sim_fill']:,.4f} ({pnl_pct*100:+.1f}%)")

    if fired:
        kp._save(state)
    return fired


def check_moon_account(account, verbose=False):
    """Same idea as check_account, but for krakenmoon's funnel: profit ladder
    + trailing stop + hard stop, all checked against a LIVE ticker price
    instead of the last closed bar. This is the bot specifically hunting big,
    fast pumps (a coin running a few 100%) — it's the one that most needs its
    exits to not lag up to an hour behind the real price."""
    try:
        state = km.load(account)
    except FileNotFoundError:
        return 0
    if state.get("halted") or not state.get("positions"):
        return 0

    rate = km._rate(state)
    trail, stop = state["trailing_stop_pct"], state["stop_loss_pct"]
    ladder_levels = state.get("profit_ladder_levels", [])
    ladder_sells = state.get("profit_ladder_sells", [])
    broker = Kraken(dry_run=state.get("paper", True))
    fired = 0

    for product in list(state["positions"]):
        pos = state["positions"][product]
        try:
            px = broker.ticker_price(product)
        except Exception:
            continue
        if not px:
            continue

        pos["peak"] = max(pos.get("peak", 0.0), px) or px
        pos.setdefault("original_units", pos["units"])
        pos.setdefault("ladder_hit", [])
        chg = px / pos["avg_cost"] - 1 if pos["avg_cost"] else 0.0

        for i, level in enumerate(ladder_levels):
            if i in pos["ladder_hit"] or chg < level:
                continue
            frac = ladder_sells[i] if i < len(ladder_sells) else 0.0
            sell_units = min(pos["original_units"] * frac, pos["units"])
            pos["ladder_hit"].append(i)
            if sell_units <= 0:
                continue
            broker.market_order(product, "sell", sell_units)
            proceeds = sell_units * px * (1 - rate)
            pnl = km._record_realized(state, product, sell_units, pos["avg_cost"], proceeds)
            state["cash"] += proceeds
            pos["units"] -= sell_units
            state["fills"].append({"ts": int(time.time()), "coin": product, "side": "sell",
                                   "price": px, "units": sell_units,
                                   "reason": f"profit_ladder_{int(level*100)}pct",
                                   "source": "risk_monitor"})
            journal.log(account, "ladder_sell", coin=product, level_pct=round(level * 100, 0),
                        units=round(sell_units, 8), proceeds=round(proceeds, 2),
                        pnl=round(pnl, 2), live=broker.live, source="risk_monitor")
            alerts.notify(
                f"market_coach: {account} LIVE ladder take-profit {product}" +
                (" (REAL)" if broker.live else " (paper)"),
                f"+{level*100:.0f}% milestone caught live: sold {frac:.0%} of original stake, "
                f"{pos['units']:.6f} still riding", priority="default", tags=["moneybag"])
            fired += 1
            if verbose:
                print(f"[{account}] LIVE LADDER {product}: +{level*100:.0f}% milestone at ${px:,.2f}")

        if product not in state["positions"] or state["positions"][product]["units"] <= 1e-10:
            state["positions"].pop(product, None)
            continue
        pos = state["positions"][product]
        hit_trail = pos["peak"] > 0 and px <= pos["peak"] * (1 - trail)
        hit_stop = chg <= -stop
        if not (hit_trail or hit_stop):
            continue

        broker.market_order(product, "sell", pos["units"])
        proceeds = pos["units"] * px * (1 - rate)
        km._record_realized(state, product, pos["units"], pos["avg_cost"], proceeds)
        state["cash"] += proceeds
        reason = "trailing_stop" if hit_trail else "hard_stop"
        state["fills"].append({"ts": int(time.time()), "coin": product, "side": "sell",
                               "price": px, "units": pos["units"], "reason": reason,
                               "source": "risk_monitor"})
        journal.log(account, "exit", coin=product, price=round(px, 4), reason=reason,
                    pnl_pct=round(chg * 100, 1), live=broker.live, source="risk_monitor")
        alerts.notify(
            f"market_coach: {account} LIVE RISK EXIT {product}" + (" (REAL)" if broker.live else " (paper)"),
            f"{reason} caught live at ${px:,.2f} ({chg*100:+.1f}%) — not waiting for the "
            f"next hourly tick", priority="high", tags=["rotating_light"])
        del state["positions"][product]
        fired += 1
        if verbose:
            print(f"[{account}] LIVE EXIT {product}: {reason} at ${px:,.2f} ({chg*100:+.1f}%)")

    if fired:
        km._save(state)
    return fired


def _challenger_accounts():
    import glob, os
    files = (glob.glob(os.path.join(kp.STATE_DIR, "chal_*.kraken.json")) +
             glob.glob(os.path.join(kp.STATE_DIR, "c4h_*.kraken.json")))
    return [(os.path.basename(f)[:-len(".kraken.json")], "sleeve") for f in sorted(files)]


def check_all(verbose=False):
    total = 0
    for name, kind in ACCOUNTS + ARENA_ACCOUNTS + _challenger_accounts():
        if kind == "sleeve":
            total += check_account(name, verbose=verbose)
        elif kind == "moon":
            total += check_moon_account(name, verbose=verbose)
    return total


if __name__ == "__main__":
    n = check_all(verbose=True)
    if n:
        print(f"{n} live risk exit(s) fired.")
