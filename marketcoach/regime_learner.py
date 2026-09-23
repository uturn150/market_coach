"""Regime learner: learns WHEN each strategy loses, remembers it, and acts on
it only in the safe (tighten-only) direction.

Two learning loops, both persisted in paper_state/learner_memory.json so every
run starts from what was learned before:

  1. HYPOTHESIS LOOP (historical). Standing hypothesis H_SIDEWAYS: "sitting out
     SIDEWAYS-regime days improves a strategy". Tested on every champion at the
     real fee tier, on the same 60/40 in-sample/out-of-sample split and the same
     gates as everything else. A champion "improves" only if profit factor
     (in-sample) AND return (out-of-sample) both beat its own baseline. The
     hypothesis is SUPPORTED only when it REPLICATES across independent
     strategies (>= MIN_REPLICATION share of the >= MIN_TESTED tested), which
     is what stops it being data-mined per strategy. Only then may a challenger
     be spawned (separate paper account, same strategy + avoid_regimes), and
     only if that strategy individually clears the gates and Monte Carlo.
  2. LIVE LOOP. Every closed live-paper round trip is attributed to the regime
     in force when it was entered. When an account has >= MIN_LIVE_TRIPS in a
     regime and its mean net return there is significantly negative
     (t <= T_BLOCK), that regime is ADDED to its avoid_regimes. New entries
     only; exits untouched. Restrictions are never removed automatically.
  3. A/B TRACKING. Each spawned challenger is compared to its parent over the
     same live period so the memory records whether the lesson actually helped.

Never promotes, never enables live trading, never touches real orders.
"""
import json, math, os, time
from collections import defaultdict
from . import data, regimes, journal, alerts, kraken_portfolio as kp
from . import learner as L
from .learner import _log, LOG, BASE, STATE_DIR, gates, CHAMPIONS, MAX_LIVE_CHALLENGERS
from .monte_carlo import bootstrap, summarize_distribution

MEMORY_FILE = os.path.join(STATE_DIR, "learner_memory.json")
# Pre-registered hypotheses: fixed here BEFORE looking at results, so adding
# a new one is a visible code change, never a silent search over regimes.
HYPOTHESES = {
    "avoid_SIDEWAYS": {"avoid": ["SIDEWAYS"]},
    "avoid_HIGH_VOLATILITY": {"avoid": ["HIGH_VOLATILITY"]},
    "avoid_SIDEWAYS_and_HIGH_VOLATILITY": {"avoid": ["SIDEWAYS", "HIGH_VOLATILITY"]},
    "market_filter_F1": {"filter": "F1"},   # BTC > SMA200 AND SMA50 rising (market_filters.py)
}
MAX_FILTER_CHALLENGERS = 4   # F1 challengers have their own slots, apart from the regime-avoid ones
H_SIDEWAYS = "avoid_SIDEWAYS"
MIN_TESTED = 5            # need this many testable strategies before a verdict
MIN_REPLICATION = 0.4     # share of tested strategies that must improve in BOTH halves
MIN_LIVE_TRIPS = 12       # live round trips in one regime before it can drive a restriction
T_BLOCK = -1.5            # t-statistic of mean live net return that triggers a restriction
MC_P5, MC_LOSS = 0.0, 0.10
KILL_MIN_TRIPS, KILL_T = 30, -3.0   # live experiment that has clearly failed: >=30 trips, t <= -3
PLACEBO_SEEDS = 5         # random day-blocking of the SAME share of days: the control group


def _load():
    try:
        return json.load(open(MEMORY_FILE))
    except Exception:
        return {"hypotheses": {}, "live": {}, "restrictions": [], "ab": {}, "lessons": [], "runs": 0}


def _save(m):
    os.makedirs(STATE_DIR, exist_ok=True)
    json.dump(m, open(MEMORY_FILE, "w"), indent=1)


def _label(hid):
    h = HYPOTHESES[hid]
    return "avoid " + "+".join(h["avoid"]) if "avoid" in h else "market filter " + h["filter"]


def _stamp(hid):
    h = HYPOTHESES[hid]
    return {"avoid_regimes": list(h["avoid"])} if "avoid" in h else {"market_filter": h["filter"]}


def mask_for(hid, base_allow, btc_bars):
    h = HYPOTHESES[hid]
    if "avoid" in h:
        return regime_mask(base_allow, btc_bars, h["avoid"])
    from . import market_filters
    return base_allow & market_filters.f1_timestamps(btc_bars)


def account_allow(st, base_allow, coin_bars):
    """The buy-permission set an account is actually judged under (its own learned restrictions)."""
    allow = base_allow
    if st.get("granularity") == 14400:       # 4h accounts: daily F1 mapped onto 4h bars
        if st.get("market_filter") == "F1" and "BTC-USD" in coin_bars:
            from . import market_filters
            allow = allow & market_filters.f1_timestamps_4h(data.get_bars("BTC-USD", 86400, max_bars=1500), coin_bars["BTC-USD"])
        return allow
    if "BTC-USD" in coin_bars:
        if st.get("market_filter") == "F1":
            from . import market_filters
            allow = allow & market_filters.f1_timestamps(coin_bars["BTC-USD"])
        if st.get("avoid_regimes"):
            allow = regime_mask(allow, coin_bars["BTC-USD"], st["avoid_regimes"])
    return allow


def regime_mask(base_allow, btc_bars, avoid):
    """base_allow minus timestamps whose OWN-close regime label is in `avoid`.
    A bar's label uses only data through that bar's close, the same moment the
    strategy's signal on that bar is computed: no lookahead."""
    labels = regimes.label_map(btc_bars)
    return {ts for ts in base_allow if labels.get(ts) not in avoid}


def placebo_mask(base_allow, masked, seed):
    """Block a RANDOM set of days as large as the real SIDEWAYS block. If random
    blocking 'improves' strategies as often as regime blocking does, the effect
    is just 'trade less in this window', not anything about sideways markets."""
    import random
    blocked = len(base_allow) - len(masked)
    drop = set(random.Random(seed).sample(sorted(base_allow), blocked)) if blocked > 0 else set()
    return base_allow - drop


# ------------------------------------------------------------ live evidence
def _round_trips(fills):
    """FIFO per coin -> [(entry_ts, net_return)] using actual cash in/out."""
    lots, out = defaultdict(list), []
    for f in fills:
        c = f["coin"]
        if f["side"] == "buy":
            cost = f.get("notional") or f["price"] * f["units"]
            lots[c].append([f["units"], cost, f["ts"]])
        else:
            rem, proceeds, units = f["units"], f.get("proceeds"), f["units"]
            if proceeds is None:
                proceeds = f["price"] * units
            while rem > 1e-12 and lots[c]:
                lot = lots[c][0]
                take = min(lot[0], rem)
                cost_part = lot[1] * take / lot[0] if lot[0] else 0
                proceeds_part = proceeds * take / units if units else 0
                if cost_part > 0:
                    out.append((lot[2], proceeds_part / cost_part - 1))
                lot[1] -= cost_part
                lot[0] -= take
                rem -= take
                if lot[0] <= 1e-12:
                    lots[c].pop(0)
    return out


def live_evidence(labels):
    """{account: {regime: {n, mean_pct, t}}} from every paper account's fills."""
    import glob
    ev = {}
    for f in glob.glob(os.path.join(STATE_DIR, "*.kraken.json")):
        try:
            st = json.load(open(f))
        except Exception:
            continue
        by = defaultdict(list)
        for ts, ret in _round_trips(st.get("fills", [])):
            day = (ts // 86400) * 86400 - 86400
            lab = labels.get(day) or labels.get(day + 86400 - 86400)
            if lab:
                by[lab].append(ret)
        if by:
            ev[st["account"]] = {r: _stats(v) for r, v in by.items()}
    return ev


def live_pooled():
    """{account: stats over ALL its closed live-paper trips, any regime}."""
    import glob
    out = {}
    for f in glob.glob(os.path.join(STATE_DIR, "*.kraken.json")):
        try:
            st = json.load(open(f))
        except Exception:
            continue
        rets = [r for _, r in _round_trips(st.get("fills", []))]
        if rets:
            out[st["account"]] = _stats(rets)
    return out


def retire_failed_experiments(mem, pooled, act):
    """Live results overrule a backtest: an ARENA/demo/factory-explorer experiment with >=KILL_MIN_TRIPS
    closed paper trips whose mean net return is significantly negative (t <= KILL_T)
    has failed. Its NEW entries are paused (needs_revalidation); positions still exit,
    the account and its record stay. Champions/challengers are never auto-killed here —
    they go through the gates and a human. Tighten-only."""
    events = []
    for acct, s in pooled.items():
        if not (acct.startswith("arena_") or acct.startswith("krakendemo") or "_fxe_" in acct or "_gr_" in acct):
            continue
        try:
            st = kp.load(acct)
        except FileNotFoundError:
            continue
        if st.get("needs_revalidation") or st.get("retired"):
            continue
        if s["n"] >= KILL_MIN_TRIPS and s["t"] <= KILL_T:
            msg = f"{acct}: LIVE FAILURE (n={s['n']} trips, mean {s['mean_pct']}% net, t={s['t']}): entries paused"
            if act:
                kp.mark_needs_revalidation(acct, f"LIVE FAILURE: {s['n']} paper trips, mean {s['mean_pct']}% net, t={s['t']}")
                mem.setdefault("killed", []).append({"t": int(time.time()), "account": acct, **s})
                _log(LOG, action="live_failure", account=acct, **s)
            events.append(msg if act else "[dry run] " + msg)
    return events


def _stats(rets):
    n = len(rets)
    m = sum(rets) / n
    if n > 1:
        sd = math.sqrt(sum((x - m) ** 2 for x in rets) / (n - 1))
        t = m / (sd / math.sqrt(n)) if sd > 0 else 0.0
    else:
        t = 0.0
    return {"n": n, "mean_pct": round(m * 100, 3), "t": round(t, 2)}


# --------------------------------------------------------- hypothesis loop
def test_hypothesis(mem, hid, coin_bars, allow, btc_bars, verbose=True):
    masked = mask_for(hid, allow, btc_bars)
    results = {}
    for account, sname in CHAMPIONS.items():
        try:
            acct = kp.load(account)
        except FileNotFoundError:
            continue
        if acct.get("retired"):
            continue
        spec = json.load(open(os.path.join(BASE, acct["strategy_path"])))
        base = gates(spec, coin_bars, allow)
        var = gates(spec, coin_bars, masked)
        if "raw" not in base or "raw" not in var:
            results[account] = {"testable": False, "why": "no trades"}
            continue
        b, v = base["raw"], var["raw"]
        results[account] = {
            "testable": True, "base_pf_in": round(b["pf_in"], 2), "var_pf_in": round(v["pf_in"], 2),
            "base_out_ret_pct": round(b["out_return"] * 100, 1), "var_out_ret_pct": round(v["out_return"] * 100, 1),
            "var_trades_in": v["n_in"], "base_trades_in": b["n_in"],
            "improves_both": v["pf_in"] > b["pf_in"] and v["out_return"] > b["out_return"],
            "var_passes_gates": var["pass"], "var_why": var["why"]}
    tested = [r for r in results.values() if r["testable"]]
    improved = sum(1 for r in tested if r["improves_both"])
    # control group: same test with randomly chosen days blocked instead
    placebo_counts = []
    for seed in range(PLACEBO_SEEDS):
        pm = placebo_mask(allow, masked, seed)
        cnt = 0
        for account, r in results.items():
            if not r["testable"]:
                continue
            acct = kp.load(account)
            spec = json.load(open(os.path.join(BASE, acct["strategy_path"])))
            pv = gates(spec, coin_bars, pm).get("raw")
            if pv and pv["pf_in"] > r["base_pf_in"] and pv["out_return"] * 100 > r["base_out_ret_pct"]:
                cnt += 1
        placebo_counts.append(cnt)
    if len(tested) < MIN_TESTED:
        verdict = "INCONCLUSIVE"
    elif improved / len(tested) >= MIN_REPLICATION and improved > max(placebo_counts):
        verdict = "SUPPORTED"
    else:
        verdict = "REFUTED"
    h = mem["hypotheses"].setdefault(hid, {
        "statement": f"Using '{_label(hid)}' improves a strategy (real fees, in-sample PF and out-of-sample return both beat baseline), replicated across strategies and beating a random-day control.",
        "history": []})
    h["status"], h["last_tested"] = verdict, time.strftime("%Y-%m-%d %H:%M")
    h["history"].append({"t": int(time.time()), "verdict": verdict, "improved": improved,
                         "placebo_improved": placebo_counts, "tested": len(tested), "data_end": max(b[-1].ts for b in coin_bars.values())})
    h["history"] = h["history"][-60:]
    h["latest"] = results
    _log(LOG, action="hypothesis_test", hypothesis=hid, verdict=verdict,
         improved=improved, tested=len(tested))
    return verdict, results, masked


def spawn_from_hypothesis(mem, hid, state, results, coin_bars, masked, act):
    events = []
    is_filter = "filter" in HYPOTHESES[hid]
    stamp = _stamp(hid)
    ranked = sorted(((a, r) for a, r in results.items() if r.get("testable")),
                    key=lambda t: -(t[1]["var_pf_in"] / t[1]["base_pf_in"]) if t[1]["base_pf_in"] else 0)  # in-sample only
    for account, r in ranked:
        if not r["improves_both"] or not r["var_passes_gates"]:
            continue
        if any(c["champion"] == account and c["status"] != "RETIRED" and c.get("stamp") == stamp
               for c in state["challengers"].values()):
            continue
        live = [c for c in state["challengers"].values() if c["status"] != "RETIRED"]
        if is_filter:
            full = sum(1 for c in live if "market_filter" in (c.get("stamp") or {})) >= MAX_FILTER_CHALLENGERS
        else:
            full = sum(1 for c in live if "market_filter" not in (c.get("stamp") or {})) >= MAX_LIVE_CHALLENGERS
        if full:
            events.append(f"challenger cap reached; further {_label(hid)} candidates wait")
            break
        acct = kp.load(account)
        spec = json.load(open(os.path.join(BASE, acct["strategy_path"])))
        trades = L.collect_trades_for(spec, coin_bars, masked)
        dist = summarize_distribution(bootstrap(trades, n_sims=1500, starting_cash=50.0), 50.0, 0.0)
        if dist.get("n_sims") and (dist["p5_return_pct"] < MC_P5 or dist["probability_of_losing_money"] > MC_LOSS):
            L._log(L.REJECT_LOG, champion=account, candidate=hid, stage="monte_carlo",
                   why="fragile bootstrap distribution", p5=dist["p5_return_pct"],
                   p_loss=dist["probability_of_losing_money"])
            events.append(f"{account}: {hid} improves but failed Monte Carlo — not spawned")
            continue
        if act:
            name = _spawn(account, spec, state, stamp, _label(hid), {"pf_in": r["var_pf_in"], "out_return_pct": r["var_out_ret_pct"],
                                                 "baseline_pf_in": r["base_pf_in"],
                                                 "baseline_out_return_pct": r["base_out_ret_pct"],
                                                 "mc_p5_return_pct": dist.get("p5_return_pct")})
            mem["ab"][name] = {"hypothesis": hid, "parent": account, "since": int(time.time()),
                               "parent_equity_at_start": _equity(account), "own_equity_at_start": 50.0}
            events.append(f"challenger {name} spawned: {account} + {_label(hid)} (hypothesis SUPPORTED, gates+MC pass)")
        else:
            events.append(f"[dry run] would spawn {account} + {_label(hid)}")
    return events


def _spawn(champion, spec, state, stamp, label, evidence):
    # Reuse learner's spawner, then stamp the regime restriction on the new account.
    name = L.spawn_challenger(champion, label, spec, evidence, state)
    st = kp.load(name)
    st.update(stamp)
    kp._save(st)
    state["challengers"][name]["stamp"] = dict(stamp)
    state["challengers"][name].update(stamp)
    return name


def _equity(name):
    try:
        st = kp.load(name)
        return round(sum(sl["cash"] + sl["units"] * sl["last_px"] for sl in st["sleeves"].values()), 2)
    except Exception:
        return None


# ---------------------------------------------------------------- live loop
def learn_from_live(mem, ev, act):
    events = []
    import glob
    for acct, regs in ev.items():
        if not (acct in CHAMPIONS or acct.startswith(("chal_", "c4h_"))):
            continue        # arena is observational only
        try:
            st = kp.load(acct)
        except FileNotFoundError:
            continue
        if st.get("retired"):
            continue
        for reg, s in regs.items():
            if s["n"] >= MIN_LIVE_TRIPS and s["t"] <= T_BLOCK and reg not in st.get("avoid_regimes", []):
                msg = (f"{acct} loses in {reg} live (n={s['n']}, mean {s['mean_pct']}%, t={s['t']}): "
                       f"new entries blocked in {reg}")
                if act:
                    st["avoid_regimes"] = sorted(set(st.get("avoid_regimes", [])) | {reg})
                    kp._save(st)
                    journal.log(acct, "learned_restriction", regime=reg, **s)
                    mem["restrictions"].append({"t": int(time.time()), "account": acct, "regime": reg, **s})
                    _log(LOG, action="learned_restriction", account=acct, regime=reg, **s)
                    events.append(msg)
                else:
                    events.append("[dry run] " + msg)
    return events


def update_ab(mem):
    for name, ab in mem["ab"].items():
        pe, oe = _equity(ab["parent"]), _equity(name)
        if pe is None or oe is None:
            continue
        ab["parent_return_pct"] = round((pe / (ab.get("parent_equity_at_start") or 50.0) - 1) * 100, 2)
        ab["own_return_pct"] = round((oe / 50.0 - 1) * 100, 2)
        ab["days"] = round((time.time() - ab["since"]) / 86400, 1)


def build_lessons(mem, ev):
    out = []
    for hid, h in mem["hypotheses"].items():
        last = h["history"][-1]
        out.append(f"{hid}: {h['status']} ({last['improved']}/{last['tested']} strategies improved in both halves; "
                   f"needs >= {int(MIN_REPLICATION*100)}% of >= {MIN_TESTED} AND more than random-day blocking, "
                   f"which improved {last.get('placebo_improved')}).")
    try:   # context the hypothesis verdicts must be read against
        d = json.load(open(os.path.join(BASE, "logs", "oos_diagnosis.json")))
        gross = {k: v["avg_gross_pct"] for k, v in d["pooled"].items() if v["trips"] >= 20}
        if gross and max(gross.values()) < 0:
            out.append("Caution: in the held-out period the champions' average GROSS move per trip was negative in every regime "
                       f"({', '.join(f'{k.lower()} {v:+.1f}%' for k, v in gross.items())}). Sitting out a regime mostly "
                       "reduces exposure; it does not by itself create edge.")
    except Exception:
        pass
    pooled = defaultdict(list)
    for regs in ev.values():
        for r, s in regs.items():
            pooled[r].append(s)
    for r, ss in sorted(pooled.items()):
        n = sum(s["n"] for s in ss)
        if n:
            mean = sum(s["mean_pct"] * s["n"] for s in ss) / n
            out.append(f"Live, all accounts, entries in {r}: {n} round trips, mean {mean:+.2f}% net.")
    for k in mem.get("killed", [])[-5:]:
        out.append(f"Live failure: {k['account']} paused after {k['n']} trips at {k['mean_pct']}% mean net (t={k['t']}).")
    for r in mem["restrictions"][-5:]:
        out.append(f"Learned from live: {r['account']} blocked in {r['regime']} (n={r['n']}, t={r['t']}).")
    return out


def run(act=True, verbose=True, light=False):
    """light=True: only the LIVE loop (attribute closed paper trades to regimes,
    apply tighten-only restrictions, update A/B) — no backtests, runs in seconds,
    so it can run every hour. light=False adds the historical hypothesis tests."""
    mem = _load()
    state = L._load_state()
    coin_bars = allow = None
    if not light:
        coin_bars, allow = L._market_data(state, verbose)
    btc = (coin_bars or {}).get("BTC-USD") or data.get_bars("BTC-USD", 86400, max_bars=1500)
    labels = regimes.label_map(btc)
    try:   # fresh Kraken daily bars so trades from the last day or two are labelled too
        from . import kraken_data
        kb = [x for x in kraken_data.fetch_recent("BTC-USD", 86400) if x.ts + 86400 <= time.time()]
        labels.update(regimes.label_map(kb))
    except Exception:
        pass
    events = []
    tested = {}
    for hid in ([] if light else HYPOTHESES):
        verdict, results, masked = test_hypothesis(mem, hid, coin_bars, allow, btc, verbose)
        tested[hid] = (verdict, results)
        if verdict == "SUPPORTED":
            events += spawn_from_hypothesis(mem, hid, state, results, coin_bars, masked, act)
    ev = live_evidence(labels)
    mem["live"] = ev
    events += learn_from_live(mem, ev, act)
    pooled = live_pooled()
    mem["live_pooled"] = pooled
    events += retire_failed_experiments(mem, pooled, act)
    update_ab(mem)
    mem["lessons"] = build_lessons(mem, ev)
    mem["runs"] += 1
    mem["light_runs" if light else "full_runs"] = mem.get("light_runs" if light else "full_runs", 0) + 1
    mem["last_run"] = time.strftime("%Y-%m-%d %H:%M")
    mem["last_events"] = events
    if act:
        _save(mem)
        L._save_state(state)
        if events:
            alerts.notify("market_coach regime learner", "\n".join(events[:6]), priority="high", tags=["brain"])
    if verbose:
        for hid, (verdict, results) in tested.items():
            h = mem["hypotheses"][hid]; l = h["history"][-1]
            print(f"H {hid}: {verdict} ({l['improved']}/{l['tested']}; random-day placebo improved {l['placebo_improved']})")
        print("lessons:", *mem["lessons"], sep="\n  ")
        print("actions:", *(events or ["none"]), sep="\n  ")
    return mem, events
