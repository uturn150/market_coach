# market_coach

A trigger/effects backtest engine for markets — sibling to `lorcana_site/deck_coach`.
Pure stdlib, offline-first. Same idea as the Lorcana sim: a **strategy** is a set of
`{when, then}` rules (like a card's `{trigger, effects}`), and the **backtest** is the
sim (like `play_match`).

## The one rule that matters

Nothing touches real money until it **beats buy-and-hold, after costs, on
out-of-sample data**. Everything else is a way to fool yourself.

Gate ladder (mirrors deck_coach bench -> databorn -> duels):

1. **backtest** — replay history, does it work at all?
2. **walk-forward** — does it work on data it never saw? (`walkforward.py`, the gate)
3. **paper** — does it work live on fake money in real time? *(not built yet)*
4. **tiny live** — only after the above, and only money you can lose.

## Layout

```
marketcoach/
  data.py         OHLCV bars: Coinbase single-page, paginated history (~4yr),
                  fetch_recent (live, uncached), synthetic() offline
  indicators.py   sma / ema / rsi / roc — no-lookahead, None during warmup
  strategy.py     Strategy.load(json): {when:{left,op,right}, then:{action,size}}
  execution.py    ONE fill/cost model shared by backtest + paper (no drift)
  engine.py       backtest sim: next-bar-open fills, fee_bps + slippage_bps costs
  metrics.py      return / CAGR / Sharpe / maxDD / win% / excess-vs-hold
  optimize.py     param search: select in-sample, judge out-of-sample, overfit check
  paper.py        live paper trading (fake money) using the same engine logic
strategies/       *.json rule sets, plus *.tpl.json templates for the optimizer
backtest.py       run one strategy, print metrics vs buy-and-hold
walkforward.py    the honesty gate: in-sample vs out-of-sample
optimize.py       search a template, save the winning strategy
paper.py          open / tick / status / loop a paper account
```

## Usage

```bash
# backtest
python3 backtest.py strategies/sma_cross.json synthetic        # offline, deterministic
python3 backtest.py strategies/sma_cross.json BTC-USD 50       # ~4yr BTC daily, $50 start
python3 walkforward.py strategies/sma_cross.json BTC-USD 0.6   # gate: 60% train / 40% test

# evolve (the smart module): search params, honestly
python3 optimize.py strategies/sma_cross.tpl.json BTC-USD 0.6 excess

# paper trade forward on fake money (databorn stage)
python3 paper.py open btc1 strategies/sma_cross.json BTC-USD 50
python3 paper.py tick btc1        # run on a schedule; trades only when a bar closes
python3 paper.py status btc1
```

## Honesty knobs (don't cheat these)

- `fee_bps` / `slippage_bps` per strategy — real trading costs. A strategy that only
  wins at 0 bps has no edge. Coinbase taker is ~40–60 bps; default is 50+5.
- No-lookahead is enforced in `engine.py`: a signal from bar *i*'s close is acted on
  at bar *i+1*'s open. Never trade on info you couldn't have had.

## Built

- Paginated Coinbase history (~4yr daily) + multi-coin basket (`fetch_many`).
- Optimizer with the in-sample/out-of-sample split baked in + overfitting diagnostic.
- Paper-trading loop (live fake money, same engine/execution as backtest).
- **Itemized fee model** — `costs` block: taker/maker fee, half-spread, slippage.
  Taker pays fee+spread every trade (what silently bleeds scalpers); maker pays a
  lower fee and no spread but fills aren't guaranteed. `Strategy.breakeven_move`
  is the move each round trip must clear just to break even.
- **Fee gate** (`metrics.fee_gate`) — refuses any strategy whose average trade
  can't clear its fee wall. Wired into `backtest.py` (rejects on the spot) and
  `optimize.py` (fee-burners are discarded before ranking). The guardrail that
  stops "many small trades" from quietly losing to fees.

## The two gates (a strategy must pass BOTH)

1. **Fee gate** — does the typical trade clear its own cost wall? (survives fees)
2. **Edge gate** (walk-forward) — does it beat buy-and-hold out-of-sample? (has edge)

Passing the fee gate is necessary, not sufficient: a slow swing strategy can clear
fees easily and *still* have no real edge over holding. Both must hold before paper,
and paper must hold before a cent is real.

## Not built yet (roadmap)

- Scheduling the paper `tick` so it runs unattended (cron / the schedule skill).
- Intraday granularities; portfolio of multiple coins at once.
- Stocks via a second data adapter (engine is already asset-agnostic).
- A live broker adapter for real money — deliberately last, and gated on paper
  results matching backtest for a meaningful stretch.

## The uncomfortable truth the engine keeps showing

On ~4yr BTC, simple TA strategies "beat buy-and-hold" mostly by sitting in cash
during crashes — they cut drawdown but roughly match or lag holding over a full
cycle. When ~90% of optimizer candidates "beat hold" out-of-sample, that's not
found alpha; it's a structural property (trend-following dodges drawdowns), and
it is not a reason to bet money. Real edge looks like a robust family that beats
hold on *risk-adjusted return*, forward, after costs. Until paper shows that, the
honest expected outcome of $50 live is: roughly track BTC, minus fees.
```
