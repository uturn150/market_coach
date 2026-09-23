"""How much does the fee tier matter to each community bot? Same 9-coin 2020-26 history, same configs, only the
maker/taker schedule changes.  Current account tier (from Kraken TradeVolume) is 0.40% maker / 0.80% taker.
    python3 bot_fee_sensitivity.py"""
import json, os
from marketcoach import market_sim as ms, bot_engine as be
from bot_lab import configs, make_ok, CASH

BASE = os.path.dirname(os.path.abspath(__file__))
SCHEDULES = {"current 0.40/0.80": (0.0040, 0.0080), "Pro base 0.25/0.40": (0.0025, 0.0040), "vol tier 0.16/0.26": (0.0016, 0.0026), "high vol 0.10/0.20": (0.0010, 0.0020)}
PICK = ["grid L8 step6% F1", "grid L8 step4% F1", "grid L8 step2.5% F1", "dca SO5 dev5% x2.0 tp3% stop0.2 F1", "dca SO5 dev5% x2.0 tp1.5% stop0.2 F1",
        "dca SO5 dev3% x2.0 tp3% stop0.2 F1", "rotation top4 lb60 every7 F1"]


def main():
    bars = ms.load_long()
    ok = make_ok(bars)
    C = configs(ok)
    print(f"{'bot':40s}" + "".join(f"{s:>26s}" for s in SCHEDULES))
    print(f"{'':40s}" + "".join(f"{'return  maxDD  green-yr':>26s}" for _ in SCHEDULES))
    res = {}
    for name in PICK:
        row = []
        for sname, (mk, tk) in SCHEDULES.items():
            r = be.run(C[name][1](), bars, cash=CASH, maker=mk, taker=tk)
            y = ms.yearly(dict(zip(r.ts, r.equity)))
            peak, dd = r.equity[0], 0.0
            for v in r.equity:
                peak = max(peak, v); dd = min(dd, v / peak - 1)
            row.append((round((r.equity[-1] / r.equity[0] - 1) * 100, 1), round(dd * 100, 1), y["green_year_pct"]))
        res[name] = row
        print(f"{name:40s}" + "".join(f"{a:>+9.1f}% {b:>6.1f}% {g:>6.0f}%" for a, b, g in row))
    json.dump(res, open(os.path.join(BASE, "logs", "bot_fee_sensitivity.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
