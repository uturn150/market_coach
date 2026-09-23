# Graduation: when is it reasonable to consider real money?

```
backtest -> out-of-sample -> walk-forward -> fee gate -> edge gate -> Monte Carlo
   -> live-paper execution -> live-paper sample -> portfolio check
   -> ELIGIBLE_FOR_HUMAN_REVIEW      (a human decides; nothing here enables trading)
```

**The rules:** nothing skips a stage, nothing graduates itself, and **calendar time
is never a criterion** — only evidence (counts, regime diversity, robustness).
`graduation_report.py` only ever *reports*. It cannot open an account, place an
order, or change a flag. The strongest thing it can say is
`ELIGIBLE_FOR_HUMAN_REVIEW`.

## The chain (every stage is evaluated for every account)

| # | Stage | Passes when |
|---|---|---|
| 1 | HISTORICAL | Full history at the LIVE Kraken fee tier: profitable after costs, ≥30 round trips, profit factor >1 |
| 2 | OUT_OF_SAMPLE | Held-out 40%: profitable in absolute terms, ≥15 trips |
| 3 | WALK_FORWARD | Beats hold in ≥50% of rolling windows, median excess >0, and beats the regime-timed hold on windows where the rules did something the filter didn't |
| 4 | FEE_GATE | Average trade clears the real round-trip cost wall, adequate sample |
| 5 | EDGE_GATE | Held-out excess >0 vs buy-and-hold **and** >0 vs regime-timed hold (sitting in cash through a bear market is not edge) |
| 6 | MONTE_CARLO | Bootstrap 5th percentile ≥0, P(loss) ≤10%, ≥5 of 6 stress scenarios still pass both gates |
| 7 | LIVE_PAPER_EXECUTION | Paper account; ≥10 fills with real order-book execution records (≤10% fallback); no unexplained circuit-breaker trip; not flagged for revalidation |
| 8 | LIVE_PAPER_SAMPLE | ≥30 live paper round trips spanning ≥2 market regimes |
| 9 | PORTFOLIO_CHECK | Not a redundant copy (return correlation ≥0.85) of an already-eligible strategy |

Each stage is `PASS`, `FAIL`, `PENDING` (not enough live evidence yet — never a
pass), or `NOT_EVALUATED` (portfolio check only, once everything else passes).

Overall status: any FAIL → `NOT_ELIGIBLE`; no FAIL but some PENDING →
`ACCUMULATING_EVIDENCE`; all PASS → `ELIGIBLE_FOR_HUMAN_REVIEW`.

## What "eligible" would mean

Not "put the full $50 in" and not an instruction. It means the evidence is strong
enough that a human might consider a **tiny live account** (an amount you'd be
fully at peace losing) on the same strategy, run in parallel with the paper
account, never replacing it, with any real-money path built separately and
switched on by hand. Fees at the current tier are ~0.8% per side; check the
report's fee line before believing any small-account plan.

## Run it

```bash
python3 graduation_report.py                    # everything
python3 graduation_report.py realpaper c4h_multi
```
Output also lands in `logs/graduation/latest.json`. Excluded by design: `arena_*`
and `krakendemo*`. `krakenmoon` is not gradable here (funnel architecture, no
backtest harness).
