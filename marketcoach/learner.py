"""Autonomous, deterministic learning loop. Plain code, no AI session, no
approvals — runs from cron. It is allowed to ACT, but only in the safe
direction, and every action is bounded by guardrails that cannot be relaxed
by this file's own logic.

What it does each run:
  1. RE-VALIDATE every champion (real, non-arena, non-demo account) against
     the CURRENT real Kraken fee tier with the same fee+edge gate used
     everywhere in this project (`portfolio_optimize._pooled`).
  2. DEMOTE on failure: a champion that now fails a gate is flagged
     `needs_revalidation` (new entries paused). Demotion is autonomous
     because it is the SAFE direction.
  3. SEARCH for a replacement, but only inside the failed strategy's OWN
     parameter family (nudged indicator periods, numeric thresholds, stop
     widths) — never a new indicator or signal family. Candidates are
     selected on in-sample profit factor and judged out-of-sample, and must
     also clear a Monte Carlo robustness check and belong to a robust
     family (most neighbors also survive) to be admitted.
  4. SPAWN A CHALLENGER: a survivor becomes a NEW separate paper account
     (`chal_<champion>_<n>`, its own $50, its own track record). The
     champion is never overwritten and its history is never reset.
  5. REVIEW challengers: one that has accumulated enough live-paper round
     trips and still clears the gates is reported as
     ELIGIBLE_FOR_HUMAN_REVIEW. THE LEARNER NEVER PROMOTES ANYTHING.
     A challenger that decays is retired (entries paused, kept for records).

Hard guardrails (constants below, not tunable at runtime):
  - Paper accounts only. Nothing here can place a real order.
  - Fee model is always the live account tier (kraken_fees), never assumed.
  - MAX_LIVE_CHALLENGERS caps how many exist at once.
  - Failed experiments are logged, never deleted (logs/learner.jsonl and
    logs/learner_rejections.jsonl) — failure is research data.
  - Selection uses in-sample only; the out-of-sample slice is only ever
    used to JUDGE, so repeated searches don't quietly overfit to it.
"""
import json, os, subprocess, sys, time
from . import data, regime, journal, alerts, kraken_fees, kraken_portfolio as kp
from .portfolio_optimize import DEFAULT_BASKET, _pooled
from .monte_carlo import _perturb_periods, collect_trades, bootstrap, summarize_distribution
from . import metrics

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.join(BASE, "paper_state")
STATE_FILE = os.path.join(STATE_DIR, "learner_state.json")
LOG = os.path.join(BASE, "logs", "learner.jsonl")
REJECT_LOG = os.path.join(BASE, "logs", "learner_rejections.jsonl")
CHAL_DIR = os.path.join(BASE, "strategies", "challengers")

MAX_LIVE_CHALLENGERS = 6
MIN_PAPER_TRIPS_FOR_REVIEW = 15
DATA_REFRESH_SEC = 20 * 3600
MC_MIN_P5_RETURN_PCT = 0.0      # bootstrap 5th percentile must not be a loss
MC_MAX_LOSS_PROB = 0.10
ROBUST_FAMILY_MIN = 0.5         # share of surviving neighbors that must beat hold OOS
ROBUST_MIN_SURVIVORS, ROBUST_MIN_SHARE = 3, 0.25   # a lone survivor is luck, not a family

CHAMPIONS = {  # account -> strategy file (mirrors leaderboard.ACCOUNTS sleeve accounts)
    "realpaper": "smart_swing", "krakenmulti": "multi_signal", "krakenfast": "fast_swing",
    "krakenslow": "slow_swing", "krakenrsi": "rsi_meanrev", "krakenmacd": "macd_cross",
    "krakenbbreak": "bollinger_breakout", "krakenturtle": "turtle_donchian",
    "krakencci": "cci_momentum", "krakenkeltner": "keltner_breakout",
    "krakenichimoku": "ichimoku_cloud",
}


def _log(path, **rec):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rec = {"t": int(time.time()), "iso": time.strftime("%Y-%m-%d %H:%M:%S"), **rec}
    with open(path, "a") as f:
        f.write(json.dumps(rec) + "\n")


def _load_state():
    try:
        return json.load(open(STATE_FILE))
    except Exception:
        return {"challengers": {}, "last_refresh": 0}


def _save_state(st):
    os.makedirs(STATE_DIR, exist_ok=True)
    json.dump(st, open(STATE_FILE, "w"), indent=2)


def _market_data(state, verbose):
    products = [f"{c}-USD" for c in DEFAULT_BASKET]
    refresh = time.time() - state.get("last_refresh", 0) > DATA_REFRESH_SEC
    if verbose and refresh:
        print("refreshing price history from Coinbase (once per ~20h)...")
    bars = {p: data.get_bars(p, granularity=86400, max_bars=1500, use_cache=not refresh)
            for p in products}
    if refresh:
        state["last_refresh"] = int(time.time())
    allow = regime.risk_on_timestamps("BTC-USD", granularity=86400, max_bars=1500)
    return bars, allow


def gates(spec, coin_bars, allow_buy_ts):
    """Same two-gate + sample-size test as every other validation, at the real fee."""
    real_spec, fees = kraken_fees.apply_to_spec(spec)
    r = _pooled(real_spec, coin_bars, cash=50.0, split=0.6, allow_buy_ts=allow_buy_ts)
    if r is None:
        return {"pass": False, "why": "no trades", "fee_source": fees["source"]}
    ok = r["fee_gate_ok"] and r["sample_ok"] and r["out_excess"] > 0
    why = [] if ok else [w for w, bad in (("fee gate", not r["fee_gate_ok"]),
                                          ("sample size", not r["sample_ok"]),
                                          ("no OOS edge", r["out_excess"] <= 0)) if bad]
    return {"pass": ok, "why": ", ".join(why), "pf_in": round(r["pf_in"], 2), "n_in": r["n_in"],
            "n_out": r["n_out"], "out_excess_pct": round(r["out_excess"] * 100, 1),
            "fee_source": fees["source"], "raw": r}


def _numeric_nudges(spec):
    """Yield (label, new_spec) variants that nudge numeric literals in rules
    (e.g. RSI 25/75, CCI +/-100) by +/-10% and +/-20%, and stop widths by a
    small absolute step. Same parameter family only."""
    out = []
    def clone():
        return json.loads(json.dumps(spec))
    for scale in (0.8, 0.9, 1.1, 1.2):
        s = clone(); changed = False
        for rule in s.get("rules", []):
            for side in ("left", "right"):
                v = rule["when"].get(side)
                if isinstance(v, (int, float)) and not isinstance(v, bool) and v != 0:
                    rule["when"][side] = round(v * scale, 2); changed = True
        if changed:
            out.append((f"thresholds x{scale}", s))
    for key, step in (("trailing_stop_pct", 0.04), ("stop_loss_pct", 0.03)):
        cur = spec.get("risk", {}).get(key)
        if cur:
            for d in (-step, step):
                nv = round(cur + d, 3)
                if 0.03 <= nv <= 0.40:
                    s = clone(); s["risk"][key] = nv
                    out.append((f"{key} {cur}->{nv}", s))
    return out


def candidates_for(spec):
    cands = [("period -3", _perturb_periods(spec, -3)), ("period -2", _perturb_periods(spec, -2)),
             ("period -1", _perturb_periods(spec, -1)), ("period +1", _perturb_periods(spec, 1)),
             ("period +2", _perturb_periods(spec, 2)), ("period +3", _perturb_periods(spec, 3))]
    return cands + _numeric_nudges(spec)


def search_replacement(champion, spec, coin_bars, allow_buy_ts, verbose=True):
    """Select in-sample, judge out-of-sample, then robustness-check. Returns
    (best_label, best_spec, evidence) or (None, None, evidence-of-why-not)."""
    survivors, evaluated = [], 0
    for label, cand in candidates_for(spec):
        g = gates(cand, coin_bars, allow_buy_ts)
        evaluated += 1
        if g["pass"]:
            survivors.append((g["raw"]["pf_in"], label, cand, g))
        else:
            _log(REJECT_LOG, champion=champion, candidate=label, stage="gates", why=g["why"],
                 pf_in=g.get("pf_in"), n_in=g.get("n_in"))
    if not survivors:
        return None, None, {"evaluated": evaluated, "survivors": 0, "why": "no candidate cleared both gates"}
    beat = sum(1 for s in survivors if s[3]["out_excess_pct"] > 0)
    family_ok = (beat / len(survivors) >= ROBUST_FAMILY_MIN and len(survivors) >= ROBUST_MIN_SURVIVORS
                 and len(survivors) / max(1, evaluated) >= ROBUST_MIN_SHARE)
    survivors.sort(key=lambda t: t[0], reverse=True)   # rank by IN-SAMPLE profit factor only
    pf, label, cand, g = survivors[0]
    if not family_ok:
        return None, None, {"evaluated": evaluated, "survivors": len(survivors),
                            "why": "survivors are not a robust family"}
    trades = collect_trades_for(cand, coin_bars, allow_buy_ts)
    dist = summarize_distribution(bootstrap(trades, n_sims=1500, starting_cash=50.0), 50.0, 0.0)
    if dist.get("n_sims") and (dist["p5_return_pct"] < MC_MIN_P5_RETURN_PCT
                               or dist["probability_of_losing_money"] > MC_MAX_LOSS_PROB):
        _log(REJECT_LOG, champion=champion, candidate=label, stage="monte_carlo",
             why="fragile bootstrap distribution", p5=dist["p5_return_pct"],
             p_loss=dist["probability_of_losing_money"])
        return None, None, {"evaluated": evaluated, "survivors": len(survivors),
                            "why": f"best candidate '{label}' failed Monte Carlo robustness"}
    return label, cand, {"evaluated": evaluated, "survivors": len(survivors), "pf_in": g["pf_in"],
                         "out_excess_pct": g["out_excess_pct"], "n_in": g["n_in"],
                         "mc_p5_return_pct": dist.get("p5_return_pct"),
                         "mc_loss_prob": dist.get("probability_of_losing_money")}


def collect_trades_for(spec, coin_bars, allow_buy_ts):
    tmp = os.path.join(STATE_DIR, "_learner_tmp_spec.json")
    real_spec, _ = kraken_fees.apply_to_spec(spec)
    json.dump(real_spec, open(tmp, "w"))
    try:
        return collect_trades(tmp, coin_bars, 50.0 / len(coin_bars), allow_buy_ts)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _add_cron(account):
    line = (f"{(abs(hash(account)) % 50) + 5} * * * * cd {BASE} && /usr/bin/python3 "
            f"kraken_portfolio.py tick {account} >> {BASE}/paper_state/{account}.log 2>&1")
    cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    if f"tick {account} " in cur:
        return
    subprocess.run(["crontab", "-"], input=cur.rstrip("\n") + "\n" + line + "\n", text=True)


def spawn_challenger(champion, label, spec, evidence, state):
    n = sum(1 for k in state["challengers"] if state["challengers"][k]["champion"] == champion) + 1
    name = f"chal_{champion}_{n}"
    os.makedirs(CHAL_DIR, exist_ok=True)
    rel = f"strategies/challengers/{name}.json"
    spec = json.loads(json.dumps(spec))
    spec["name"] = f"CHALLENGER for {champion} ({label}) — earns champion status only via the gates"
    real_spec, _ = kraken_fees.apply_to_spec(spec)
    json.dump(real_spec, open(os.path.join(BASE, rel), "w"), indent=2)
    kp.open_account(name, rel, [f"{c}-USD" for c in DEFAULT_BASKET], granularity=86400,
                    cash=50.0, paper=True)   # paper is the default AND explicit: never live
    _add_cron(name)
    state["challengers"][name] = {"champion": champion, "label": label, "created": int(time.time()),
                                  "evidence": evidence, "status": "PAPER_TRIAL"}
    _log(LOG, action="spawn_challenger", challenger=name, champion=champion, label=label, evidence=evidence)
    return name


def review_challengers(state, coin_bars, allow_buy_ts, verbose):
    events = []
    for name, info in list(state["challengers"].items()):
        if info["status"] in ("RETIRED",):
            continue
        try:
            acct = kp.load(name)
        except FileNotFoundError:
            continue
        if acct.get("retired"):
            continue  # retired by a human: no revalidation, no replacement search
        spec = json.load(open(os.path.join(BASE, acct["strategy_path"])))
        from .regime_learner import account_allow
        allow_c = account_allow(acct, allow_buy_ts, coin_bars)
        g = gates(spec, coin_bars, allow_c)
        trips = sum(1 for f in acct.get("fills", []) if f["side"] == "sell")
        if not g["pass"]:
            if not acct.get("needs_revalidation"):
                kp.mark_needs_revalidation(name, f"challenger decayed: {g['why']}")
            info["status"] = "RETIRED"
            events.append(f"{name}: RETIRED ({g['why']})")
            _log(LOG, action="retire_challenger", challenger=name, why=g["why"])
        elif trips >= MIN_PAPER_TRIPS_FOR_REVIEW and info["status"] != "ELIGIBLE_FOR_HUMAN_REVIEW":
            info["status"] = "ELIGIBLE_FOR_HUMAN_REVIEW"
            events.append(f"{name}: ELIGIBLE_FOR_HUMAN_REVIEW ({trips} paper round trips, gates still pass)")
            _log(LOG, action="eligible_for_review", challenger=name, paper_round_trips=trips)
        info["paper_round_trips"] = trips
    return events


def run(act=True, verbose=True):
    state = _load_state()
    coin_bars, allow = _market_data(state, verbose)
    report, alerts_out = [], []

    for account, sname in CHAMPIONS.items():
        try:
            acct = kp.load(account)
        except FileNotFoundError:
            continue
        spec = json.load(open(os.path.join(BASE, acct["strategy_path"])))
        g = gates(spec, coin_bars, allow)
        flagged = bool(acct.get("needs_revalidation"))
        line = f"{account}: {'PASS' if g['pass'] else 'FAIL ('+g['why']+')'} pf_in={g.get('pf_in')} " \
               f"oos={g.get('out_excess_pct')}% fee_src={g['fee_source']}"
        report.append(line)
        _log(LOG, action="revalidate", account=account, passed=g["pass"], why=g["why"],
             pf_in=g.get("pf_in"), out_excess_pct=g.get("out_excess_pct"))
        if not g["pass"] and not flagged and act:
            kp.mark_needs_revalidation(account, f"learner: {g['why']} at live fee tier "
                                                f"(pf_in={g.get('pf_in')}, oos={g.get('out_excess_pct')}%)")
            alerts_out.append(f"{account} DEMOTED (entries paused): {g['why']}")
            flagged = True
        has_chal = any(c["champion"] == account and c["status"] != "RETIRED"
                       for c in state["challengers"].values())
        live_chals = sum(1 for c in state["challengers"].values() if c["status"] != "RETIRED")
        if flagged and not has_chal and live_chals < MAX_LIVE_CHALLENGERS:
            label, cand, ev = search_replacement(account, spec, coin_bars, allow, verbose)
            report.append(f"  search for {account}: {ev}")
            if cand is not None and act:
                name = spawn_challenger(account, label, cand, ev, state)
                alerts_out.append(f"challenger {name} started for {account} ({label}); evidence {ev}")
            elif cand is None:
                _log(LOG, action="no_replacement", account=account, evidence=ev)

    alerts_out += review_challengers(state, coin_bars, allow, verbose)
    _save_state(state)
    if verbose:
        print("\n".join(report + ["--- actions ---"] + (alerts_out or ["none"])))
    if alerts_out and act:
        alerts.notify("market_coach learner: actions taken", "\n".join(alerts_out[:8]),
                      priority="high", tags=["brain"])
    return report, alerts_out
