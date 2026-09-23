"""Progress tracker: is the bot actually getting better? Once a day it snapshots a few
MEASURABLE numbers into logs/progress_history.jsonl so improvement (or the lack of it) is a
trend you can read, not a claim. Also writes a plain-English report (logs/progress/latest.md)
and pushes a short summary. Read-only over existing state; changes nothing.

Numbers tracked (all from real paper results, never backtests):
  live_trips / live_mean_net_pct   closed paper round trips across accounts still allowed to trade, mean net
  stage_passes                     total PASS cells across the graduation matrix, and per stage
  ab                               each challenger vs its parent, same live period
  best_account                     the account closest to eligibility and what blocks it
"""
import glob, json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from . import regime_learner as RL, regimes, market_filters, alerts

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HIST = os.path.join(BASE, "logs", "progress_history.jsonl")
OUT = os.path.join(BASE, "logs", "progress")
STATE = os.path.join(BASE, "paper_state")


def _equity(st):
    return sum(s["cash"] + s["units"] * s["last_px"] for s in st["sleeves"].values())


def _group_series(names):
    """{day_ts: combined equity} from hourly journal ticks: each account's last tick of the day,
    carried forward, summed. Needs no backtest: this is what actually happened, live."""
    from . import journal
    per = {}
    for n in names:
        byday = {}
        for t in journal.read(n, event="tick"):
            if "equity" in t:
                byday[(t["t"] // 86400) * 86400] = t["equity"]
        if byday:
            per[n] = byday
    if not per:
        return {}
    days = list(range(min(min(d) for d in per.values()), (int(time.time()) // 86400) * 86400 + 1, 86400))
    out, last = {}, {n: None for n in per}
    for d in days:
        for n, s_ in per.items():
            if d in s_:
                last[n] = s_[d]
        if all(v is not None for v in last.values()):
            out[d] = sum(last.values())
    return out


def live_green():
    import glob as _g
    from .learner import CHAMPIONS
    from green_study import stats
    groups = {"green_candidates": sorted(os.path.basename(f)[:-len(".kraken.json")] for f in _g.glob(os.path.join(STATE, "chal_gr_*.kraken.json"))),
              "champions": list(CHAMPIONS)}
    out = {}
    for g, names in groups.items():
        ser = _group_series(names)
        st = stats(ser) if len(ser) >= 30 else None
        out[g] = st or {"days": len(ser), "note": "needs 30+ days of live history"}
    return out


def collect():
    rows, pooled = {}, RL.live_pooled()
    for f in glob.glob(os.path.join(STATE, "*.kraken.json")):
        st = json.load(open(f))
        rows[st["account"]] = {"equity": _equity(st), "start": st.get("start_cash", 50.0),
                               "paused": bool(st.get("needs_revalidation"))}
    active = {a: s for a, s in pooled.items() if not rows.get(a, {}).get("paused")}
    n = sum(s["n"] for s in active.values())
    mean = sum(s["mean_pct"] * s["n"] for s in active.values()) / n if n else None
    grad = json.load(open(os.path.join(BASE, "logs", "graduation", "latest.json")))
    per_stage, best = {}, None
    for a, r in grad["accounts"].items():
        passes = 0
        for s, v in r["stages"].items():
            if s.startswith("_"):
                continue
            if v["status"] == "PASS":
                per_stage[s] = per_stage.get(s, 0) + 1
                passes += 1
        fails = [s for s, v in r["stages"].items() if v["status"] == "FAIL"]
        key = (len(fails), -passes)
        if best is None or key < best[0]:
            best = (key, a, fails, passes)
    mem = json.load(open(RL.MEMORY_FILE)) if os.path.exists(RL.MEMORY_FILE) else {}
    RL.update_ab(mem)
    ab = {n_: {k: v.get(k) for k in ("parent", "days", "own_return_pct", "parent_return_pct")}
          for n_, v in mem.get("ab", {}).items()}
    try:
        cur = regimes.current_regime()["label"]; f1 = market_filters.f1_on_now()
    except Exception:
        cur, f1 = None, None
    tot_eq, tot_start = sum(r["equity"] for r in rows.values()), sum(r["start"] for r in rows.values())
    return {"t": int(time.time()), "date": time.strftime("%Y-%m-%d"), "accounts": len(rows),
            "fleet_return_pct": round((tot_eq / tot_start - 1) * 100, 2),
            "live_trips": n, "live_mean_net_pct": round(mean, 3) if mean is not None else None,
            "stage_passes": sum(per_stage.values()), "per_stage": per_stage,
            "eligible": sum(1 for r in grad["accounts"].values() if r["status"] == "ELIGIBLE_FOR_HUMAN_REVIEW"),
            "best_account": {"name": best[1], "fails": best[2], "passes": best[3]} if best else None,
            "hypotheses": {h: v.get("status") for h, v in mem.get("hypotheses", {}).items()},
            "restrictions": len(mem.get("restrictions", [])), "killed": len(mem.get("killed", [])),
            "ab": ab, "regime": cur, "f1_on": f1, "live_green": live_green()}


def _prev():
    if not os.path.exists(HIST):
        return None
    lines = [json.loads(l) for l in open(HIST) if l.strip()]
    today = time.strftime("%Y-%m-%d")
    lines = [l for l in lines if l["date"] != today]
    return lines[-1] if lines else None


def render(snap, prev):
    def delta(k, unit=""):
        if prev is None or prev.get(k) is None or snap.get(k) is None:
            return ""
        d = snap[k] - prev[k]
        return f" ({d:+.2f}{unit} vs {prev['date']})" if isinstance(d, float) else f" ({d:+d} vs {prev['date']})"
    L = [f"# Progress {snap['date']}", "",
         f"- Market: {snap['regime']}, F1 filter {'ON' if snap['f1_on'] else 'OFF'}.",
         f"- Fleet: {snap['accounts']} paper accounts, combined return {snap['fleet_return_pct']:+.2f}%{delta('fleet_return_pct', '%')}.",
         f"- Live evidence: {snap['live_trips']} closed paper trips, mean net "
         f"{snap['live_mean_net_pct'] if snap['live_mean_net_pct'] is not None else 'n/a'}%/trip{delta('live_mean_net_pct', '%')}.",
         f"- Graduation: {snap['stage_passes']} stage passes{delta('stage_passes')}, {snap['eligible']} eligible for human review.",
         ]
    b = snap["best_account"]
    if b:
        L.append(f"- Closest account: {b['name']} ({b['passes']} passes; failing {', '.join(b['fails']) or 'nothing yet'}).")
    L.append(f"- Learner: hypotheses {snap['hypotheses']}; {snap['restrictions']} live restrictions, {snap['killed']} live failures.")
    for g, v in (snap.get("live_green") or {}).items():
        if "green_week_pct" in v:
            L.append(f"- Live green ({g}): {v['green_day_pct']}% green days ({v['flat_pct']}% flat), {v['green_week_pct']}% green weeks, worst week {v['worst_week_pct']}%.")
        else:
            L.append(f"- Live green ({g}): {v.get('days', 0)} days of history so far (needs 30).")
    if snap["ab"]:
        L.append("- A/B (challenger vs parent, same live period):")
        for n, a in snap["ab"].items():
            if a.get("own_return_pct") is not None:
                L.append(f"    {n}: {a['own_return_pct']:+.2f}% vs {a['parent']} {a['parent_return_pct']:+.2f}% over {a['days']}d")
    return "\n".join(L)


def run(push=True, verbose=True):
    snap = collect()
    prev = _prev()
    text = render(snap, prev)
    os.makedirs(OUT, exist_ok=True)
    open(os.path.join(OUT, "latest.md"), "w").write(text + "\n")
    lines = [json.loads(l) for l in open(HIST)] if os.path.exists(HIST) else []
    lines = [l for l in lines if l["date"] != snap["date"]] + [snap]
    with open(HIST, "w") as f:
        f.write("\n".join(json.dumps(l) for l in lines) + "\n")
    if push:
        alerts.notify("market_coach daily progress", text.split("\n", 2)[2][:900], priority="low", tags=["chart_with_upwards_trend"])
    if verbose:
        print(text)
    return snap
