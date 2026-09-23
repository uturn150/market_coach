"""What account SIZE do these bots actually need? Every order must meet Kraken's minimum order value (~$7 for most
coins; a few need ~$13-15). Run the flagship bots at different balances WITH that minimum enforced. Paper accounts
so far assume infinitely divisible orders; this shows what a real small account could really do.
    python3 balance_lab.py"""
import json, os
from marketcoach import market_sim as ms, bot_engine as be, community_bots as cb
from bot_lab import configs, make_ok

BASE = os.path.dirname(os.path.abspath(__file__))
BAL = (50, 100, 250, 500, 1000, 2500, 5000, 10000)


def main():
    bars = ms.load_long(); ok = make_ok(bars)
    bots = {"DCA d5 x2 SO5 F1 (9 coins)": lambda: cb.DCACycleBot(safety=5, dev=0.05, mult=2.0, tp=0.03, stop=0.2, market_ok=ok["F1"]),
            "DCA d3 x2 SO5 F1 (9 coins)": lambda: cb.DCACycleBot(safety=5, dev=0.03, mult=2.0, tp=0.03, stop=0.2, market_ok=ok["F1"]),
            "Grid L8 step6% F1 (9 coins)": lambda: cb.GridBot(levels=8, step=0.06, market_ok=ok["F1"])}
    print(f"{'bot':32s} " + "".join(f"{'$'+str(b):>17s}" for b in BAL))
    print(f"{'':32s} " + "".join(f"{'ret%  trades/day':>17s}" for _ in BAL))
    out = {}
    days = len(next(iter(bars.values())))
    for name, mk in bots.items():
        row = []
        for b in BAL:
            r = be.run(mk(), bars, cash=float(b), min_order=7.0)
            row.append(((r.equity[-1] / b - 1) * 100, len(r.trips) / days, r.ctx.skipped_min))
        out[name] = row
        print(f"{name:32s} " + "".join(f"{a:>+8.1f}% {t:>5.2f}/d " for a, t, _ in row))
    print("\norders REJECTED for being under the $7 minimum (count over 6.7 years):")
    for name, row in out.items():
        print(f"{name:32s} " + "".join(f"{s:>17d}" for _, _, s in row))
    json.dump(out, open(os.path.join(BASE, "logs", "balance_lab.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
