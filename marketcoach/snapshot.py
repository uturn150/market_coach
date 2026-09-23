"""Build the live account snapshot — the same data dashboard.py and the Position
Book artifact render, extracted into one function so the web app can call it
without re-fetching upstream data more often than necessary.
"""
import glob, json, os, time
import dashboard as db
from . import regime, journal, alerts, leaderboard as lb
from .broker_kraken import Kraken

STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "paper_state")


def _live_px(broker, product, fallback):
    """Live ticker price for a held coin, falling back to whatever price the
    caller already had (last bar close / avg cost) if the live call fails —
    never show nothing just because one network call hiccuped."""
    try:
        px = broker.ticker_price_cached(product)
        if px:
            return px
    except Exception:
        pass
    return fallback


def _holding(broker, product, units, entry_px):
    """One held position's full live detail: how many units were actually
    bought, at what price, and what that's worth right now — not just a
    dollar total with no way to see the underlying share count."""
    live = _live_px(broker, product, entry_px)
    return {
        "coin": product.replace("-USD", ""),
        "units": round(units, 8),
        "entry_px": round(entry_px, 8) if entry_px else 0,
        "live_px": round(live, 8) if live else 0,
        "value": round(units * live, 2),
        "pnl_pct": round((live / entry_px - 1) * 100, 2) if entry_px else 0.0,
    }


def build():
    snap = {"generated": int(time.time()), "regime_risk_on": regime.is_risk_on(),
           "alert_channel": alerts.channel(), "accounts": [], "moons": [], "research": []}
    broker = Kraken(dry_run=True)  # ticker_price is public/always-safe; one shared instance
    try:  # one bulk request warms the cache for every held position below
        from .broker_kraken import PAIR_MAP
        broker.prefetch_prices(list(PAIR_MAP))
    except Exception:
        pass

    for path in sorted(glob.glob(os.path.join(STATE_DIR, "*.json"))):
        base = os.path.basename(path)
        if base.endswith(".alpaca.json") or "keys" in base or "seen" in base or "regime_state" in base:
            continue
        try:
            s = json.load(open(path))
        except Exception:
            continue
        if not isinstance(s, dict):
            continue
        is_kraken_moon = base.endswith(".kraken_moon.json")
        name = s.get("account", base[:-5])
        if "sleeves" in s:
            eq, pos = db._equity_sleeves(s)
            kind = "kraken" if base.endswith(".kraken.json") else "portfolio"
            held = [_holding(broker, c, sl["units"], sl.get("entry_px", sl.get("last_px", 0)))
                   for c, sl in s["sleeves"].items() if sl["units"] > 0]
            n_coins = len(s["sleeves"])
        elif "positions" in s and "max_positions" in s:
            eq, pos = db._equity_moon(s)
            kind = "kraken-moon" if is_kraken_moon else "moon-funnel"
            held = [_holding(broker, c, p["units"], p["avg_cost"])
                   for c, p in s["positions"].items()]
            n_coins = s["max_positions"]
        elif "units" in s:
            eq, pos = db._equity_single(s); kind = "single"
            held = [_holding(broker, s["source"], s["units"], s.get("avg_cost", 0))] if s["units"] > 0 else []
            n_coins = 1
        else:
            continue
        days = (snap["generated"] - s.get("created", snap["generated"])) / 86400
        snap["accounts"].append({
            "name": name, "kind": kind, "equity": round(eq, 2), "start": s.get("start_cash", 50),
            "ret_pct": round((eq / s.get("start_cash", 50) - 1) * 100, 2), "days_live": round(days, 1),
            "halted": s.get("halted", False), "regime_filter": bool(s.get("regime_filter")),
            "needs_revalidation": bool(s.get("needs_revalidation", False)),
            "revalidation_reason": s.get("revalidation_reason"),
            "trades": len(s.get("fills", [])), "held": held, "n_coins": n_coins,
            "strategy": os.path.basename(s.get("strategy_path", "")).replace(".json", "")
                       .replace("_", " ") or "breakout funnel",
        })

    snap["moons"] = [{"iso": h["iso"], "coin": h["coin"].replace("-USD", ""),
                      "score": h.get("score"), "roc": h.get("roc")}
                     for h in journal.read("moon", event="detected", limit=5)]
    snap["research"] = [{"iso": r["iso"], "title": r["title"], "n_ideas": r.get("n_ideas", 0),
                         "summary": r.get("summary", "")}
                        for r in journal.read("research", event="analyzed", limit=3)]
    rows = lb.build()
    snap["leaderboard"] = rows
    snap["leaderboard_verdict"] = lb.verdict(rows)
    arena_rows = lb.build(lb.ARENA_ACCOUNTS)
    snap["arena"] = arena_rows
    snap["arena_verdict"] = lb.verdict(arena_rows)
    chal_rows = lb.build(lb.challenger_accounts())
    try:
        from . import regimes
        snap["market_regime"] = regimes.current_regime()
    except Exception:
        snap["market_regime"] = None
    snap["challengers"] = chal_rows
    # research panels: each is optional — a missing/stale file must never break the page
    from . import dash_data
    for key, fn in (("validation", dash_data.validation), ("regime_perf", dash_data.regime_perf),
                    ("rejections", dash_data.rejections), ("learning", dash_data.learning), ("green", dash_data.green), ("meta", dash_data.meta), ("ab", dash_data.ab), ("exec_quality", dash_data.exec_quality),
                    ("portfolio", lambda: dash_data.portfolio(snap["accounts"], rows))):
        try:
            snap[key] = fn()
        except Exception as e:
            snap[key] = None
    snap["challengers_verdict"] = lb.verdict(chal_rows) if chal_rows else "No challengers yet."
    return snap
