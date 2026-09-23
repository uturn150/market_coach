"""Kraken execution: the SAME signals, regime filter, vol/correlation sizing, and
circuit breaker as the paper_portfolio accounts — routed to real order placement
instead of the internal simulator. This is the "tiny live" stage from
GRADUATION.md, not a new trading system: same brain, different hands.

Sleeve shape deliberately mirrors paper_portfolio.py's ({cash, units, last_px,
...}) so both account types can share the same equity math in dashboard.py —
a paper Kraken account tracks its own simulated cash/fees locally exactly like
the internal-simulator accounts do (the real Kraken balance is untouched by a
paper account; it's checked separately in status() as an independent sanity
check, not the source of this account's equity).

This module is NEVER cron'd automatically for a LIVE (non-paper) account.
Opening a live account is a deliberate act you take after graduation_check.py
says READY, and every tick while live prints a loud reminder that real orders
are possible. A paper account (the default) is safe to cron — paper=True is
baked into its saved state, not inferred from whether keys exist.
"""
import json, os, time
from . import kraken_data as data, journal, regime, risk, alerts, paper_exec
from .strategy import Strategy
from .broker_kraken import Kraken, to_kraken_pair

STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "paper_state")


def _path(account):
    return os.path.join(STATE_DIR, f"{account}.kraken.json")


def load(account):
    with open(_path(account)) as f:
        return json.load(f)


def _save(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(_path(state["account"]), "w") as f:
        json.dump(state, f, indent=2)


def open_account(account, strategy_path, coins, granularity=86400, cash=50.0,
                 regime_filter=True, max_drawdown=0.25, paper=True, avoid_regimes=None, market_filter=None):
    """paper=True (the default, and the safe one): orders are simulated even
    though the real connected account is used for prices/balance — this account
    can NEVER place a real order, regardless of whether live keys exist,
    because that's remembered in the account's own state, not inferred from
    'do keys happen to be present.' Only paper=False, passed explicitly, opens
    a live-order account."""
    coins = [c for c in coins if to_kraken_pair(c if "-" in c else f"{c}-USD")]
    products = [c if "-" in c else f"{c}-USD" for c in coins]
    per = round(float(cash) / len(products), 4)
    now = int(time.time())
    sleeves = {}
    for product in products:
        try:
            bars = data.fetch_recent(product, granularity)
            seed = max((b.ts for b in bars if b.ts + granularity <= now), default=0)
            last_px = bars[-1].close if bars else 0.0
        except Exception:
            seed, last_px = 0, 0.0
        sleeves[product] = {"cash": per, "units": 0.0, "last_px": last_px,
                            "last_bar_ts": seed, "pending": None, "peak": 0.0,
                            "in_position": False, "entry_px": 0.0}
        time.sleep(0.2)
    state = {"account": account, "strategy_path": strategy_path, "granularity": granularity,
             "start_cash": float(cash), "sleeves": sleeves, "created": now,
             "regime_filter": bool(regime_filter), "max_drawdown": float(max_drawdown),
             "halted": False, "fills": [], "paper": bool(paper), "peak_equity": float(cash),
             "avoid_regimes": list(avoid_regimes or []), "market_filter": market_filter}
    _save(state)
    broker = Kraken(dry_run=paper)
    if paper:
        mode = "PAPER — real account connected for prices, orders simulated only"
    else:
        mode = "!!! LIVE — REAL MONEY !!!" if broker.live else "DRY-RUN (no Kraken keys yet)"
    print(f"Opened Kraken account '{account}': ${cash:.2f} across {len(products)} coins "
          f"(${per:.2f} each)  [{mode}]")
    return state


def tick(account, now=None, verbose=True):
    state = load(account)
    strat_meta = Strategy.load(state["strategy_path"])
    gran = state["granularity"]
    now = now or int(time.time())
    rate = strat_meta.side_rate  # simulated fee/spread rate, same discipline as everywhere else
    stop = strat_meta.risk.get("stop_loss_pct")
    take = strat_meta.risk.get("take_profit_pct")
    trail = strat_meta.risk.get("trailing_stop_pct")
    # This account's OWN remembered setting decides paper vs live — never inferred
    # from "do keys happen to exist." A paper account stays paper even with real
    # keys saved, forever, unless explicitly reopened with paper=False.
    broker = Kraken(dry_run=state.get("paper", True))

    # broker.live only means "keys exist" — the real question is whether THIS
    # account will place real orders, which is broker.dry_run (from the
    # account's own saved `paper` flag). Warning on the wrong condition here
    # would print a false REAL-MONEY alarm on a completely safe paper account.
    if not broker.dry_run and verbose:
        print("!!! LIVE MODE — this tick can place REAL orders with REAL money !!!")

    risk_on = regime.check_and_alert() if state.get("regime_filter") else True
    can_buy = risk_on and not state.get("halted") and not state.get("needs_revalidation")
    # Stricter market filter (F1: BTC > SMA200 AND SMA50 rising). Tighten-only; fails toward NOT buying.
    if can_buy and state.get("market_filter") == "F1":
        try:
            from . import market_filters as _mf
            if not _mf.f1_on_now():
                can_buy = False
                journal.log(account, "market_filter_blocked", filter="F1")
        except Exception as e:
            can_buy = False
            journal.log(account, "market_filter_blocked", filter="F1", error=str(e)[:80])
    # Learned restriction (tighten-only): no NEW entries while the market regime is
    # one this account was found to lose in. Exits are never affected. If the
    # regime can't be read, fail toward NOT buying.
    if can_buy and state.get("avoid_regimes"):
        try:
            from . import regimes as _rg
            cur = _rg.current_regime()["label"]
            if cur in state["avoid_regimes"]:
                can_buy = False
                journal.log(account, "regime_entry_blocked", regime=cur)
        except Exception as e:
            can_buy = False
            journal.log(account, "regime_entry_blocked", regime="UNKNOWN", error=str(e)[:80])

    all_closed, coin_vols = {}, {}
    for product in state["sleeves"]:
        try:
            bars = data.fetch_recent(product, gran)
        except Exception:
            continue
        closed = [b for b in bars if b.ts + gran <= now]
        if closed:
            all_closed[product] = closed
            coin_vols[product] = risk.realized_vol(closed)
        if data.last_was_live:
            time.sleep(0.2)   # politeness pause only after a real API call, not a shared-cache hit
    vol_weights = risk.vol_target_weights(coin_vols)
    if state.get("fixed_sizing"):          # small accounts: one full-size position per coin, never shrunk below the exchange minimum
        vol_weights = {p: 1.0 for p in coin_vols}
    returns_by_coin = {p: risk.daily_returns(b)[-40:] for p, b in all_closed.items()}

    def _execute(sl, product, bar):
        """Execute the sleeve's pending order at the LIVE price now. Returns True if the order was
        dropped early (nothing to spend / no depth) so the caller skips the rest of that bar."""
        act = sl["pending"]
        prev_why, retry_sell = sl.get("pending_why"), False
        if act == "buy" and not sl["in_position"]:
            held = [p for p in state["sleeves"] if p != product and state["sleeves"][p]["in_position"]]
            held_rets = [returns_by_coin[p] for p in held if p in returns_by_coin]
            corr_mult = risk.correlation_size_multiplier(
                risk.max_correlation_to_held(returns_by_coin.get(product, []), held_rets))
            if state.get("fixed_sizing"):
                corr_mult = 1.0
            spend = min(sl["cash"], sl["cash"] * vol_weights.get(product, 1.0) * corr_mult)
            if state.get("size_mode") in ("half_in_chop", "third_in_chop") and spend > 0:
                try:                                                  # risk: smaller entries when the market is choppy / very volatile
                    from . import regimes as _rg
                    chop = _rg.current_regime()["label"] in ("SIDEWAYS", "HIGH_VOLATILITY")
                except Exception:
                    chop = True                                        # can't read the regime -> assume the cautious case
                if chop:
                    spend = max(min(spend, state.get("min_order", 0.0)) if spend < state.get("min_order", 0.0) else state.get("min_order", 0.0),
                                spend * (0.5 if state["size_mode"] == "half_in_chop" else 0.34))
            if state.get("pooled"):
                # ONE shared cash pool instead of a tiny per-coin slice: every entry is a whole multiple of `order_step` dollars
                # (default $10) and never below Kraken's real minimum for that coin; cash is pulled from the other sleeves as needed.
                from . import kraken_rules
                step = float(state.get("order_step", 10.0))
                px_ref = sl.get("pending_px") or sl.get("last_px") or 0.0
                mn = kraken_rules.min_notional(product, px_ref) if px_ref else 0.0
                spend = step * max(1, int(-(-mn // step)))
                pool = sum(o["cash"] for o in state["sleeves"].values())
                if pool < spend * 1.01:                                # cannot afford a fee-inclusive minimum-size order
                    sl["pending"] = None
                    return True
                need = spend - sl["cash"]
                for p, o in sorted(state["sleeves"].items(), key=lambda kv: -kv[1]["cash"]):
                    if need <= 1e-12: break
                    if p == product: continue
                    take = min(o["cash"], need); o["cash"] -= take; sl["cash"] += take; need -= take
                spend = min(spend, sl["cash"])
            if spend < max(0.01, state.get("min_order", 0.0)):     # below the exchange's minimum order: cannot be placed
                sl["pending"] = None
                return True
            # Realistic paper fill: walk Kraken's real order book (not
            # "signal price = fill price" plus a flat haircut) and charge
            # the account's real taker fee. See marketcoach/paper_exec.py.
            sim = paper_exec.simulate_market_buy(broker, product, spend,
                                                 signal_price=sl.get("pending_px"))
            units = sim.get("filled_units", 0.0)
            if units <= 0:
                sl["pending"] = None
                return True
            broker.market_order(product, "buy", units)  # [DRY] print for paper; real order only if paper=False
            spent = sim["total_cost_usd"]  # fee included; a thin book may spend less than `spend`
            avg_cost = spent / units
            sl["cash"] -= spent
            sl["in_position"], sl["entry_px"], sl["peak"], sl["units"] = True, avg_cost, avg_cost, units
            reason = sl.get("pending_why") or "signal"
            ex = paper_exec.exec_fields(sim)
            state["fills"].append({"ts": bar.ts, "coin": product, "side": "buy",
                                   "price": sim["sim_fill"], "notional": spent, "units": units,
                                   "reason": reason, "exec": ex})
            journal.log(account, "kraken_fill", coin=product, side="buy",
                        price=round(sim["sim_fill"], 8), notional=round(spent, 4),
                        units=round(units, 8), live=broker.live, reason=reason, **ex)
            alerts.notify(f"market_coach: {account} BOUGHT {product}" +
                         (" (REAL)" if broker.live else " (paper)"),
                         f"${spent:.2f} at ~${sim['sim_fill']:,.4f} ({reason}; exec cost "
                         f"${sim['total_exec_cost_usd']:.3f})", priority="high", tags=["moneybag"])
        elif act == "sell" and sl["in_position"]:
            sim = paper_exec.simulate_market_sell(broker, product, sl["units"],
                                                  signal_price=sl.get("pending_px"))
            sold = sim.get("filled_units", 0.0)
            if sold <= 0:
                retry_sell = True  # nothing obtainable right now; try again next tick
            else:
                broker.market_order(product, "sell", sold)
                proceeds = sim["net_proceeds_usd"]
                pnl_pct = (proceeds / sold) / sl["entry_px"] - 1 if sl["entry_px"] else 0
                sl["cash"] += proceeds
                reason = sl.get("pending_why") or "signal"
                ex = paper_exec.exec_fields(sim)
                state["fills"].append({"ts": bar.ts, "coin": product, "side": "sell",
                                       "price": sim["sim_fill"], "units": sold,
                                       "proceeds": proceeds, "reason": reason, "exec": ex})
                journal.log(account, "kraken_fill", coin=product, side="sell",
                            price=round(sim["sim_fill"], 8), units=round(sold, 8),
                            proceeds=round(proceeds, 4), pnl_pct=round(pnl_pct * 100, 2),
                            live=broker.live, reason=reason, **ex)
                alerts.notify(f"market_coach: {account} SOLD {product}" +
                             (" (REAL)" if broker.live else " (paper)"),
                             f"{sold:.6f} units at ~${sim['sim_fill']:,.4f} "
                             f"({pnl_pct*100:+.1f}%) &mdash; {reason}", priority="high", tags=["moneybag"])
                sl["units"] -= sold
                if sl["units"] <= 1e-12:
                    sl["in_position"], sl["entry_px"], sl["peak"], sl["units"] = False, 0.0, 0.0, 0.0
                else:
                    retry_sell = True  # partial fill: the remainder is still owed a sell
        sl["pending"], sl["pending_why"] = None, None
        if retry_sell:
            sl["pending"], sl["pending_why"] = "sell", prev_why
        return False

    for product, sl in state["sleeves"].items():
        closed = all_closed.get(product)
        if not closed:
            continue
        sl["last_px"] = closed[-1].close
        prices = [b.close for b in closed]
        volumes = [b.volume for b in closed]
        highs = [b.high for b in closed]
        lows = [b.low for b in closed]
        product_strat = Strategy.load(state["strategy_path"])
        new = [i for i, b in enumerate(closed) if b.ts > sl["last_bar_ts"]]
        for i in new:
            bar = closed[i]
            # Catch-up: an order left pending by an EARLIER bar (or a partial-fill retry) goes first.
            if _execute(sl, product, bar):
                continue
            if sl["in_position"] and sl["entry_px"] > 0:
                sl["peak"] = max(sl["peak"], bar.close) if sl["peak"] else bar.close
                chg = bar.close / sl["entry_px"] - 1
                hit_trail = trail is not None and sl["peak"] > 0 and bar.close <= sl["peak"] * (1 - abs(trail))
                hit_stop = stop is not None and chg <= -abs(stop)
                hit_take = take is not None and chg >= abs(take)
                if hit_trail or hit_stop or hit_take:
                    sl["pending"], sl["pending_px"] = "sell", bar.close
                    sl["pending_why"] = "trailing_stop" if hit_trail else ("stop_loss" if hit_stop else "take_profit")
            if not sl["pending"]:
                for a in product_strat.signals_at(i, prices, volumes, highs, lows):
                    if a["action"] == "buy" and not sl["in_position"] and can_buy:
                        sl["pending"], sl["pending_why"], sl["pending_px"] = "buy", a.get("why", "signal"), bar.close; break
                    if a["action"] == "sell" and sl["in_position"]:
                        sl["pending"], sl["pending_why"], sl["pending_px"] = "sell", a.get("why", "signal"), bar.close; break
            # Real time: this is the newest closed bar, so act on its signal NOW (the live price is
            # ~ the next bar's open) instead of waiting a whole extra bar. This is exactly what the
            # backtest engine assumes (signal at close -> fill at next open); the old flow was one
            # bar late (found by parity_check.py).
            if i == new[-1] and sl["pending"]:
                _execute(sl, product, bar)
            sl["last_bar_ts"] = bar.ts

    total_value = sum(sl["cash"] + sl["units"] * sl["last_px"] for sl in state["sleeves"].values())

    # circuit breaker: liquidate everything via REAL orders + credit cash + halt
    state["peak_equity"] = max(state.get("peak_equity", state["start_cash"]), total_value)
    dd = total_value / state["peak_equity"] - 1 if state["peak_equity"] else 0
    if not state.get("halted") and dd <= -state.get("max_drawdown", 0.25):
        for product, sl in state["sleeves"].items():
            if sl["in_position"]:
                # Same order-book fill model as normal sells. A breaker must exit
                # everything, so any unfilled remainder (a thin book) is closed at
                # the last price less the flat rate rather than left open.
                sim = paper_exec.simulate_market_sell(broker, product, sl["units"],
                                                      signal_price=sl["last_px"])
                sold = sim.get("filled_units", 0.0)
                if sold > 0:
                    broker.market_order(product, "sell", sold)
                proceeds = sim["net_proceeds_usd"] if sold > 0 else 0.0
                rest = sl["units"] - sold
                if rest > 1e-12:
                    proceeds += rest * sl["last_px"] * (1 - rate)
                sl["cash"] += proceeds
                ex = paper_exec.exec_fields(sim) if sold > 0 else {}
                journal.log(account, "kraken_fill", coin=product, side="sell",
                            reason="circuit_breaker", units=round(sl["units"], 8),
                            proceeds=round(proceeds, 2), live=broker.live, **ex)
                sl["in_position"], sl["units"] = False, 0.0
        state["halted"] = True
        journal.log(account, "circuit_breaker", drawdown=round(dd * 100, 1), live=broker.live,
                    equity=round(total_value, 2), note="liquidated all Kraken positions, halted")
        alerts.notify(f"market_coach: {account} circuit breaker TRIPPED" +
                     (" (REAL MONEY)" if broker.live else " (paper)"),
                     f"Drawdown {dd*100:.1f}% breached limit. Liquidated everything. "
                     f"Run: python3 kraken_portfolio.py reset {account}",
                     priority="urgent", tags=["rotating_light"])

    journal.log(account, "tick", equity=round(total_value, 2),
                holding=sum(1 for sl in state["sleeves"].values() if sl["in_position"]),
                halted=state.get("halted", False), paper=state.get("paper", True))
    _save(state)
    if verbose:
        status(account, state, broker)
    return state


def reset_breaker(account):
    state = load(account)
    state["halted"] = False
    total = sum(sl["cash"] + sl["units"] * sl["last_px"] for sl in state["sleeves"].values())
    state["peak_equity"] = max(total, state["start_cash"])
    _save(state)
    journal.log(account, "reset", equity=round(total, 2), note="kraken circuit breaker re-armed")
    print(f"[{account}] circuit breaker reset; trading resumes. peak reset to ${state['peak_equity']:.2f}")


def mark_needs_revalidation(account, reason):
    """Blocks NEW buy entries only — existing positions (if any) still exit
    normally via their own trailing-stop/stop-loss rules. Distinct from the
    circuit breaker (which liquidates everything on a real drawdown): this is
    a fee/edge-gate finding, not an emergency. Never auto-set by anything
    other than an explicit re-validation run finding a genuine failure — a
    strategy doesn't get flagged just because of a bad week."""
    state = load(account)
    state["needs_revalidation"] = True
    state["revalidation_reason"] = reason
    _save(state)
    journal.log(account, "needs_revalidation", reason=reason)
    print(f"[{account}] marked NEEDS REVALIDATION: {reason}. New entries paused; "
          f"existing positions (if any) still exit normally.")


def retire(account, reason):
    """Human decision: stop entering for good. Blocks new buys (existing positions
    still exit on their own stops), the learner skips it, and the account, its
    history and its rejection-log entry all stay. Reversible only by editing the
    state file — deliberately not a one-liner."""
    state = load(account)
    state["retired"] = True
    state["needs_revalidation"] = True
    state["revalidation_reason"] = f"RETIRED by user: {reason}"
    _save(state)
    journal.log(account, "retired", reason=reason)
    print(f"[{account}] RETIRED: {reason}")


def clear_revalidation(account):
    state = load(account)
    state["needs_revalidation"] = False
    state["revalidation_reason"] = None
    _save(state)
    journal.log(account, "revalidation_cleared", note="re-cleared both gates, entries resumed")
    print(f"[{account}] revalidation cleared; new entries resumed.")


def status(account, state=None, broker=None):
    state = state or load(account)
    is_paper = state.get("paper", True)
    broker = broker or Kraken(dry_run=is_paper)
    held = [p for p, sl in state["sleeves"].items() if sl["in_position"]]
    total = sum(sl["cash"] + sl["units"] * sl["last_px"] for sl in state["sleeves"].values())
    ret = total / state["start_cash"] - 1 if state["start_cash"] else 0
    mode = ("PAPER (real account connected)" if broker.live else "PAPER (dry-run, no keys)") \
        if is_paper else "!!! LIVE — REAL ORDERS !!!"
    print(f"[{account}] [{mode}] equity ${total:,.2f} ({ret*100:+.1f}%)  "
          f"holding {len(held)}/{len(state['sleeves'])}: {', '.join(held) or 'all cash'}")
    if broker.live:
        try:
            bal = broker.balance()
            print(f"  Real Kraken balance (sanity check, separate from this paper account's "
                  f"simulated equity above): {bal}")
        except Exception as e:
            print(f"  balance check failed: {e}")
