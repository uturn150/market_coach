"""Live meta-allocator (PAPER, virtual): a fleet-level account whose capital is split across the bots by
meta_allocator weights, re-computed weekly from the bots' REAL live paper results only.

Default method: inverse-volatility ('invvol'). Tested on real 2020-26 history and 100 resampled years it cut
drawdown the most (avg max-drawdown -9.3% vs -14.3% for an equal split) without more green years;
performance-chasing methods (momentum, bandit) did WORSE than equal, so they are not used.
Until a bot has MIN_DAYS of live history the fleet is split equally (nothing to learn from yet).
It moves no money and places no orders: it is a bookkeeping account (paper_state/meta_alloc.json).
"""
import json, os, time
from . import journal, meta_allocator as ma

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(BASE, "paper_state")
FILE = os.path.join(STATE, "meta_alloc.json")
METHOD, MIN_DAYS = "invvol", 45
EXCLUDE = ("arena_", "krakendemo", "zz_")


def eligible():
    out = []
    for f in sorted(os.listdir(STATE)):
        if f.endswith(".kraken.json") and not f.startswith(EXCLUDE):
            try:
                st = json.load(open(os.path.join(STATE, f)))
            except Exception:
                continue
            if st.get("needs_revalidation") or st.get("retired") or st.get("halted"):
                continue
            out.append(st["account"])
    return out


def daily_returns(names):
    """{bot: {day_ts: daily return}} from the hourly tick journal (last tick of each UTC day)."""
    out = {}
    for n in names:
        byday = {}
        for t in journal.read(n, event="tick"):
            if "equity" in t:
                byday[(t["t"] // 86400) * 86400] = t["equity"]
        days = sorted(byday)
        if len(days) >= 2:
            out[n] = {days[i]: byday[days[i]] / byday[days[i - 1]] - 1 for i in range(1, len(days)) if byday[days[i - 1]] > 0}
    return out


def load():
    try:
        return json.load(open(FILE))
    except Exception:
        return {"method": METHOD, "start": 1000.0, "equity": 1000.0, "history": [], "weights": {}, "weights_day": 0}


def tick(verbose=True):
    st = load()
    names = eligible()
    R = daily_returns(names)
    today = (int(time.time()) // 86400) * 86400
    days = sorted({d for r in R.values() for d in r})
    if not days:
        return st
    last_done = st["history"][-1]["day"] if st["history"] else 0
    for d in [x for x in days if x > last_done and x < today + 1]:
        # weights fixed at the start of the week from data BEFORE that day
        wk = (d // 86400 + 3) // 7
        if st.get("weights_week") != wk:
            hist = {n: [r[x] for x in sorted(r) if x < d] for n, r in R.items()}
            hist = {n: v for n, v in hist.items() if len(v) >= MIN_DAYS}
            if len(hist) >= 3:
                w, how = ma.METHODS[st.get("method", METHOD)](hist), st.get("method", METHOD)
            else:
                w, how = ma.equal({n: [] for n in R}), "equal (not enough live history yet)"
            st.update(weights=w, weights_week=wk, weights_how=how)
        ret = sum(w * R[n].get(d, 0.0) for n, w in st["weights"].items())
        st["equity"] *= 1 + ret
        st["history"].append({"day": d, "equity": round(st["equity"], 4), "ret_pct": round(ret * 100, 3)})
    st["history"] = st["history"][-400:]
    st["as_of"] = time.strftime("%Y-%m-%d %H:%M")
    st["n_bots"] = len(names)
    json.dump(st, open(FILE, "w"), indent=1)
    if verbose:
        top = sorted(st["weights"].items(), key=lambda kv: -kv[1])[:5]
        print(f"meta_alloc equity ${st['equity']:,.2f} ({(st['equity']/st['start']-1)*100:+.2f}%), {st['n_bots']} bots, weights by {st.get('weights_how')}: " +
              ", ".join(f"{n} {w*100:.0f}%" for n, w in top))
    return st


if __name__ == "__main__":
    tick()
