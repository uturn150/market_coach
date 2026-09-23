#!/usr/bin/env python3
"""One-glance view of every paper account + market regime + recent moon hits.

  python3 dashboard.py
"""
import glob, json, os, time
from marketcoach import data, regime, journal, alerts, leaderboard as lb

STATE_DIR = os.path.join(os.path.dirname(__file__), "paper_state")


def _price(product, gran):
    try:
        return data.fetch_recent(product, gran)[-1].close
    except Exception:
        return None


def _equity_single(s):
    px = _price(s["source"], s["granularity"]) or 0
    return s["cash"] + s["units"] * px, ("1 held" if s["units"] else "flat")


def _equity_sleeves(s):
    total = held = 0
    for sl in s["sleeves"].values():
        total += sl["cash"] + sl["units"] * sl.get("last_px", 0)
        held += 1 if sl["units"] else 0
    return total, f"{held}/{len(s['sleeves'])} held"


def _equity_moon(s):
    total = s["cash"]
    for c, p in s["positions"].items():
        total += p["units"] * (_price(c, s["granularity"]) or p["avg_cost"])
    return total, f"{len(s['positions'])}/{s['max_positions']} held"




def main():
    print("\n" + "=" * 64)
    print(f" alerts: {'ON via ' + alerts.channel() if alerts.enabled() else 'OFF (no channel configured)'}")
    ro = regime.is_risk_on()
    print(f" MARKET REGIME: {'RISK-ON (BTC > 200d) — entries allowed' if ro else 'RISK-OFF (BTC < 200d) — new buys blocked'}")
    print("=" * 64)
    print(f" {'account':10} {'type':10} {'equity':>9} {'return':>8}  positions")
    print("-" * 64)
    for path in sorted(glob.glob(os.path.join(STATE_DIR, "*.json"))):
        if path.endswith(".alpaca.json"):
            continue
        is_kraken_moon = path.endswith(".kraken_moon.json")
        try:
            s = json.load(open(path))
        except Exception:
            continue
        if not isinstance(s, dict):
            continue  # e.g. research_seen.json is a plain list, not an account
        name = s.get("account", os.path.basename(path)[:-5])
        start = s.get("start_cash", 50)
        if "sleeves" in s:
            eq, pos = _equity_sleeves(s)
            kind = "kraken" if path.endswith(".kraken.json") else "portfolio"
        elif "positions" in s and "max_positions" in s:
            eq, pos = _equity_moon(s)
            kind = "kraken-moon" if is_kraken_moon else "moon-funnel"
        elif "units" in s:
            eq, pos = _equity_single(s); kind = "single"
        else:
            continue
        print(f" {name:10} {kind:10} ${eq:>8.2f} {(eq/start-1)*100:>+7.1f}%  {pos}")
    print("-" * 64)
    hits = journal.read("moon", event="detected", limit=5)
    if hits:
        print(" recent moon detections:")
        for h in hits:
            print(f"   {h['iso']}  {h['coin']:9} score {h.get('score')}  roc {h.get('roc')}")
    else:
        print(" no moons detected recently (logs/moon.jsonl)")

    finds = journal.read("research", event="analyzed", limit=3)
    stubs = len(glob.glob(os.path.join(BASE_DIR := os.path.dirname(__file__), "research_inbox", "*.needs_paste.txt")))
    print(f"\n research: {len(finds)} recent video(s) analyzed, {stubs} awaiting manual "
          f"transcript paste (research_inbox/*.needs_paste.txt)")
    for f in finds:
        print(f"   {f['iso']}  {f['title'][:55]:55} {f.get('n_ideas',0)} ideas")

    print("\n" + lb.fmt(lb.build()))
    print(" " + lb.verdict(lb.build()))

    arena_rows = lb.build(lb.ARENA_ACCOUNTS)
    print("\n--- ARENA (hourly, maker orders, EXPERIMENTAL) ---")
    print("Real pooled backtest: only 1/28 taker+maker combos survives fees+edge at hourly,")
    print("and barely. This sleeve exists to watch frequent live trading happen, not to find")
    print("a winner — treat any short-term 'leader' here as noise, not signal.")
    print(lb.fmt(arena_rows))
    print(" " + lb.verdict(arena_rows))
    print()


if __name__ == "__main__":
    main()
