#!/usr/bin/env python3
"""Moon monitor: scan a wide coin list for breakouts and rank them. Logs every
detected moon to logs/moon.jsonl so you build a history of what fired and when.

Usage:
  python3 moonscan.py                     # daily scan of the wide liquid list
  python3 moonscan.py 3600                # 1h bars (faster, noisier)
  python3 moonscan.py 86400 BTC ETH SOL   # custom coins
"""
import sys, time
from marketcoach import data, moon, journal

# Wider liquid set than the trading basket — more coins = more chances to catch a
# real mover. Still liquid names (thin alts fake volume spikes and trap you).
WIDE = ["BTC", "ETH", "SOL", "ADA", "AVAX", "LINK", "LTC", "XRP", "DOT", "DOGE",
        "ATOM", "UNI", "AAVE", "MKR", "LDO", "APT", "ARB", "OP", "INJ", "SUI",
        "NEAR", "FIL", "ICP", "RNDR", "GRT", "AERO", "SEI", "TIA", "JTO", "PYTH"]


def main():
    args = sys.argv[1:]
    gran = 86400
    if args and args[0].isdigit():
        gran = int(args[0]); args = args[1:]
    coins = args or WIDE

    coin_bars = {}
    for sym in coins:
        product = sym if "-" in sym else f"{sym}-USD"
        try:
            coin_bars[product] = data.get_bars(product, granularity=gran, max_bars=400)
        except Exception:
            continue
        time.sleep(0.15)

    ranked = moon.rank(coin_bars)
    label = {900: "15m", 3600: "1h", 86400: "1d"}.get(gran, f"{gran}s")
    print(f"\nMoon monitor  [{label} bars, {len(ranked)} coins]   "
          f"triggers: breakout + momentum + volume + not-blowoff")
    print("=" * 72)
    print(f"{'coin':10} {'score':>6} {'roc':>7} {'vol x':>6} {'rsi':>5}  triggers")
    print("-" * 72)
    mooning = []
    for product, ev in ranked[:15]:
        flags = "".join(k[0].upper() if v else "." for k, v in ev["triggers"].items())
        star = "  <== MOONING" if ev["is_mooning"] else ""
        print(f"{product:10} {ev['score']:6.1f} {ev['roc']*100:+6.1f}% "
              f"{ev['vol_ratio']:5.1f}x {ev['rsi']:5.0f}  {flags}{star}")
        if ev["is_mooning"]:
            mooning.append(product)
            journal.log("moon", "detected", coin=product, score=ev["score"],
                        roc=round(ev["roc"], 3), vol_ratio=round(ev["vol_ratio"], 2),
                        rsi=round(ev["rsi"], 1), tf=label)
    print("-" * 72)
    print("triggers key: B=breakout M=momentum V=volume N=not-blowoff (all 4 = MOONING)")
    if mooning:
        print(f"MOONING NOW: {', '.join(mooning)}  (logged to logs/moon.jsonl)")
    else:
        print("Nothing passes all 4 triggers right now. Top of list = what's heating up.")


if __name__ == "__main__":
    main()
