"""The 'smart module': search a template's parameter space for good rule settings.

The ONE rule that stops this from being a money incinerator:
  * Candidates are SELECTED on the in-sample slice only.
  * The winner is JUDGED on the out-of-sample slice it never touched.
Optimizing and judging on the same data is overfitting — you'd 'evolve' a curve
that fit past noise and makes zero real dollars. So we split first, always.

We also report an overfitting diagnostic: what fraction of ALL candidates beat
buy-and-hold out-of-sample. If only the hand-picked winner does, the edge is
luck. If a broad swath of the family does, that's a signal worth paper-trading.
"""
import copy, itertools, json, random
from . import data, engine, metrics
from .strategy import Strategy


def _fill(node, values):
    """Recursively substitute {param} placeholders. A pure '{p}' becomes the raw
    value (keeps numbers numeric); embedded ones like 'sma:{fast}' become strings."""
    if isinstance(node, dict):
        return {k: _fill(v, values) for k, v in node.items()}
    if isinstance(node, list):
        return [_fill(v, values) for v in node]
    if isinstance(node, str):
        if node.startswith("{") and node.endswith("}") and node.count("{") == 1:
            return values[node[1:-1]]
        out = node
        for k, v in values.items():
            out = out.replace("{" + k + "}", str(v))
        return out
    return node


def candidates(template):
    """Yield (values, spec) for every param combo satisfying the constraints."""
    params = template.get("params", {})
    names = list(params)
    constraints = template.get("constraints", [])
    for combo in itertools.product(*(params[n] for n in names)):
        values = dict(zip(names, combo))
        if all(eval(c, {}, values) for c in constraints):  # trusted local template only
            spec = _fill({k: v for k, v in template.items()
                          if k not in ("params", "constraints")}, values)
            yield values, spec


def score(spec, bars, cash, objective):
    rep = metrics.summarize(engine.run(Strategy(spec), bars, cash=cash))
    return getattr(rep, {"excess": "excess", "sharpe": "sharpe",
                         "return": "total_return"}[objective]), rep


def optimize(template, bars, split=0.6, cash=50.0, objective="excess",
             max_candidates=3000, seed=1, verbose=True):
    cut = int(len(bars) * split)
    in_bars, out_bars = bars[:cut], bars[cut:]

    combos = list(candidates(template))
    if len(combos) > max_candidates:
        combos = random.Random(seed).sample(combos, max_candidates)

    ranked = []       # fee-gate survivors only
    rejected = 0      # failed the fee gate in-sample
    out_beats = 0
    for values, spec in combos:
        strat = Strategy(spec)
        in_res = engine.run(strat, in_bars, cash=cash)
        in_rep = metrics.summarize(in_res)
        gate = metrics.fee_gate(in_res, strat)
        if not gate.ok:
            rejected += 1
            continue                       # fee gate: never even consider fee-burners
        if in_rep.low_confidence:
            rejected += 1
            continue                       # too few trades in-sample to trust the ranking
        out_rep = metrics.summarize(engine.run(strat, out_bars, cash=cash))
        if out_rep.excess > 0:
            out_beats += 1
        obj = {"excess": in_rep.excess, "sharpe": in_rep.sharpe,
               "return": in_rep.total_return}[objective]
        ranked.append((obj, values, spec, in_rep, out_rep, gate))

    ranked.sort(key=lambda t: t[0], reverse=True)
    frac = out_beats / len(ranked) if ranked else 0.0
    best = ranked[0] if ranked else None

    if verbose:
        print(f"\nOptimizing '{template.get('name')}'  objective={objective}  "
              f"{len(combos)} candidates  split {split:.0%}/{1-split:.0%}")
        print("-" * 74)
        print(f"FEE GATE + SAMPLE SIZE: {rejected}/{len(combos)} candidates REJECTED "
              f"(fee-burner OR <{metrics.MIN_TRADES_FOR_CONFIDENCE} in-sample round trips). "
              f"{len(ranked)} survived.")
        if not ranked:
            print("\nNo candidate survives. Either every setting trades too often/small "
                  "to beat costs (fee-burner), OR this single asset just doesn't produce "
                  f"{metrics.MIN_TRADES_FOR_CONFIDENCE}+ trades in-sample to judge honestly "
                  "(common for slow strategies on one coin — try a basket via portfolio.py, "
                  "or a longer history / faster timeframe).")
            return None, ranked, frac
        print("-" * 74)
        print("Top 5 survivors by IN-SAMPLE, with OUT-OF-SAMPLE excess (the honest number):")
        for obj, vals, spec, in_rep, out_rep, gate in ranked[:5]:
            flag = "PASS" if out_rep.excess > 0 else "fail"
            print(f"  {vals}  trade {gate.avg_gross*100:+.1f}% vs wall "
                  f"{gate.breakeven*100:.2f}%  OUT:{out_rep.excess*100:+6.1f}%  [{flag}]")
        print("-" * 74)
        print(f"Overfitting check: {out_beats}/{len(ranked)} "
              f"({frac*100:.0f}%) of SURVIVORS beat hold OUT-OF-SAMPLE.")
        b_vals, b_in, b_out = best[1], best[3], best[4]
        print(f"\nIn-sample winner: {b_vals}")
        print(f"  IN : {metrics.fmt(b_in)}")
        print(f"  OUT: {metrics.fmt(b_out)}")
        if b_out.excess > 0 and frac >= 0.5:
            print("\nRESULT: clears fees, beats hold out-of-sample, family broadly robust. "
                  "Reasonable candidate for paper trading.")
        elif b_out.excess > 0:
            print("\nRESULT: clears fees and beats hold out-of-sample, but the family is "
                  "thin (<50%). Could be luck — paper-trade small.")
        else:
            print("\nRESULT: clears fees but does NOT beat hold out-of-sample. "
                  "No real edge — do not risk money.")

    return best, ranked, frac
