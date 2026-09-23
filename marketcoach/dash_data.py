"""Data feeds for the dashboard's research panels: validation matrix,
performance-by-regime, live portfolio view, execution quality, rejection log.

Everything here is READ-ONLY over files the research tools already write
(logs/graduation, logs/regimes, logs/portfolio_sim, logs/portfolio_correlation,
logs/research_rejections.jsonl, logs/learner_rejections.jsonl) plus the live
account state/journals. Each feed is cached (file mtime or short TTL) because
the page rebuilds every few seconds, and each is wrapped by the caller so a
missing file degrades to an empty panel instead of a broken dashboard.
"""
import glob, json, os, time
from collections import defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS = os.path.join(BASE, "logs")
STATE = os.path.join(BASE, "paper_state")

_cache = {}


def _cached(key, ttl, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _cache[key] = (time.time(), val)
    return val


def _latest(pattern):
    files = glob.glob(os.path.join(LOGS, pattern))
    return max(files, key=os.path.getmtime) if files else None


def _json(path):
    try:
        return json.load(open(path))
    except Exception:
        return None


# ---------------------------------------------------------------- validation
STAGE_LABEL = {"HISTORICAL": "Hist", "OUT_OF_SAMPLE": "OOS", "WALK_FORWARD": "Walk-fwd",
               "FEE_GATE": "Fee gate", "EDGE_GATE": "Edge gate", "MONTE_CARLO": "Robust", "HOLDOUT_COINS": "Unseen coins",
               "LIVE_PAPER_EXECUTION": "Execution", "LIVE_PAPER_SAMPLE": "Paper sample",
               "PORTFOLIO_CHECK": "Portfolio"}


def validation():
    def build():
        path = os.path.join(LOGS, "graduation", "latest.json")
        rep = _json(path)
        if not rep:
            return None
        rows = []
        for acct, r in rep["accounts"].items():
            sells = 0
            try:
                st = json.load(open(os.path.join(STATE, f"{acct}.kraken.json")))
                sells = sum(1 for f in st.get("fills", []) if f["side"] == "sell")
            except Exception:
                pass
            rows.append({"account": acct, "kind": r["kind"], "status": r["status"],
                         "blocking": r.get("blocking", []), "paper_round_trips": sells,
                         "stages": {k: v for k, v in r["stages"].items()}})
        return {"generated": rep.get("generated"), "note": rep.get("note"),
                "stage_labels": STAGE_LABEL, "rows": rows,
                "age_hours": round((time.time() - os.path.getmtime(path)) / 3600, 1)}
    return _cached("validation", 30, build)


# ------------------------------------------------------------ regime perf
def regime_perf():
    def build():
        path = _latest("regimes/analysis_*.json")
        rep = _json(path) if path else None
        if not rep:
            return None
        rows = []
        for name, cells in rep["table"].items():
            label = name
            if name.startswith("daily:"):
                label = name[6:] + " (daily)"
            elif name.startswith("4h:"):
                label = "c4h_" + name[3:] + " (4h)"
            rows.append({"name": label, "is_hold": name.startswith("HOLD"),
                         "cells": {k: {"return_pct": v["return_pct"], "excess": v["excess_vs_hold_pct"],
                                       "days": v["days"]} for k, v in cells.items()}})
        return {"period": rep["period"], "share_pct": rep["regime_share_pct"],
                "days": rep["regime_days"], "rows": rows,
                "pairwise_corr": rep["avg_pairwise_strategy_correlation_by_regime"]}
    return _cached("regime_perf", 60, build)


# ------------------------------------------------------------ rejections
_LEARNER_WHY = {"fee gate": "FEE BLEED", "no OOS edge": "NO OOS EDGE", "sample size": "INSUFFICIENT SAMPLE"}


def _read_jsonl(path):
    out = []
    if os.path.exists(path):
        for line in open(path):
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def rejections():
    def build():
        groups = {}
        def add(source, subject, reason, t, detail=""):
            g = groups.setdefault((source, subject, reason), {"source": source, "subject": subject,
                                                              "reason": reason, "count": 0, "t": 0, "detail": detail})
            g["count"] += 1
            g["t"] = max(g["t"], t)
            if detail and not g["detail"]:
                g["detail"] = detail
        for r in _read_jsonl(os.path.join(LOGS, "research_rejections.jsonl")):
            fam = f"4h {r.get('family')}"
            detail = ""
            if r["kind"] == "candidate":
                p = r.get("params")
                detail = f"e.g. {p}" if p else ""
                if r.get("pf_in") is not None:
                    detail += f" (PF {r['pf_in']})"
            elif r.get("detail"):
                detail = str(r["detail"])[:140]
            add("4H research" + (" (family)" if r["kind"] == "family" else ""), fam, r["reason"], r["t"], detail)
        for r in _read_jsonl(os.path.join(LOGS, "learner_rejections.jsonl")):
            reason = "MONTE CARLO FAILURE" if r.get("stage") == "monte_carlo" else _LEARNER_WHY.get(r.get("why"), str(r.get("why")).upper())
            add("learner", f"{r.get('champion')} ({r.get('candidate')})", reason, r["t"], r.get("why", ""))
        # accounts demoted / flagged: also rejected experiments, and must stay visible
        for f in glob.glob(os.path.join(STATE, "*.kraken.json")):
            try:
                s = json.load(open(f))
            except Exception:
                continue
            if s.get("needs_revalidation"):
                why = s.get("revalidation_reason") or ""
                reason = "LIVE FAILURE" if "LIVE FAILURE" in why else "RETIRED" if "RETIRED" in why else "OVERFIT" if "OVERFIT" in why else ("FEE BLEED" if "fee gate" in why.lower() else "FAILED REVALIDATION")
                add("demoted account", s.get("account", os.path.basename(f)), reason, int(os.path.getmtime(f)), why[:140])
        items = sorted(groups.values(), key=lambda g: -g["t"])
        by_reason = defaultdict(int)
        for g in items:
            by_reason[g["reason"]] += g["count"]
        for g in items:
            g["iso"] = time.strftime("%Y-%m-%d %H:%M", time.gmtime(g["t"]))
        return {"by_reason": dict(sorted(by_reason.items(), key=lambda kv: -kv[1])),
                "total": sum(by_reason.values()), "items": items[:40]}
    return _cached("rejections", 30, build)


# --------------------------------------------------------------- exec quality
def exec_quality():
    def build():
        from . import exec_quality as eq
        return eq.summarize()
    return _cached("exec_quality", 60, build)


# ------------------------------------------------------------------ portfolio
def _combined_history(names):
    """Hourly combined equity of the given accounts: each account's last
    logged tick carried forward, summed. Only as long as the live history is."""
    from . import journal
    series = {}
    for n in names:
        ticks = [(t["t"], t["equity"]) for t in journal.read(n, event="tick") if "equity" in t]
        if ticks:
            series[n] = ticks
    if not series:
        return []
    t0 = min(s[0][0] for s in series.values())
    hour0 = t0 - t0 % 3600
    out, idx = [], {n: 0 for n in series}
    last = {n: 50.0 for n in series}
    t = hour0
    now = int(time.time())
    while t <= now:
        for n, s in series.items():
            while idx[n] < len(s) and s[idx[n]][0] <= t + 3600:
                last[n] = s[idx[n]][1]
                idx[n] += 1
        out.append((t, sum(last.values())))
        t += 3600
    return out


def portfolio(accounts, champion_rows):
    """Live view of the real fleet as ONE book: combined equity, exposure,
    per-coin exposure, positions, drawdown — plus the 3-year simulator's
    baseline numbers for context (what the same fleet did historically)."""
    def build():
        from .leaderboard import ACCOUNTS
        real = [a for a, _ in ACCOUNTS]
        accs = [a for a in accounts if a["name"] in real]
        equity = sum(a["equity"] for a in accs)
        start = sum(a["start"] for a in accs) or 1.0
        held = [(a["name"], h) for a in accs for h in a["held"]]
        invested = sum(h["value"] for _, h in held)
        by_coin = defaultdict(lambda: {"value": 0.0, "bots": set()})
        for name, h in held:
            by_coin[h["coin"]]["value"] += h["value"]
            by_coin[h["coin"]]["bots"].add(name)
        coins = sorted(({"coin": c, "value": round(v["value"], 2),
                         "pct_of_equity": round(v["value"] / equity * 100, 1) if equity else 0,
                         "bots": len(v["bots"])} for c, v in by_coin.items()),
                       key=lambda x: -x["value"])
        hist = _cached("combined_history", 60, lambda: _combined_history(real))
        peak, max_dd, cur_dd = (hist[0][1] if hist else start), 0.0, 0.0
        for _, e in hist:
            peak = max(peak, e)
            cur_dd = e / peak - 1 if peak else 0.0
            max_dd = min(max_dd, cur_dd)
        base = _json(os.path.join(LOGS, "portfolio_sim", "baseline.json"))
        corr_path = _latest("portfolio_correlation/analysis_*.json")
        corr = _json(corr_path) if corr_path else None
        top_pairs = []
        if corr:
            m = corr.get("correlation_matrix", {})
            names = list(m)
            pairs = [(a, b, m[a][b]) for i, a in enumerate(names) for b in names[i + 1:]]
            top_pairs = [{"a": a, "b": b, "corr": c} for a, b, c in sorted(pairs, key=lambda t: -t[2])[:5]]
        return {
            "n_accounts": len(accs), "combined_equity": round(equity, 2), "start_equity": round(start, 2),
            "pnl": round(equity - start, 2), "pnl_pct": round((equity / start - 1) * 100, 2),
            "invested": round(invested, 2), "exposure_pct": round(invested / equity * 100, 1) if equity else 0,
            "open_positions": len(held), "bots_holding": len({n for n, _ in held}),
            "max_bots_on_one_coin": max((c["bots"] for c in coins), default=0),
            "coins": coins[:10],
            "drawdown": {"current_pct": round(cur_dd * 100, 2), "max_pct": round(max_dd * 100, 2),
                         "history_hours": len(hist)},
            "correlation": {"avg_pairwise": (corr or {}).get("summary", {}).get("avg_pairwise_correlation"),
                            "top_pairs": top_pairs},
            "simulator_baseline": ({
                "period": base["period"], "max_drawdown_pct": base["max_drawdown_pct"],
                "avg_invested_pct": base["exposure"]["avg_invested_pct"],
                "max_invested_pct": base["exposure"]["max_invested_pct"],
                "max_open_positions": base["exposure"]["max_open_positions"],
                "effective_independent_strategies": base["cross_strategy"]["effective_independent_strategies"],
                "drawdown_correlation": base["cross_strategy"]["avg_drawdown_correlation"],
                "fees_as_pct_of_start": base["fees_as_pct_of_start"]} if base else None),
        }
    return _cached("portfolio", 15, build)


# ------------------------------------------------------------------ learning
def _progress_history():
    try:
        rows = [json.loads(l) for l in open(os.path.join(LOGS, "progress_history.jsonl")) if l.strip()]
        return [{k: r.get(k) for k in ("date", "live_trips", "live_mean_net_pct", "stage_passes", "fleet_return_pct", "eligible")}
                for r in rows[-14:]]
    except Exception:
        return []


def learning():
    def build():
        mem = _json(os.path.join(STATE, "learner_memory.json"))
        st = _json(os.path.join(STATE, "learner_state.json")) or {}
        if not mem:
            return None
        hyps = []
        for hid, h in mem.get("hypotheses", {}).items():
            last = h["history"][-1] if h.get("history") else {}
            hyps.append({"id": hid, "statement": h.get("statement"), "status": h.get("status"),
                         "improved": last.get("improved"), "tested": last.get("tested"),
                         "placebo": last.get("placebo_improved"), "last_tested": h.get("last_tested"),
                         "runs": len(h.get("history", []))})
        chals = [{"name": n, "parent": c["champion"], "status": c["status"], "label": c["label"],
                  "avoid": c.get("avoid_regimes") or [], "market_filter": c.get("market_filter"), **{k: mem.get("ab", {}).get(n, {}).get(k)
                  for k in ("days", "own_return_pct", "parent_return_pct")}}
                 for n, c in st.get("challengers", {}).items()]
        live = []
        for acct, regs in mem.get("live", {}).items():
            for r, v in regs.items():
                live.append({"account": acct, "regime": r, **v})
        return {"runs": mem.get("runs"), "last_run": mem.get("last_run"), "hypotheses": hyps,
                "lessons": mem.get("lessons", []), "restrictions": mem.get("restrictions", [])[-10:],
                "last_events": mem.get("last_events", []), "challengers": chals,
                "live_trips": sum(v["n"] for v in live), "live": sorted(live, key=lambda x: -x["n"])[:12],
                "thresholds": {"min_live_trips": 12, "t_block": -1.5},
                "history": _progress_history()}
    return _cached("learning", 30, build)


# --------------------------------------------------------------------- green
def green():
    """Green-day / green-week scoreboard: live results for the fleet groups, plus the
    backtest studies (green_study.py, green_search.py, green_search_4h.py)."""
    def build():
        hist = []
        try:
            hist = [json.loads(l) for l in open(os.path.join(LOGS, "progress_history.jsonl")) if l.strip()]
        except Exception:
            pass
        live = (hist[-1].get("live_green") if hist else None) or {}
        study = _json(os.path.join(LOGS, "green_study.json")) or {}
        rows = []
        for name, v in study.items():
            o = v.get("oos")
            if o and not name.endswith("+F1"):
                rows.append({"name": name, **{k: o[k] for k in ("green_week_pct", "green_of_active_wk_pct", "green_day_pct",
                             "flat_pct", "worst_week_pct", "total_return_pct", "max_dd_pct")}})
        rows.sort(key=lambda r: -r["green_of_active_wk_pct"])
        fin = []
        for fn, tag in (("green_search.json", "daily"), ("green_search_4h.json", "4h")):
            for r in _json(os.path.join(LOGS, fn)) or []:
                fin.append({"tf": tag, "name": r["family"], "params": r["params"], "oos": r["oos_large"], "mid": r["mid"], "unseen": r["unseen"]})
        fin.sort(key=lambda r: -min(r["oos"]["green_of_active_wk_pct"], r["mid"]["green_of_active_wk_pct"], r["unseen"]["green_of_active_wk_pct"]))
        return {"live": live, "backtest": rows[:12], "finalists": fin[:10], "as_of": hist[-1]["date"] if hist else None}
    return _cached("green", 60, build)


def meta():
    def build():
        st = _json(os.path.join(STATE, "meta_alloc.json"))
        if not st:
            return None
        w = sorted(st.get("weights", {}).items(), key=lambda kv: -kv[1])[:8]
        return {"method": st.get("weights_how") or st.get("method"), "equity": round(st["equity"], 2), "start": st["start"],
                "n_bots": st.get("n_bots"), "as_of": st.get("as_of"), "top": [{"bot": n, "pct": round(v * 100, 1)} for n, v in w],
                "days": len(st.get("history", []))}
    return _cached("meta", 60, build)


def ab():
    def build():
        st = _json(os.path.join(STATE, "ab_live.json"))
        if not st:
            return None
        rows = []
        for p, a in st["acct"].items():
            h = a["history"]
            peak, dd = st["start"], 0.0
            for x in h:
                peak = max(peak, x["equity"]); dd = min(dd, x["equity"] / peak - 1)
            rows.append({"policy": p, "equity": round(a["equity"], 2), "ret_pct": round((a["equity"] / st["start"] - 1) * 100, 2),
                         "max_dd_pct": round(dd * 100, 2), "days": len(h), "last_regime": h[-1]["regime"] if h else None})
        return {"rows": rows, "as_of": st.get("as_of"), "n_bots": st.get("n_bots")}
    return _cached("ab", 60, build)
