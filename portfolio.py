#!/usr/bin/env python3
"""Backtest a strategy as an equal-weight PORTFOLIO across many coins, with the
fee gate pooled across every trade. Answers: does this strategy, spread over a
basket at this timeframe, actually end up positive after costs?

Usage:
  python3 portfolio.py strategies/intraday_momentum.json 3600            # 1h bars
  python3 portfolio.py strategies/intraday_momentum.json 900 BTC ETH SOL # 15m, custom
  python3 portfolio.py strategies/swing_btc.json 86400                   # daily
"""
import sys, statistics
from marketcoach import data, engine, metrics
from marketcoach.strategy import Strategy

BASKET = ["BTC", "ETH", "SOL", "ADA", "AVAX", "LINK", "LTC", "XRP", "DOT", "DOGE", "ATOM", "UNI"]
GRAN_LABEL = {900: "15m", 3600: "1h", 21600: "6h", 86400: "1d"}
BARS_PER_DAY = {900: 96, 3600: 24, 21600: 4, 86400: 1}


def run(path, gran=3600, coins=None, cash=50.0, split=0.6, max_bars=1500):
    coins = coins or BASKET
    per = cash / len(coins)
    rows, pooled_trips, total_days = [], [], 0

    for sym in coins:
        product = sym if "-" in sym else f"{sym}-USD"
        try:
            bars = data.get_bars(product, granularity=gran, max_bars=max_bars)
        except Exception as e:
            print(f"  skip {product}: {type(e).__name__}"); continue
        if len(bars) < 100:
            continue
        strat = Strategy.load(path)
        res = engine.run(strat, bars, cash=per)
        rep = metrics.summarize(res, bars_per_year=365 * BARS_PER_DAY[gran])
        pooled_trips += metrics.roundtrips(res, strat.side_rate)
        total_days = max(total_days, len(bars) / BARS_PER_DAY[gran])
        # out-of-sample sleeve
        cut = int(len(bars) * split)
        out = metrics.summarize(engine.run(Strategy.load(path), bars[cut:], cash=per),
                                bars_per_year=365 * BARS_PER_DAY[gran])
        rows.append((product, rep, out))

    strat = Strategy.load(path)
    be = strat.breakeven_move
    label = GRAN_LABEL.get(gran, f"{gran}s")

    # combined numbers
    comb_final = sum(r[1].final_equity for r in rows)
    comb_ret = comb_final / cash - 1
    comb_out_final = sum(r[2].final_equity for r in rows)
    n_trades = sum(r[1].n_trades for r in rows)
    trades_per_day = n_trades / total_days if total_days else 0

    print(f"\nPortfolio: {strat.name}")
    print(f"{len(rows)} coins, equal weight, {label} bars (~{total_days:.0f} days), "
          f"${cash:.0f} start   [{strat.costs['order_type']}, wall {be*100:.2f}%/trade]")
    print("=" * 78)
    print(f"{'coin':9} {'return':>9} {'trades':>7} {'OOS ret':>9} {'OOS vs hold':>12}")
    print("-" * 78)
    for product, rep, out in sorted(rows, key=lambda r: r[1].total_return, reverse=True):
        print(f"{product:9} {rep.total_return*100:+8.1f}% {rep.n_trades:7d} "
              f"{out.total_return*100:+8.1f}% {out.excess*100:+11.1f}%")
    print("-" * 78)

    # pooled fee gate across the whole basket
    if pooled_trips:
        avg_gross = statistics.mean(g for g, _ in pooled_trips)
        avg_net = statistics.mean(n for _, n in pooled_trips)
        hit = sum(1 for g, _ in pooled_trips if g > be) / len(pooled_trips)
        gate_ok = avg_net > 0
        print(f"FULL-PERIOD combined: {comb_ret*100:+.1f}%  (${cash:.2f} -> ${comb_final:.2f})   "
              f"~{trades_per_day:.1f} trades/day")
        print(f"POOLED FEE GATE [{'PASS' if gate_ok else 'FAIL'}]: avg trade {avg_gross*100:+.2f}% "
              f"vs wall {be*100:.2f}%   net/trade {avg_net*100:+.3f}%   "
              f"{hit*100:.0f}% clear the wall   (n={len(pooled_trips)})")
    else:
        gate_ok = False
        print("No round trips generated.")

    print(f"OUT-OF-SAMPLE combined: ${comb_out_final:.2f} from ${cash:.2f}")
    print("-" * 78)
    if gate_ok and comb_out_final > cash:
        print("Clears fees AND ends positive out-of-sample. Forward-test candidate.")
    elif not gate_ok:
        print("VERDICT: fee gate FAIL — the average trade loses to fees. Trading this "
              "often at this size is a fee-burner. Do not deploy.")
    else:
        print("VERDICT: survives fees but ends DOWN out-of-sample. No reliable edge.")
    return rows


def main():
    if len(sys.argv) < 2:
        print(__doc__); return
    gran = int(sys.argv[2]) if len(sys.argv) > 2 else 3600
    coins = sys.argv[3:] or None
    run(sys.argv[1], gran=gran, coins=coins)


if __name__ == "__main__":
    main()
