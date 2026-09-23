"""Live A/B of fleet-level policies (PAPER, virtual accounts, $1000 each, updated daily from the bots' REAL live results):
  equal      1/N over every eligible bot
  invvol     inverse-volatility weights (meta_allocator), equal until 45 days of history
  chameleon  regime router at GROUP level: trend bots vs mean-reversion/grid/DCA bots, weights per regime learned
             from history (append-only table, so no retroactive change)
  overlay    equal weights x a regime exposure (100% / 50% / 0% cash) learned from history (append-only)
Regime = previous day's BTC label. Nothing here moves money or places orders.
"""
import json, math, os, time
from . import journal, meta_allocator as ma, meta_live, regimes, kraken_data

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(BASE, "paper_state")
FILE = os.path.join(STATE, "ab_live.json")
TABLES = os.path.join(STATE, "ab_tables.json")
POLICIES = ("equal", "invvol", "chameleon", "overlay")
REGS = ("TRENDING_UP", "TRENDING_DOWN", "SIDEWAYS", "HIGH_VOLATILITY")


def group_of(name):
    return "mean" if (name.startswith("bot_") or "_gr_" in name or "dip" in name or "pullback" in name) else "trend"


def _sharpe(v, min_days=40):
    if len(v) < min_days:
        return None
    m = sum(v) / len(v); sd = math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1))
    return m / sd if sd > 1e-12 else 0.0


def learn_tables():
    """From the 39-bot replay fleet (2020-26): per-regime group weights and overlay exposure."""
    import sys
    sys.path.insert(0, BASE)
    from meta_lab import fleet_series
    from . import market_sim as ms
    ts, R, fam, up = fleet_series()
    bars = ms.load_long()
    lm = regimes.label_map(bars["BTC-USD"]); labels = [lm.get(t) for t in ts]
    grp = {g: [k for k in R if fam[k] == g] for g in ("trend", "mean")}
    gret = {g: [sum(R[k][i] for k in ks) / len(ks) for i in range(len(ts))] for g, ks in grp.items()}
    eq = [sum(R[k][i] for k in R) / len(R) for i in range(len(ts))]
    cham, over = {}, {}
    for lab in REGS:
        sh = {g: _sharpe([gret[g][d] for d in range(2, len(ts)) if labels[d - 1] == lab]) for g in gret}
        pos = {g: s for g, s in sh.items() if s is not None and s > 0}
        cham[lab] = {g: v / sum(pos.values()) for g, v in pos.items()} if pos else {}
        x = _sharpe([eq[d] for d in range(2, len(ts)) if labels[d - 1] == lab])
        over[lab] = 1.0 if (x is None or x > 0.03) else (0.5 if x > 0 else 0.0)
    return {"chameleon": cham, "overlay": over}


def update_tables(verbose=True):
    hist = json.load(open(TABLES)) if os.path.exists(TABLES) else []
    t = learn_tables()
    if not hist or hist[-1]["tables"] != t:
        hist.append({"from_ts": int(time.time()), "iso": time.strftime("%Y-%m-%d %H:%M"), "tables": t})
        json.dump(hist, open(TABLES, "w"), indent=1)
    if verbose:
        print(json.dumps(t))
    return hist


def tables_at(ts):
    cur = None
    for e in (json.load(open(TABLES)) if os.path.exists(TABLES) else []):
        if e["from_ts"] <= ts:
            cur = e["tables"]
    return cur


def _labels():
    bars = [b for b in kraken_data.fetch_recent("BTC-USD", 86400) if b.ts + 86400 <= time.time()]
    lab = regimes.classify_series(bars)
    return {b.ts: l for b, l in zip(bars, lab) if l}


def load():
    try:
        return json.load(open(FILE))
    except Exception:
        return {"start": 1000.0, "acct": {p: {"equity": 1000.0, "history": []} for p in POLICIES}, "last_day": 0}


def tick(verbose=True):
    st = load()
    names = meta_live.eligible()
    R = meta_live.daily_returns(names)
    labels = _labels()
    days = sorted({d for r in R.values() for d in r})
    todo = [d for d in days if d > st["last_day"]]
    for d in todo:
        lab = labels.get(d - 86400)                        # previous day's regime
        tb = tables_at(d) or {}
        hist = {n: [r[x] for x in sorted(r) if x < d] for n, r in R.items()}
        ret_d = {n: R[n].get(d, 0.0) for n in R}
        w_eq = ma.equal(R)
        long_hist = {n: v for n, v in hist.items() if len(v) >= 45}
        w_iv = ma.invvol(long_hist) if len(long_hist) >= 3 else w_eq
        cham_w = {}
        gw = (tb.get("chameleon") or {}).get(lab)
        if gw:
            for g, gwt in gw.items():
                members = [n for n in R if group_of(n) == g]
                for n in members:
                    cham_w[n] = gwt / len(members)
        scale = (tb.get("overlay") or {}).get(lab, 1.0) if tb else 1.0
        if not tb:                                          # before the first learned table exists: behave like equal
            cham_w = w_eq
        pol = {"equal": w_eq, "invvol": w_iv, "chameleon": cham_w, "overlay": {n: w * scale for n, w in w_eq.items()}}
        for p in POLICIES:
            r = sum(w * ret_d.get(n, 0.0) for n, w in pol[p].items())
            a = st["acct"][p]
            a["equity"] *= 1 + r
            a["history"].append({"day": d, "equity": round(a["equity"], 4), "ret_pct": round(r * 100, 3), "regime": lab})
            a["history"] = a["history"][-400:]
        st["last_day"] = d
    st["as_of"] = time.strftime("%Y-%m-%d %H:%M"); st["n_bots"] = len(names)
    json.dump(st, open(FILE, "w"), indent=1)
    if verbose:
        print("A/B equity: " + ", ".join(f"{p} ${st['acct'][p]['equity']:,.2f}" for p in POLICIES) + f"  ({len(names)} bots)")
    return st


if __name__ == "__main__":
    import sys
    if "--learn" in sys.argv:
        update_tables()
    tick()
