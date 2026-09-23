"""Medium-speed breakout: buy when price clears its N-hour high on above-normal volume (a real breakout, not noise),
exit on a trailing stop / hard stop / time limit. Hours-scale instead of the failed minutes-scale pump chase.
Built from the 27 coins' minute data aggregated to hourly bars (5 years, real fees). Same discipline as every other
test here: grid-search on the 9 TUNED coins' early years, then validate on (a) those same 9 coins' held-out recent
year and (b) the 18 UNSEEN coins over the full 5 years. A design only counts if BOTH validations are positive.
    python3 medium_breakout_lab.py -> logs/medium_breakout_lab.json"""
import calendar, itertools, json, os, time
from marketcoach import minute_data as md, market_sim as ms
BASE = os.path.dirname(os.path.abspath(__file__))
T = lambda y, m, d: calendar.timegm((y, m, d, 0, 0, 0))
TAKER = 0.008; SPREAD_SLIP = 0.0008
STOP = 0.08
VOLW = 48           # hours averaged for the volume baseline
MAXHOLD = 24 * 10    # hours, force-exit after 10 days if nothing else fired
GRID = {"lookback": (12, 24, 48, 72), "vol_mult": (1.3, 1.8, 2.5), "trail": (0.03, 0.05, 0.08)}
SET_A, SET_B = md.universe()[:9], md.universe()[9:]


def hourly(coin, first_month, last_month):
    ts, o, h, l, c, v = [], [], [], [], [], []
    cur_h, co, ch, cl, ccl, cv = None, None, None, None, None, 0.0
    for y, m in md.months(first_month, last_month):
        try:
            rows = md.load_month(coin, y, m)
        except Exception:
            continue
        for t, op, hi, lo, cls, vol in rows:
            hb = t - (t % 3600)
            if hb != cur_h:
                if cur_h is not None:
                    ts.append(cur_h); o.append(co); h.append(ch); l.append(cl); c.append(ccl); v.append(cv)
                cur_h, co, ch, cl, ccl, cv = hb, op, hi, lo, cls, 0.0
            ch = max(ch, hi); cl = min(cl, lo); ccl = cls; cv += vol
    if cur_h is not None:
        ts.append(cur_h); o.append(co); h.append(ch); l.append(cl); c.append(ccl); v.append(cv)
    return ts, o, h, l, c, v


def precompute(h, c, v):
    n = len(c)
    hh = [None] * n         # highest high of the PRIOR `lookback` bars (excludes current)
    vavg = [None] * n
    vsum = 0.0
    for i in range(n):
        vsum += v[i]
        if i >= VOLW: vsum -= v[i - VOLW]
        if i >= VOLW - 1: vavg[i] = vsum / VOLW
    return hh, vavg


def rolling_high(h, lookback, i):
    lo = max(0, i - lookback)
    return max(h[lo:i]) if i > lo else None


def simulate(o, h, l, c, v, vavg, lookback, vol_mult, trail):
    n = len(c); cash = 1.0; trips = []
    i = lookback + 1
    highs_cache = {}
    while i < n:
        if vavg[i] and v[i] >= vol_mult * vavg[i]:
            prior_high = max(h[i - lookback:i])
            if c[i] > prior_high:
                entry_px = c[i] * (1 + SPREAD_SLIP)
                units = (cash * (1 - TAKER)) / entry_px
                peak = entry_px
                j = i + 1; exit_px = None
                while j < min(n, i + MAXHOLD):
                    px = c[j]
                    peak = max(peak, px)
                    if px <= entry_px * (1 - STOP) or px <= peak * (1 - trail):
                        exit_px = px * (1 - SPREAD_SLIP); break
                    j += 1
                if exit_px is None:
                    j = min(n - 1, i + MAXHOLD - 1); exit_px = c[j] * (1 - SPREAD_SLIP)
                proceeds = units * exit_px * (1 - TAKER)
                trips.append(proceeds / cash - 1)
                cash = proceeds
                i = j + 1
                continue
        i += 1
    return cash, trips


def run_set(coins, first, last, lookback, vol_mult, trail, cache):
    tot_ret, tot_trips, wins = 0.0, 0, 0
    for coin in coins:
        key = (coin, first, last)
        if key not in cache:
            ts, o, h, l, c, v = hourly(coin, first, last)
            _, vavg = precompute(h, c, v)
            cache[key] = (o, h, l, c, v, vavg) if c else None
        if not cache[key]:
            continue
        o, h, l, c, v, vavg = cache[key]
        cash, trips = simulate(o, h, l, c, v, vavg, lookback, vol_mult, trail)
        tot_ret += (cash - 1); tot_trips += len(trips); wins += sum(1 for x in trips if x > 0)
    return tot_ret / max(1, len(coins)) * 100, tot_trips, (wins / tot_trips * 100 if tot_trips else 0)


def main():
    cache = {}
    print("== grid search: SET_A tuned-9, 2021-10..2024-12 ==", flush=True)
    results = []
    for lookback, vol_mult, trail in itertools.product(*GRID.values()):
        t0 = time.time()
        r, trips, wr = run_set(SET_A, (2021, 10), (2024, 12), lookback, vol_mult, trail, cache)
        results.append({"lookback": lookback, "vol_mult": vol_mult, "trail": trail, "avg_ret_pct": round(r, 2), "trips": trips, "win_pct": round(wr, 1)})
        print(f"lookback {lookback:3d}h vol>={vol_mult}x trail={trail:.3f}: avg/coin {r:+7.2f}% trips {trips:5d} win {wr:5.1f}% [{time.time()-t0:.0f}s]", flush=True)
        json.dump({"search": results}, open(os.path.join(BASE, "logs", "medium_breakout_lab.json"), "w"), indent=1)
    results.sort(key=lambda x: -x["avg_ret_pct"])
    top = results[:6]
    print("\n== validating top 6: SET_A held-out (2025-01..now) | SET_B unseen-18 (2021-10..now) ==", flush=True)
    out = {"search": results, "validated": []}
    cacheH, cacheU = {}, {}
    for cfg in top:
        rh, th, wh = run_set(SET_A, (2025, 1), (2026, 9), cfg["lookback"], cfg["vol_mult"], cfg["trail"], cacheH)
        ru, tu, wu = run_set(SET_B, (2021, 10), (2026, 9), cfg["lookback"], cfg["vol_mult"], cfg["trail"], cacheU)
        row = {**cfg, "heldout_ret_pct": round(rh, 2), "heldout_trips": th, "heldout_win_pct": round(wh, 1),
               "unseen_ret_pct": round(ru, 2), "unseen_trips": tu, "unseen_win_pct": round(wu, 1)}
        out["validated"].append(row)
        print(f"cfg {cfg}: held-out(9,recent) {rh:+7.2f}%/{th}trips win{wh:.0f}% | unseen(18,5y) {ru:+7.2f}%/{tu}trips win{wu:.0f}%", flush=True)
        json.dump(out, open(os.path.join(BASE, "logs", "medium_breakout_lab.json"), "w"), indent=1)

main()
