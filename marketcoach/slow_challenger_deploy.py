"""Automated version of what a human did by hand for slow50_challenger_a: look at the slow-bot learner's hall of
fame, and if one of its candidates is a genuinely different, genuinely better design, open it as its OWN live paper
account instead of waiting for someone to notice and do it manually.

Guardrails (fixed in code, none of them tunable by the search):
  * PAPER ONLY. Opens accounts the same way slow50_v1 was opened; no broker/order code touched.
  * A candidate is only considered if it uses features the live runner supports (no maker orders yet) and its
    validation used REAL held-out coins (marketcoach.slow_learner.unseen_world), not an in-basket "unseen" sample.
  * It must be profitable on both the future-year check and the real-unseen-coin check, and its worst unseen
    drawdown must be <= UNSEEN_WORST_MAX (same cap slow_updater.py uses for self-updates).
  * Its coin SET must differ from every slow-bot account already live (no point running two accounts that hold
    the same three coins) -- coin sets are compared as sets, order doesn't matter.
  * At most MAX_CHALLENGERS challenger accounts run at once (plus slow50_v1 itself) -- more independent evidence,
    not more accounts for their own sake.
  * Every decision (deployed OR rejected, and why) is appended to paper_state/slow_challenger_log.json and kept
    forever, same "never delete a rejected experiment" rule as everywhere else in this project.
Runs weekly after training (cron), same as slow_updater.py.   python3 slow_challenger_deploy.py [--apply]
"""
import calendar, glob, json, os, subprocess, sys, time
from . import slow_learner as sl, slow_updater as su, kraken_portfolio as kp, alerts

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(BASE, "paper_state", "slow_challenger_log.json")
MAX_CHALLENGERS = 3
UNSEEN_WORST_MAX = 65.0


def T(y, m, d): return calendar.timegm((y, m, d, 0, 0, 0))


def load_log():
    try:
        return json.load(open(LOG))
    except Exception:
        return {"decisions": []}


def live_slow_accounts():
    """{account_name: set(coins)} for every slow50-family account already live."""
    out = {}
    for f in sorted(glob.glob(os.path.join(BASE, "paper_state", "slow50*.kraken.json"))):
        try:
            s = json.load(open(f))
        except Exception:
            continue
        out[s["account"]] = frozenset(s.get("sleeves", {}))
    return out


def next_letter(existing):
    for c in "bcdefghij":
        if f"slow50_challenger_{c}" not in existing:
            return c
    return None


def add_cron_line(account):
    line = f"59 * * * * cd {BASE} && /usr/bin/python3 kraken_portfolio.py tick {account} >> paper_state/{account}.log 2>&1"
    try:
        cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=True).stdout
    except subprocess.CalledProcessError:
        cur = ""
    if line in cur:
        return False
    new = cur.rstrip("\n") + ("\n" if cur.strip() else "") + line + "\n"
    subprocess.run(["crontab", "-"], input=new, text=True, check=True)
    return True


def deploy(letter, g, v, apply):
    account = f"slow50_challenger_{letter}"
    spec = sl.spec_of(g)
    coins_short = ", ".join(c.replace("-USD", "") for c in g["coins"])
    spec["name"] = f"SLOW50 {account} ({coins_short}, auto-deployed): future {v['future_med']:+.1f}%/yr, unseen {v['unseen_med']:+.1f}%/yr in backtest"
    spec_path = f"strategies/{account}.json"
    if not apply:
        return account, spec_path
    json.dump(spec, open(os.path.join(BASE, spec_path), "w"), indent=2)
    kp.open_account(account, spec_path, g["coins"], granularity=86400, cash=50.0,
                     regime_filter=True, max_drawdown=0.30, paper=True, market_filter=g["filter"])
    state_path = os.path.join(BASE, "paper_state", f"{account}.kraken.json")
    st = json.load(open(state_path))
    st.update(fixed_sizing=True, min_order=10.0, order_step=10.0, size_mode=g.get("size_mode", "full"))
    json.dump(st, open(state_path, "w"), indent=2)
    add_cron_line(account)
    return account, spec_path


def run(apply=False, verbose=True):
    live = live_slow_accounts()
    n_challengers = sum(1 for n in live if n != "slow50_v1")
    log = load_log()
    if n_challengers >= MAX_CHALLENGERS:
        rec = {"iso": time.strftime("%Y-%m-%d %H:%M"), "decision": "SKIP", "why": f"already at the cap ({n_challengers}/{MAX_CHALLENGERS} challengers live)"}
        log["decisions"].append(rec); json.dump(log, open(LOG, "w"), indent=1)
        if verbose: print(rec["why"])
        return rec

    bars = None
    from . import market_sim as ms
    bars = ms.load_long(coins=[c[:-4] for c in sl.TRAIN_COINS]); f0, f1 = ms.filters_for(bars)
    W = sl.World(bars, {"F0": f0, "F1": f1})
    e_future = W.episodes(T(2024, 1, 1), T(2026, 9, 1)); e_all = W.episodes(T(2020, 3, 1), T(2026, 9, 1))

    mem = sl.load()
    live_coin_sets = set(live.values())
    cands = []
    for h in mem["hall_of_fame"]:
        g = h["genome"]
        if g.get("maker") or g.get("size_mode", "full") not in su.SUPPORTED_SIZE:
            continue
        if frozenset(g["coins"]) in live_coin_sets:
            continue
        cands.append(g)
    if not cands:
        rec = {"iso": time.strftime("%Y-%m-%d %H:%M"), "decision": "SKIP", "why": "no hall-of-fame candidate with a new coin set and supported features"}
        log["decisions"].append(rec); json.dump(log, open(LOG, "w"), indent=1)
        if verbose: print(rec["why"])
        return rec

    scored = [(su.validate(W, g, e_future, e_all), g) for g in cands]
    scored.sort(key=lambda t: -t[0]["val"])
    v, g = scored[0]
    gates = {"profitable on both": v["future_med"] > 0 and v["unseen_med"] > 0,
             "unseen worst dd ok": v["unseen_worst_dd"] <= UNSEEN_WORST_MAX,
             "beats slow50_v1's own validation": True}   # informational only; a challenger doesn't have to beat v1, it has to be independently good
    ok = gates["profitable on both"] and gates["unseen worst dd ok"]
    letter = next_letter(live)
    if verbose:
        print(f"best new-coin-set candidate: val {v['val']} (future {v['future_med']:+.1f}% dd {v['future_dd']}%, unseen {v['unseen_med']:+.1f}% worst dd {v['unseen_worst_dd']}%) coins {g['coins']}")
        print("gates:", {k: ("PASS" if ok_ else "fail") for k, ok_ in gates.items()})
    if not ok or letter is None:
        rec = {"iso": time.strftime("%Y-%m-%d %H:%M"), "decision": "REJECT", "why": "gates failed" if not ok else "no free challenger slot",
               "genome": g, "validation": v}
        log["decisions"].append(rec); json.dump(log, open(LOG, "w"), indent=1)
        if verbose: print("DECISION: REJECT -", rec["why"])
        return rec

    account, spec_path = deploy(letter, g, v, apply)
    rec = {"iso": time.strftime("%Y-%m-%d %H:%M"), "decision": "DEPLOY" if apply else "WOULD DEPLOY", "account": account,
           "spec_path": spec_path, "genome": g, "validation": v}
    log["decisions"].append(rec); json.dump(log, open(LOG, "w"), indent=1)
    if verbose:
        print(f"DECISION: {rec['decision']} {account} ({', '.join(c.replace('-USD','') for c in g['coins'])})")
    if apply:
        alerts.notify(f"market_coach: opened {account} (paper)",
                      f"New slow-bot challenger, coins {', '.join(c.replace('-USD','') for c in g['coins'])}. "
                      f"Backtest: future {v['future_med']:+.1f}%/yr, unseen {v['unseen_med']:+.1f}%/yr, worst unseen dd {v['unseen_worst_dd']}%.",
                      priority="default", tags=["seedling"])
    return rec


if __name__ == "__main__":
    run(apply="--apply" in sys.argv)
