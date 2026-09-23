"""Pump-and-dump catcher: buy on a fast price+volume spike (a real 'pump'), exit on a trailing stop, a hard stop, or a time
limit (whichever first) -- the mechanical version of "spot it and take advantage of it". Real fees (taker in, taker out --
chasing a pump is never a resting order). Minute data.

Method: precompute each coin's K-minute return and volume-vs-60min-average ratio ONCE, then grid-search entry/exit
parameters (cheap re-use of the precomputed arrays) on the 9 TUNED coins over 2024-2025, pick the best few by total
return, then check them out-of-sample on: (a) the same 9 coins' MOST RECENT year (2025-09 to now, never used in the
search) and (b) all 18 UNSEEN coins over the full window. A design only counts if it survives both.
    python3 pump_lab.py -> logs/pump_lab.json"""
import calendar, itertools, json, os, time
from marketcoach import minute_data as md
BASE = os.path.dirname(os.path.abspath(__file__))
T = lambda y, m, d: calendar.timegm((y, m, d, 0, 0, 0))
K = 15             # minutes of look-back for the "pump" return
VOLW = 60          # minutes averaged for the volume baseline
STOP = 0.06        # hard stop, fraction below entry
MAXHOLD = 180      # minutes, force-exit if neither stop nor trail has fired
TAKER = 0.008; SPREAD_SLIP = 0.0008   # your real taker fee + a conservative spread/slippage haircut, each side
GRID = {"entry_move": (0.02, 0.035, 0.05, 0.08), "vol_mult": (3, 6, 10), "trail": (0.01, 0.02, 0.035)}
SET_A, SET_B = md.universe()[:9], md.universe()[9:]


def load_series(coin, first_month, last_month):
    ts, close, vol = [], [], []
    for y, m in md.months(first_month, last_month):
        try:
            rows = md.load_month(coin, y, m)
        except Exception:
            continue
        for t, o, h, l, c, v in rows:
            ts.append(t); close.append(c); vol.append(v)
    return ts, close, vol


def precompute(ts, close, vol):
    n = len(close)
    ret_k = [None] * n
    vsum = 0.0
    vavg = [None] * n
    for i in range(n):
        vsum += vol[i]
        if i >= VOLW: vsum -= vol[i - VOLW]
        if i >= VOLW - 1: vavg[i] = vsum / VOLW
        if i >= K and close[i - K] > 0:
            ret_k[i] = close[i] / close[i - K] - 1
    return ret_k, vavg


def simulate(ts, close, vol, ret_k, vavg, entry_move, vol_mult, trail):
    n = len(close); cash = 1.0; trips = []
    i = K
    while i < n:
        if ret_k[i] is not None and vavg[i] and vol[i] >= vol_mult * vavg[i] and ret_k[i] >= entry_move:
            entry_px = close[i] * (1 + SPREAD_SLIP)
            units = (cash * (1 - TAKER)) / entry_px
            peak = entry_px
            j = i + 1; exit_px = None
            while j < min(n, i + MAXHOLD):
                px = close[j]
                peak = max(peak, px)
                if px <= entry_px * (1 - STOP) or px <= peak * (1 - trail):
                    exit_px = px * (1 - SPREAD_SLIP); break
                j += 1
            if exit_px is None:
                exit_px = close[min(n - 1, i + MAXHOLD - 1)] * (1 - SPREAD_SLIP)
                j = min(n - 1, i + MAXHOLD - 1)
            proceeds = units * exit_px * (1 - TAKER)
            trips.append(proceeds / cash - 1)
            cash = proceeds
            i = j + 1
        else:
            i += 1
    return cash, trips


def run_set(coins, first, last, entry_move, vol_mult, trail, cache):
    tot_ret, tot_trips, wins = 0.0, 0, 0
    for c in coins:
        if c not in cache:
            ts, close, vol = load_series(c, first, last)
            cache[c] = (ts, close, vol) + precompute(ts, close, vol) if close else None
        if not cache[c]:
            continue
        ts, close, vol, ret_k, vavg = cache[c]
        cash, trips = simulate(ts, close, vol, ret_k, vavg, entry_move, vol_mult, trail)
        tot_ret += (cash - 1); tot_trips += len(trips); wins += sum(1 for x in trips if x > 0)
    return tot_ret / max(1, len(coins)) * 100, tot_trips, (wins / tot_trips * 100 if tot_trips else 0)


def main():
    search_win = (T(2024, 1, 1) // 2629800, T(2025, 9, 1) // 2629800)  # rough month index, unused; use months() directly
    cacheA = {}
    print("== grid search on SET_A tuned-9, 2024-01..2025-08 ==", flush=True)
    results = []
    for entry_move, vol_mult, trail in itertools.product(*GRID.values()):
        t0 = time.time()
        r, trips, wr = run_set(SET_A, (2024, 1), (2025, 8), entry_move, vol_mult, trail, cacheA)
        results.append({"entry_move": entry_move, "vol_mult": vol_mult, "trail": trail, "avg_ret_pct": round(r, 2), "trips": trips, "win_pct": round(wr, 1)})
        print(f"move>={entry_move:.3f} vol>={vol_mult}x trail={trail:.3f}: avg/coin {r:+7.2f}% trips {trips:5d} win {wr:5.1f}% [{time.time()-t0:.0f}s]", flush=True)
        json.dump({"search": results}, open(os.path.join(BASE, "logs", "pump_lab.json"), "w"), indent=1)
    results.sort(key=lambda x: -x["avg_ret_pct"])
    top = results[:5]
    print("\n== validating top 5 on SET_A held-out (2025-09..now) and SET_B unseen-18 (2024-01..now) ==", flush=True)
    out = {"search": results, "validated": []}
    cacheA2, cacheB = {}, {}
    for cfg in top:
        rh, th, wh = run_set(SET_A, (2025, 9), (2026, 9), cfg["entry_move"], cfg["vol_mult"], cfg["trail"], cacheA2)
        ru, tu, wu = run_set(SET_B, (2024, 1), (2026, 9), cfg["entry_move"], cfg["vol_mult"], cfg["trail"], cacheB)
        row = {**cfg, "heldout_ret_pct": round(rh, 2), "heldout_trips": th, "heldout_win_pct": round(wh, 1),
               "unseen_ret_pct": round(ru, 2), "unseen_trips": tu, "unseen_win_pct": round(wu, 1)}
        out["validated"].append(row)
        print(f"cfg {cfg}: held-out(9tuned,recent) {rh:+7.2f}%/{th}trips win{wh:.0f}% | unseen(18) {ru:+7.2f}%/{tu}trips win{wu:.0f}%", flush=True)
        json.dump(out, open(os.path.join(BASE, "logs", "pump_lab.json"), "w"), indent=1)

main()
