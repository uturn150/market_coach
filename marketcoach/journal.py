"""Structured logging so the system can learn from its own history. Every fill,
every moon detection, every tick summary is one JSON line in logs/<account>.jsonl.
Plain JSONL = easy to tail, grep, or load back for analysis later.
"""
import json, os, time

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")


def log(stream, event, **fields):
    """Append one event. `stream` is the log file stem (usually the account name)."""
    os.makedirs(LOG_DIR, exist_ok=True)
    rec = {"t": int(time.time()), "iso": time.strftime("%Y-%m-%d %H:%M:%S"),
           "event": event, **fields}
    with open(os.path.join(LOG_DIR, f"{stream}.jsonl"), "a") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def read(stream, event=None, limit=None):
    """Load events back (newest last). Optionally filter by event type."""
    path = os.path.join(LOG_DIR, f"{stream}.jsonl")
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if event is None or r.get("event") == event:
                out.append(r)
    return out[-limit:] if limit else out


def tail(stream, n=20):
    for r in read(stream, limit=n):
        extra = " ".join(f"{k}={v}" for k, v in r.items() if k not in ("t", "iso", "event"))
        print(f"{r['iso']}  {r['event']:12} {extra}")
