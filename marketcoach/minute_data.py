"""1-minute candle store for the market simulator.

Source: Coinbase Exchange public candles (GET /products/{id}/candles, granularity=60, max 300 rows/request).
Layout: data/minute/<PRODUCT>/<YYYY-MM>.bin.z  = zlib(struct '<I5f' rows: ts, open, high, low, close, volume), ascending.
The downloader is RESUMABLE and IDEMPOTENT: a month file is only written once complete (or once the coin's real
history for that month is exhausted); a checkpoint of finished months is in logs/minute_download.json. Politeness:
~4-5 requests/second, exponential back-off on 429/5xx. Newest months first, round-robin across coins, so the most
recent year of every coin is usable early. Public market data only: no keys, no account, no orders.
"""
import calendar, json, os, struct, sys, time, urllib.request, urllib.error, zlib

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(BASE, "data", "minute")
STATUS = os.path.join(BASE, "logs", "minute_download.json")
UNIVERSE = os.path.join(BASE, "logs", "coin_universe.json")
ROW = struct.Struct("<I5f")
YEARS = 5
REQ_GAP = 0.22          # seconds between requests (~4.5/s; Coinbase public limit is 10/s)


def universe(min_years=5):
    u = json.load(open(UNIVERSE))
    return [p for p, v in u.items() if v.get("years", 0) >= min_years]


def month_range(product):
    """(first_month, last_month) as (year, month) for this coin: max(listing, now - YEARS) .. current month."""
    u = json.load(open(UNIVERSE))[product]
    y0, m0, _ = (int(x) for x in u["start"].split("-"))
    now = time.gmtime()
    cut = (now.tm_year - YEARS, now.tm_mon)
    first = max((y0, m0), cut)
    return first, (now.tm_year, now.tm_mon)


def months(first, last):
    y, m = first
    out = []
    while (y, m) <= last:
        out.append((y, m))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def _path(product, y, m):
    return os.path.join(DIR, product, f"{y:04d}-{m:02d}.bin.z")


def _fetch(product, start, end):
    url = (f"https://api.exchange.coinbase.com/products/{product}/candles?granularity=60&"
           f"start={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(start))}&end={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(end))}")
    delay = 1.0
    for attempt in range(8):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "marketcoach-sim/0.1"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(delay); delay = min(delay * 2, 60); continue
            if e.code == 404:
                return []
            raise
        except Exception:
            time.sleep(delay); delay = min(delay * 2, 60)
    raise RuntimeError(f"gave up on {product} {start}")


def download_month(product, y, m):
    t0 = calendar.timegm((y, m, 1, 0, 0, 0))
    t1 = calendar.timegm((y + (m == 12), 1 if m == 12 else m + 1, 1, 0, 0, 0))
    t1 = min(t1, int(time.time()) // 60 * 60)
    rows = {}
    t = t0
    while t < t1:
        end = min(t + 300 * 60, t1)
        for c in _fetch(product, t, end - 60):            # [time, low, high, open, close, volume]
            rows[int(c[0])] = (int(c[0]), float(c[3]), float(c[2]), float(c[1]), float(c[4]), float(c[5]))
        t = end
        time.sleep(REQ_GAP)
    data = b"".join(ROW.pack(*rows[k]) for k in sorted(rows))
    os.makedirs(os.path.dirname(_path(product, y, m)), exist_ok=True)
    tmp = _path(product, y, m) + ".tmp"
    open(tmp, "wb").write(zlib.compress(data, 6))
    os.replace(tmp, _path(product, y, m))
    return len(rows)


def load_month(product, y, m):
    p = _path(product, y, m)
    if not os.path.exists(p):
        return []
    raw = zlib.decompress(open(p, "rb").read())
    return [ROW.unpack_from(raw, i) for i in range(0, len(raw), ROW.size)]


def plan():
    """Every (product, y, m) still to fetch, newest month first, round-robin over coins."""
    per = {}
    for p in universe():
        first, last = month_range(p)
        per[p] = [ym for ym in reversed(months(first, last)) if not os.path.exists(_path(p, *ym)) or ym == last]
    todo, k = [], 0
    while any(per.values()):
        for p in per:
            if per[p]:
                y, m = per[p].pop(0)
                todo.append((p, y, m))
    return todo


def status():
    todo = plan()
    total = sum(len(months(*month_range(p))) for p in universe())
    done = total - len(todo)
    return {"months_total": total, "months_done": done, "pct": round(100 * done / total, 1), "remaining": len(todo)}


def run(max_months=None, log=True):
    todo = plan()
    n = 0
    t_start = time.time()
    for p, y, m in todo:
        c = download_month(p, y, m)
        n += 1
        st = {"last": f"{p} {y}-{m:02d}", "candles": c, "elapsed_s": round(time.time() - t_start), **status(), "updated": time.strftime("%Y-%m-%d %H:%M:%S")}
        os.makedirs(os.path.dirname(STATUS), exist_ok=True)
        json.dump(st, open(STATUS, "w"))
        if log:
            print(f"{st['updated']} {p} {y}-{m:02d}: {c} candles | {st['pct']}% ({st['remaining']} months left)", flush=True)
        if max_months and n >= max_months:
            break
    return n


if __name__ == "__main__":
    run(int(sys.argv[1]) if len(sys.argv) > 1 else None)
