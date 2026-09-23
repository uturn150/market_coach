"""Portfolio+regime-aware optimizer: the gap we kept flagging. `optimize.py` tunes
a strategy on ONE coin — but what's actually live is a 12-coin basket with a
market-regime filter, and those two things change results by 3x (see smart_swing:
+31.5% solo -> +90.3% with regime+basket). This module searches a template the
way it's actually deployed: pooled across the basket, with the regime filter on,
split by TIME (not by coin) so it's still a genuine in-sample/out-of-sample test.

Same honesty rules as optimize.py: select on in-sample, judge on out-of-sample,
reject anything that doesn't clear the pooled fee gate + pooled sample-size gate.
"""
import random
from . import data, engine, metrics, regime
from .optimize import candidates
from .strategy import Strategy

DEFAULT_BASKET = ["BTC", "ETH", "SOL", "ADA", "AVAX", "LINK", "LTC", "XRP",
                  "DOT", "DOGE", "ATOM", "UNI",
                  # Expansion batch (2026-09-18): tested up to 43 coins total —
                  # this 24-coin cut is the evidence-based sweet spot. Going to
                  # 43 kept clearing the FEE gate everywhere but broke the EDGE
                  # gate for 7/10 strategies (newer/thinner/more speculative
                  # coins diluted out-of-sample edge even though each trade
                  # still individually cleared costs). More coins helps up to a
                  # point, then hurts — this was the point.
                  "BCH", "ALGO", "FIL", "NEAR", "APT", "ARB", "OP", "ICP",
                  "ETC", "XLM", "AAVE", "SHIB"]


def _pooled(spec, coin_bars, cash, split, allow_buy_ts):
    """Run one candidate across every coin, in-sample and out-of-sample, pooling
    fills for an honest across-basket fee/sample check. Returns a dict of pooled
    stats or None if a coin's data was too short to split."""
    per = cash / len(coin_bars)
    in_trips, out_trips = [], []
    in_final = out_final = in_bh = out_bh = 0.0

    for bars in coin_bars.values():
        cut = int(len(bars) * split)
        if cut < 50 or len(bars) - cut < 30:
            continue
        in_bars, out_bars = bars[:cut], bars[cut:]

        strat_in = Strategy(spec)
        res_in = engine.run(strat_in, in_bars, cash=per, allow_buy_ts=allow_buy_ts)
        in_trips += metrics.roundtrips(res_in, strat_in.side_rate)
        in_final += res_in.equity[-1]
        in_bh += per * (in_bars[-1].close / in_bars[0].close)

        strat_out = Strategy(spec)
        res_out = engine.run(strat_out, out_bars, cash=per, allow_buy_ts=allow_buy_ts)
        out_trips += metrics.roundtrips(res_out, strat_out.side_rate)
        out_final += res_out.equity[-1]
        out_bh += per * (out_bars[-1].close / out_bars[0].close)

    if not in_trips and not out_trips:
        return None
    n_in, n_out = len(in_trips), len(out_trips)
    avg_net_in = sum(n for _, n in in_trips) / n_in if n_in else 0.0

    # Rank by the strategy's OWN quality (profit factor: gross win $ / gross loss
    # $ on in-sample trips), not "excess vs hold" — excess can be deeply negative
    # for a genuinely great strategy simply because buy-and-hold happened to be in
    # a strong bull run during the in-sample window. Profit factor judges the
    # strategy on its own trades, immune to that confound.
    gross_win = sum(t for t in (g for g, _ in in_trips) if t > 0)
    gross_loss = -sum(t for t in (g for g, _ in in_trips) if t < 0)
    pf_in = (gross_win / gross_loss) if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)

    return {
        "n_in": n_in, "n_out": n_out, "avg_net_in": avg_net_in, "pf_in": pf_in,
        "fee_gate_ok": n_in > 0 and avg_net_in > 0,
        "sample_ok": n_in >= metrics.MIN_TRADES_FOR_CONFIDENCE,
        "in_return": in_final / cash - 1, "in_excess": (in_final - in_bh) / cash,
        "out_return": out_final / cash - 1, "out_excess": (out_final - out_bh) / cash,
        "out_final": out_final,
    }


def optimize(template, coins=None, split=0.6, cash=50.0, use_regime=True,
             granularity=86400, max_bars=1500, max_candidates=400, seed=1, verbose=True):
    coins = coins or DEFAULT_BASKET
    products = [c if "-" in c else f"{c}-USD" for c in coins]
    if verbose:
        print(f"Loading {len(products)}-coin basket ({granularity}s bars)...")
    coin_bars = {}
    for p in products:
        try:
            coin_bars[p] = data.get_bars(p, granularity=granularity, max_bars=max_bars)
        except Exception:
            continue

    allow_buy_ts = regime.risk_on_timestamps("BTC-USD", granularity=granularity,
                                             max_bars=max_bars) if use_regime else None

    combos = list(candidates(template))
    if len(combos) > max_candidates:
        combos = random.Random(seed).sample(combos, max_candidates)

    ranked, rejected, out_beats = [], 0, 0
    for values, spec in combos:
        stats = _pooled(spec, coin_bars, cash, split, allow_buy_ts)
        if stats is None or not stats["fee_gate_ok"] or not stats["sample_ok"]:
            rejected += 1
            continue
        if stats["out_excess"] > 0:
            out_beats += 1
        ranked.append((stats["pf_in"], values, spec, stats))

    ranked.sort(key=lambda t: t[0], reverse=True)
    frac = out_beats / len(ranked) if ranked else 0.0
    best = ranked[0] if ranked else None

    if verbose:
        regime_lbl = "WITH regime filter" if use_regime else "no regime filter"
        print(f"\nPortfolio optimize: '{template.get('name')}'  {len(products)} coins, "
              f"{regime_lbl}, split {split:.0%}/{1-split:.0%}")
        print("-" * 78)
        print(f"POOLED FEE+SAMPLE GATE: {rejected}/{len(combos)} candidates rejected "
              f"(fee-burner OR <{metrics.MIN_TRADES_FOR_CONFIDENCE} pooled in-sample trades). "
              f"{len(ranked)} survived.")
        if not ranked:
            print("\nNothing survives. This family can't clear costs across the basket "
                  "even pooled — needs slower rules or a different signal set.")
            return None, ranked, frac
        print("-" * 78)
        print("Top 5 survivors by pooled IN-SAMPLE profit factor, with pooled OUT-OF-SAMPLE:")
        for pf_in, vals, spec, st in ranked[:5]:
            flag = "PASS" if st["out_excess"] > 0 else "fail"
            print(f"  {vals}  PF_in:{pf_in:5.2f}  in_return:{st['in_return']*100:+6.1f}%  "
                  f"OUT excess:{st['out_excess']*100:+6.1f}%  "
                  f"(n_in={st['n_in']}, n_out={st['n_out']})  [{flag}]")
        print("-" * 78)
        print(f"Overfitting check: {out_beats}/{len(ranked)} ({frac*100:.0f}%) of survivors "
              f"beat hold OUT-OF-SAMPLE (pooled).")
        b_vals, b_stats = best[1], best[3]
        print(f"\nWinner: {b_vals}")
        print(f"  IN : return {b_stats['in_return']*100:+.1f}%  excess {b_stats['in_excess']*100:+.1f}%")
        print(f"  OUT: return {b_stats['out_return']*100:+.1f}%  excess {b_stats['out_excess']*100:+.1f}%  "
              f"final ${b_stats['out_final']:.2f} (from ${cash:.0f})")
        if b_stats["out_excess"] > 0 and frac >= 0.5:
            print("\nRESULT: clears fees, beats hold pooled OUT-OF-SAMPLE, family robust. "
                  "Strong candidate to deploy live.")
        elif b_stats["out_excess"] > 0:
            print("\nRESULT: winner beats hold OOS but family is thin (<50%) — could be "
                  "luck. Deploy small/cautiously if at all.")
        else:
            print("\nRESULT: even the best candidate does not beat hold pooled OOS. "
                  "No validated edge for this template on this basket+regime combo.")

    return best, ranked, frac
