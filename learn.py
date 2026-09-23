#!/usr/bin/env python3
"""Self-check ('get smarter', safely). Re-optimizes a template on the LATEST data
and logs whether the best params still clear both gates. It does NOT auto-deploy —
that would just chase noise. It flags drift so you decide, with the gates intact.

Run it on a schedule (weekly). Reads logs/learn.jsonl to see how the edge evolves.

Usage:
  python3 learn.py strategies/swing.tpl.json BTC-USD
"""
import json, sys
from marketcoach import data, optimize as opt, journal


def main():
    if len(sys.argv) < 2:
        print(__doc__); return
    template = json.load(open(sys.argv[1]))
    source = sys.argv[2] if len(sys.argv) > 2 else "BTC-USD"

    bars = data.get_bars(source)
    best, ranked, frac = opt.optimize(template, bars, split=0.6, objective="excess",
                                      verbose=False)
    if best is None:
        journal.log("learn", "check", template=template.get("name"), source=source,
                    result="no_fee_survivors")
        print("No fee-gate survivors — this family is a fee-burner on current data.")
        return

    vals, out_rep = best[1], best[4]
    passes = out_rep.excess > 0
    journal.log("learn", "check", template=template.get("name"), source=source,
                best_params=vals, oos_excess=round(out_rep.excess, 4),
                oos_return=round(out_rep.total_return, 4),
                robust_frac=round(frac, 2),
                verdict="edge_holds" if passes and frac >= 0.5 else
                        ("weak" if passes else "no_edge"))
    print(f"Self-check logged: best={vals}  OOS excess {out_rep.excess*100:+.1f}%  "
          f"robust {frac*100:.0f}%  -> "
          f"{'EDGE HOLDS' if passes and frac>=0.5 else ('WEAK' if passes else 'NO EDGE — review')}")
    print("Nothing auto-changed. Params only move when you decide and re-gate.")


if __name__ == "__main__":
    main()
