"""Resumable 1-minute candle downloader worker.  python3 minute_downloader.py <worker> <n_workers>
Each worker takes every n-th month of the (newest-first, round-robin) plan. Safe to kill and restart."""
import json, os, sys, time
from marketcoach import minute_data as md

w = int(sys.argv[1]) if len(sys.argv) > 1 else 0
n = int(sys.argv[2]) if len(sys.argv) > 2 else 1
todo = md.plan()[w::n]
print(f"worker {w}/{n}: {len(todo)} months to fetch", flush=True)
t0 = time.time()
for i, (p, y, m) in enumerate(todo):
    c = md.download_month(p, y, m)
    st = md.status()
    st.update(last=f"{p} {y}-{m:02d}", candles=c, worker=w, updated=time.strftime("%Y-%m-%d %H:%M:%S"))
    json.dump(st, open(md.STATUS, "w"))
    print(f"{st['updated']} w{w} {p} {y}-{m:02d}: {c} candles | {st['pct']}% done ({st['remaining']} left)", flush=True)
print("worker done", flush=True)
