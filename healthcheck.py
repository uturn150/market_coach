#!/usr/bin/env python3
"""Server-side health check: plain Python, no Claude, no approvals — meant for
cron every 2 hours. DETECTS problems and reports them (Telegram + a log file);
it never edits code, strategies, or account state.

Checks: cron entries exist for every account, each account ticked recently,
recent tracebacks in logs, halted / needs-revalidation / paused accounts,
webapp health, fee-tier cache freshness/source, and new trades since last run.

Telegram: sent immediately when something is wrong; otherwise one short
"all OK" digest per day (so a quiet system isn't silent for days, but isn't
noisy every 2 hours either).
"""
import glob, json, os, subprocess, sys, time, urllib.request
BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
from marketcoach import journal, alerts

STATE_DIR = os.path.join(BASE, "paper_state")
STATE_FILE = os.path.join(STATE_DIR, "healthcheck_state.json")
LOG_FILE = os.path.join(BASE, "logs", "healthcheck.log")
STALE_TICK_SEC = 3 * 3600   # per-account cron runs hourly; >3h silent = broken
ALWAYS_ON = ["bar_watcher.py", "risk_monitor.py", "research_pipeline.py", "reconcile.py"]


def accounts():
    out = []
    for p in sorted(glob.glob(os.path.join(STATE_DIR, "*.kraken.json"))):
        out.append((os.path.basename(p)[:-len(".kraken.json")], "sleeve", p))
    for p in sorted(glob.glob(os.path.join(STATE_DIR, "*.kraken_moon.json"))):
        out.append((os.path.basename(p)[:-len(".kraken_moon.json")], "moon", p))
    return out


def tail(path, n=80):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 60_000))
            return f.read().decode("utf-8", "replace").splitlines()[-n:]
    except OSError:
        return []


def main():
    problems, notes = [], []
    now = time.time()
    prev = {}
    if os.path.exists(STATE_FILE):
        try:
            prev = json.load(open(STATE_FILE))
        except Exception:
            prev = {}

    # 1. cron entries
    cron = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    for job in ALWAYS_ON:
        if job not in cron:
            problems.append(f"cron: no entry for {job}")
    accts = accounts()
    for name, kind, _ in accts:
        if f"tick {name}" not in cron and f" {name} " not in cron + " ":
            problems.append(f"cron: no tick entry for {name}")

    # 2-4. per-account: staleness, flags, trades
    fills_now = {}
    for name, kind, path in accts:
        try:
            s = json.load(open(path))
        except Exception as e:
            problems.append(f"{name}: state file unreadable ({type(e).__name__})")
            continue
        fills_now[name] = len(s.get("fills", []))
        ticks = journal.read(name, event="tick")
        if not ticks:
            problems.append(f"{name}: no tick ever logged")
        elif now - ticks[-1]["t"] > STALE_TICK_SEC:
            problems.append(f"{name}: last tick {(now - ticks[-1]['t'])/3600:.1f}h ago (stale)")
        if s.get("halted"):
            problems.append(f"{name}: HALTED (circuit breaker) — needs a human reset decision")
        if s.get("needs_revalidation"):
            notes.append(f"{name}: needs_revalidation ({s.get('revalidation_reason')})")
        if s.get("paused_until", 0) > now:
            notes.append(f"{name}: entries paused by loss cap")
        if s.get("paper") is not True:
            problems.append(f"{name}: paper flag is NOT true — investigate immediately")

    # 5. tracebacks in recent log tails
    for log in ["bar_watcher.log", "risk_monitor.log"] + [f"{n}.log" for n, _, _ in accts]:
        p = os.path.join(STATE_DIR, log)
        if os.path.exists(p) and now - os.path.getmtime(p) < 3 * 3600:
            hits = [l for l in tail(p) if "Traceback" in l or "Error" in l.split(":")[0]]
            if hits:
                problems.append(f"log {log}: {len(hits)} error line(s) recently, e.g. {hits[-1][:120]}")

    # 6. webapp
    try:
        urllib.request.urlopen("http://127.0.0.1:8015/healthz", timeout=5).read()
    except Exception as e:
        problems.append(f"webapp on :8015 not answering ({type(e).__name__})")

    # 7. fee tier cache
    fp = os.path.join(STATE_DIR, "fee_tier_cache.json")
    try:
        fee = next(iter(json.load(open(fp)).values()))
        if fee.get("source") != "live":
            problems.append(f"fee model on FALLBACK ({fee.get('fallback_reason')}) — validation uses conservative default")
        notes.append(f"fees: taker {fee['taker_bps']}bps / maker {fee['maker_bps']}bps ({fee.get('source')})")
    except Exception:
        notes.append("fees: cache not present yet")

    # 7b. record today's market regime label once per day (live history for
    #     attributing real paper P&L to regimes as it accumulates)
    try:
        from marketcoach import regimes
        cur = regimes.record_daily()
        notes.append(f"regime: {cur['label']} ({cur['days_in_regime']}d)")
    except Exception as e:
        notes.append(f"regime label unavailable ({type(e).__name__})")

    # 8. new trades since last run
    prev_fills = prev.get("fills", {})
    new_trades = {n: c - prev_fills.get(n, c) for n, c in fills_now.items() if c > prev_fills.get(n, c)}
    if new_trades:
        notes.append("new trades since last check: " + ", ".join(f"{n} +{c}" for n, c in new_trades.items()))

    status = "PROBLEMS" if problems else "OK"
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"[{stamp}] healthcheck {status}: {len(accts)} accounts, {sum(fills_now.values())} fills total"]
    lines += [f"  PROBLEM {p}" for p in problems] + [f"  note {n}" for n in notes]
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))

    today = time.strftime("%Y-%m-%d")
    if problems:
        alerts.notify("market_coach: healthcheck found problems", "\n".join(problems[:8]),
                      priority="high", tags=["warning"])
    elif prev.get("last_ok_digest") != today:
        alerts.notify("market_coach: daily healthcheck OK",
                      f"{len(accts)} accounts ticking, {sum(fills_now.values())} fills total. "
                      + "; ".join(notes[:4]), priority="low", tags=["white_check_mark"])
        prev["last_ok_digest"] = today

    prev["fills"] = fills_now
    prev["last_run"] = int(now)
    json.dump(prev, open(STATE_FILE, "w"))


if __name__ == "__main__":
    main()
