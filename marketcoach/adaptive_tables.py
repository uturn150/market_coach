"""Append-only history of the learned per-regime DCA settings table (paper_state/adaptive_tables.json).
Weekly job re-learns from all real history to date (fixed-setting replays, per-regime Sharpe) and APPENDS a new
entry only when the table changes. Live accounts look up the table valid at each date, so a paper record is never
re-simulated with settings that were learned later (no retroactive lookahead)."""
import json, math, os, time
from . import market_sim as ms, bot_engine as be, community_bots as cb, regimes

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILE = os.path.join(BASE, "paper_state", "adaptive_tables.json")
SETS = [(5, 0.03, 2.0, 0.02), (5, 0.03, 2.0, 0.03), (5, 0.05, 2.0, 0.03), (5, 0.05, 2.0, 0.05), (5, 0.07, 2.0, 0.03), (5, 0.07, 2.0, 0.05)]
MIN_DAYS = 40
DEFAULT = (5, 0.05, 2.0, 0.03)


def _sharpe(v):
    if len(v) < MIN_DAYS:
        return None
    m = sum(v) / len(v); sd = math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1))
    return m / sd if sd > 1e-12 else 0.0


def learn():
    bars = ms.load_long()
    f0, f1 = ms.filters_for(bars)
    ts = [b.ts for b in next(iter(bars.values()))]
    ok = lambda i, s=f1: ts[i] in s
    lm = regimes.label_map(bars["BTC-USD"]); labels = [lm.get(t) for t in ts]
    fr = {}
    for s in SETS:
        e = be.run(cb.DCACycleBot(safety=s[0], dev=s[1], mult=s[2], tp=s[3], stop=0.2, market_ok=ok), bars, cash=100.0).equity
        fr[s] = [0.0] + [e[i] / e[i - 1] - 1 for i in range(1, len(e))]
    table = {}
    for lab in ("TRENDING_UP", "TRENDING_DOWN", "SIDEWAYS", "HIGH_VOLATILITY"):
        sc = []
        for s in SETS:
            x = _sharpe([fr[s][d] for d in range(2, len(ts)) if labels[d - 1] == lab])
            if x is not None:
                sc.append((x, s))
        if sc:
            best = max(sc)
            table[lab] = list(best[1]) if best[0] > 0 else None
    return table


def load():
    try:
        return json.load(open(FILE))
    except Exception:
        return []


def update(verbose=True):
    hist = load()
    t = learn()
    if not hist or hist[-1]["table"] != t:
        hist.append({"from_ts": int(time.time()), "iso": time.strftime("%Y-%m-%d %H:%M"), "table": t})
        json.dump(hist, open(FILE, "w"), indent=1)
        if verbose:
            print("new table:", t)
    elif verbose:
        print("table unchanged:", t)
    return hist


def table_at(ts):
    """Table valid at time ts ({} before the first entry -> bot falls back to its default)."""
    cur = {}
    for e in load():
        if e["from_ts"] <= ts:
            cur = {k: (tuple(v) if v is not None else None) for k, v in e["table"].items()}
    return cur


if __name__ == "__main__":
    update()
