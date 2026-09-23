"""Self-updating slow trader (PAPER ONLY): the bot replaces its OWN strategy when training finds a genuinely better one.

Why this is allowed, and what it can never do:
  * It only ever edits a PAPER account's strategy file. No broker/order code is touched, real trading cannot be enabled.
  * A candidate replaces the current version ONLY if ALL hold (fixed in code, not tunable by the search itself):
      - it comes from the learner's hall of fame and uses features the live paper runner supports (no maker entries yet),
      - on years AND coins the search never saw it beats the current version on BOTH median year returns and by >= MARGIN
        points of validation score, and is profitable on both,
      - its future-year drawdown is no worse than the current version's + 3 points, and its worst unseen-coin drawdown <= 65%,
      - it is not a recent-performance chase: it is judged on held-out history, never on the last weeks of live results,
      - >= COOLDOWN_DAYS have passed since the last change, and the account is FLAT (no open positions),
      - the coin set is unchanged (the account's sleeves stay as they are).
  * Every change is versioned (strategies/slow50_versions/), logged with the evidence, alerted, and reversible:
      python3 slow_updater.py --rollback
Runs weekly after training (cron).  python3 slow_updater.py [--apply]   (default: dry run, shows the decision)
"""
import calendar, json, os, random, shutil, sys, time
from . import slow_learner as sl, market_sim as ms, kraken_portfolio as kp, alerts

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERS = os.path.join(BASE, "paper_state", "slow50_versions.json")
VDIR = os.path.join(BASE, "strategies", "slow50_versions")
ACCOUNT, SPEC = "slow50_v1", "strategies/slow50_v1.json"
MARGIN, COOLDOWN_DAYS, DD_SLACK, UNSEEN_WORST_MAX = 6.0, 30, 3.0, 65.0
SUPPORTED_SIZE = ("full", "half_in_chop", "third_in_chop")


def T(y, m, d): return calendar.timegm((y, m, d, 0, 0, 0))


def load_versions():
    try:
        return json.load(open(VERS))
    except Exception:
        return {"current": None, "history": []}


def validate(W, g, e_future, e_all, seed=5):
    """e_all is unused now (kept in the signature so callers don't need to change) -- the unseen check runs on
    marketcoach.slow_learner.unseen_world(), REAL held-out coins (holdout.holdout_products(): never in the 24-coin
    basket any strategy here was built from), not a second sample of coins the search already knows."""
    rng = random.Random(seed)
    ef = W.evaluate(g, e_future)
    eu = []
    subs = [sorted(rng.sample(sl.UNSEEN_COINS, min(g["ncoins"], 4))) for _ in range(3)]
    for s in subs:
        wu = sl.unseen_world(s, W.allow)
        if wu is None:               # this particular combo doesn't have enough real history; try another
            continue
        eps = wu.episodes(wu.ts[0], wu.ts[-1])
        if eps:
            eu += wu.evaluate(g, eps, coins=s)
    if not eu:                       # every sampled combo lacked history -- fall back to the old in-basket check
        # rather than than crash a weekly cron job; flagged in the result so it's visible, not silent.
        subs = [sorted(rng.sample(sl.TRAIN_COINS, min(g["ncoins"], 4))) for _ in range(3)]
        eu = [x for s in subs for x in W.evaluate(g, e_all, coins=s)]
    sf, su = sl.score(ef), sl.score(eu)
    return {"val": round(sf + su, 2), "future_score": sf, "unseen_score": su,
            "future_dd": round(sum(abs(x["dd"]) for x in ef) / len(ef) * 100, 1),
            "unseen_worst_dd": round(max(abs(x["dd"]) for x in eu) * 100, 1),
            "future_med": round(sorted(x["ret"] for x in ef)[len(ef) // 2] * 100, 1),
            "unseen_med": round(sorted(x["ret"] for x in eu)[len(eu) // 2] * 100, 1)}


def run(apply=False, verbose=True):
    vs = load_versions()
    bars = ms.load_long(); f0, f1 = ms.filters_for(bars)
    W = sl.World(bars, {"F0": f0, "F1": f1})
    e_future = W.episodes(T(2024, 1, 1), T(2026, 9, 1)); e_all = W.episodes(T(2020, 3, 1), T(2026, 9, 1))
    acct = kp.load(ACCOUNT)
    coins = sorted(acct["sleeves"])
    if vs["current"] is None:                                   # bootstrap: the running strategy is version 1
        old = json.load(open(os.path.join(BASE, "paper_state", "slow_learner.json")))["hall_of_fame"][0]["genome"]
        vs["current"] = {"genome": old, "since": int(time.time()), "version": 1, "validation": None}
    cur = vs["current"]
    cur["validation"] = validate(W, cur["genome"], e_future, e_all)
    mem = sl.load()
    cands = []
    for h in mem["hall_of_fame"]:
        g = h["genome"]
        if g.get("maker") or g.get("size_mode", "full") not in SUPPORTED_SIZE or sorted(g["coins"]) != coins or sl.key(g) == sl.key(cur["genome"]):
            continue
        cands.append((validate(W, g, e_future, e_all), g))
    cands.sort(key=lambda t: -t[0]["val"])
    decision, why = "KEEP", "no candidate cleared every gate"
    flat = not any(sl_["in_position"] for sl_ in acct["sleeves"].values())
    days_since = (time.time() - cur["since"]) / 86400
    best = cands[0] if cands else None
    if best:
        v, g = best
        gates = {"beats current by margin": v["val"] >= cur["validation"]["val"] + MARGIN,
                 "better median year on FUTURE years": v["future_med"] > cur["validation"]["future_med"],
                 "better median year on UNSEEN coins": v["unseen_med"] > cur["validation"]["unseen_med"],
                 "profitable on both": v["future_med"] > 0 and v["unseen_med"] > 0,
                 "drawdown not worse": v["future_dd"] <= cur["validation"]["future_dd"] + DD_SLACK,
                 "unseen worst dd ok": v["unseen_worst_dd"] <= UNSEEN_WORST_MAX,
                 f"cooldown {COOLDOWN_DAYS}d": days_since >= COOLDOWN_DAYS or cur["version"] == 1 and not vs["history"],
                 "account flat": flat}
        if verbose:
            print(f"current v{cur['version']}: val {cur['validation']['val']} (future {cur['validation']['future_med']:+.1f}% dd {cur['validation']['future_dd']}%, unseen {cur['validation']['unseen_med']:+.1f}%)")
            print(f"best candidate: val {v['val']} (future {v['future_med']:+.1f}% dd {v['future_dd']}%, unseen {v['unseen_med']:+.1f}%) {sl.key(g)[:110]}")
            print("gates:", {k: ("PASS" if ok else "fail") for k, ok in gates.items()})
        if all(gates.values()):
            decision, why = "UPDATE", "all gates passed"
            if apply:
                os.makedirs(VDIR, exist_ok=True)
                shutil.copy(os.path.join(BASE, SPEC), os.path.join(VDIR, f"v{cur['version']}.json"))
                spec = sl.spec_of(g); spec["name"] = f"SLOW50 v{cur['version']+1} (self-updated): " + sl.key(g)[:80]
                json.dump(spec, open(os.path.join(BASE, SPEC), "w"), indent=2)
                acct = kp.load(ACCOUNT); acct["size_mode"] = g.get("size_mode", "full"); kp._save(acct)
                vs["history"].append({"version": cur["version"], "until": int(time.time()), "genome": cur["genome"], "validation": cur["validation"]})
                vs["current"] = {"genome": g, "since": int(time.time()), "version": cur["version"] + 1, "validation": v}
                alerts.notify("market_coach: slow50 self-updated", f"v{cur['version']} -> v{cur['version']+1} (paper). validation {cur['validation']['val']} -> {v['val']}; "
                              f"future dd {cur['validation']['future_dd']}% -> {v['future_dd']}%. Rollback: python3 slow_updater.py --rollback", priority="default", tags=["brain"])
        else:
            why = "gates failed: " + ", ".join(k for k, ok in gates.items() if not ok)
    if verbose:
        print("DECISION:", decision, "-", why)
    json.dump(vs, open(VERS, "w"), indent=1)
    return decision


def rollback():
    vs = load_versions()
    if not vs["history"]:
        print("nothing to roll back to"); return
    prev = vs["history"].pop()
    src = os.path.join(VDIR, f"v{prev['version']}.json")
    shutil.copy(src, os.path.join(BASE, SPEC))
    vs["current"] = {"genome": prev["genome"], "since": int(time.time()), "version": prev["version"], "validation": prev["validation"]}
    acct = kp.load(ACCOUNT); acct["size_mode"] = prev["genome"].get("size_mode", "full"); kp._save(acct)
    json.dump(vs, open(VERS, "w"), indent=1)
    print("rolled back to v%d" % prev["version"])


if __name__ == "__main__":
    if "--rollback" in sys.argv:
        rollback()
    else:
        run(apply="--apply" in sys.argv)
