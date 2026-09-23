"""Stress test the community bots against the markets that hurt them, with and without a drawdown breaker.
Scenarios (200 warm-up days of a calm uptrend so the F1 filter is ON when trouble starts):
  real 2022 (Nov-2021 -> Dec-2022) / real 2020-03 crash / slow bleed -60% in 240d with two +25% relief rallies /
  flash crash -30% in a day then partial recovery / -50% over 30 days / sideways chop.
    python3 bot_stress.py"""
import json, os, random, time
from marketcoach import market_sim as ms, bot_engine as be, community_bots as cb, market_filters as mf
from marketcoach.data import Bar

BASE = os.path.dirname(os.path.abspath(__file__))
CASH = 100.0


def synth(daily_market, seed=1, coins=9, idio=0.02):
    rng = random.Random(seed)
    names = [f"C{k}-USD" for k in range(coins)]
    names[0] = "BTC-USD"
    px = {n: 100.0 for n in names}
    bars = {n: [] for n in names}
    for d, m in enumerate(daily_market):
        for n in names:
            r = m + rng.gauss(0, idio)
            o = px[n]; c = o * (1 + r)
            bars[n].append(Bar(86400 * (18000 + d), o, max(o, c) * (1 + abs(rng.gauss(0, 0.008))), min(o, c) * (1 - abs(rng.gauss(0, 0.008))), c, 1000.0))
            px[n] = c
    return bars


def scenario_paths():
    rng = random.Random(7)
    warm = [0.0015 + rng.gauss(0, 0.012) for _ in range(260)]
    S = {}
    bleed = []
    for d in range(240):
        r = -0.0038 + rng.gauss(0, 0.03)
        if 60 <= d < 68 or 150 <= d < 158: r += 0.03           # relief rallies (~+25%)
        bleed.append(r)
    S["slow bleed -60% + 2 relief rallies"] = warm + bleed
    flash = [0.0] * 5 + [-0.30] + [0.02] * 10 + [rng.gauss(-0.001, 0.02) for _ in range(120)]
    S["flash crash -30% in a day"] = warm + flash
    S["-50% over 30 days"] = warm + [-0.0225 + rng.gauss(0, 0.02) for _ in range(30)] + [rng.gauss(0, 0.015) for _ in range(120)]
    S["sideways chop 250d"] = warm + [rng.gauss(0, 0.028) for _ in range(250)]
    return {k: synth(v) for k, v in S.items()}


def run_all(bars_by_name, ok_builder):
    out = {}
    for name, bars in bars_by_name.items():
        ts = [b.ts for b in next(iter(bars.values()))]
        f0, f1 = ms.filters_for(bars)
        ok = {"none": None, "F0": (lambda i, s=f0: ts[i] in s), "F1": (lambda i, s=f1: ts[i] in s)}
        out[name] = ok_builder(bars, ok)
    return out


BOTS = {
    "DCA d5 F1": lambda ok, br: wrap(cb.DCACycleBot(safety=5, dev=0.05, mult=2.0, tp=0.03, stop=0.2, market_ok=ok["F1"]), br),
    "DCA d3 F1": lambda ok, br: wrap(cb.DCACycleBot(safety=5, dev=0.03, mult=2.0, tp=0.03, stop=0.2, market_ok=ok["F1"]), br),
    "DCA d5 NO filter": lambda ok, br: wrap(cb.DCACycleBot(safety=5, dev=0.05, mult=2.0, tp=0.03, stop=0.2, market_ok=None), br),
    "Grid L8 s6 F1": lambda ok, br: wrap(cb.GridBot(levels=8, step=0.06, market_ok=ok["F1"]), br),
}


def wrap(bot, br):
    return cb.Breaker(bot, dd_limit=br) if br else bot


def measure(mk, bars, ok, br):
    r = be.run(wrap_bot(mk, ok, br), bars, cash=CASH)
    peak, dd = r.equity[0], 0.0
    e = r.equity[260:]                                   # score only the trouble period
    peak = e[0]
    for v in e:
        peak = max(peak, v); dd = min(dd, v / peak - 1)
    return round((e[-1] / e[0] - 1) * 100, 1), round(dd * 100, 1)


def wrap_bot(mk, ok, br):
    return mk(ok, br)


def main():
    real = {}
    long = ms.load_long()
    ts = [b.ts for b in long["BTC-USD"]]
    def window(a, b):
        ia = next(i for i, t in enumerate(ts) if t >= a); ib = next((i for i, t in enumerate(ts) if t >= b), len(ts))
        return {p: bs[max(0, ia - 260):ib] for p, bs in long.items()}, ia - max(0, ia - 260)
    tsx = lambda y, m, d: time.mktime((y, m, d, 0, 0, 0, 0, 0, 0)) - time.timezone
    scen = {"REAL 2022 (Nov-21 -> Dec-22)": window(tsx(2021, 11, 10), tsx(2022, 12, 31)),
            "REAL covid crash (Feb-Apr 2020)": window(tsx(2020, 2, 15), tsx(2020, 5, 31))}
    syn = scenario_paths()
    print(f"{'scenario':38s} {'bot':18s} | no breaker: ret  maxDD | breaker 8%: ret maxDD | 12%: ret maxDD | 15%: ret maxDD")
    table = {}
    for name, item in list(scen.items()) + [(k, (v, 260)) for k, v in syn.items()]:
        bars, warm = item
        tsl = [b.ts for b in next(iter(bars.values()))]
        f0, f1 = ms.filters_for(bars)
        ok = {"none": None, "F0": (lambda i, s=f0, t=tsl: t[i] in s), "F1": (lambda i, s=f1, t=tsl: t[i] in s)}
        for bname, mk in BOTS.items():
            row = []
            for br in (None, 0.08, 0.12, 0.15):
                r = be.run(mk(ok, br), bars, cash=CASH)
                e = r.equity[warm:]
                peak, dd = e[0], 0.0
                for v in e:
                    peak = max(peak, v); dd = min(dd, v / peak - 1)
                row.append((round((e[-1] / e[0] - 1) * 100, 1), round(dd * 100, 1)))
            table[f"{name}|{bname}"] = row
            print(f"{name:38s} {bname:18s} | " + " | ".join(f"{a:>+6.1f}% {b:>6.1f}%" for a, b in row), flush=True)
    json.dump(table, open(os.path.join(BASE, "logs", "bot_stress.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
