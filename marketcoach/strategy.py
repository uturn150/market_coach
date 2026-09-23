"""Strategy = a set of {when, then} rules, loaded from JSON. Same shape as a
deckcoach card's {trigger, effects}: a condition fires, an action happens.

Example rule:
  {"when": {"left": "sma:10", "op": "cross_above", "right": "sma:30"},
   "then": {"action": "buy", "size": "100%"}}

Refs ("left"/"right"): "price", "sma:N", "ema:N", "rsi:N", "roc:N", or a number.
Ops: ">", "<", ">=", "<=", "cross_above", "cross_below".
Actions: "buy" (size = % of cash) / "sell" (size = % of position, or "all").
Optional risk block: {"stop_loss_pct": 0.15, "take_profit_pct": 0.4}.
"""
import json
from . import indicators


class Strategy:
    def __init__(self, spec):
        self.name = spec.get("name", "unnamed")
        self.rules = spec.get("rules", [])
        self.entry = spec.get("entry")   # optional composite multi-signal buy block
        self.risk = spec.get("risk", {})
        self.costs = self._normalize_costs(spec)
        # Back-compat attributes still read elsewhere:
        self.fee_bps = self.costs["taker_fee_bps"]
        self.slippage_bps = self.costs["slippage_bps"]
        self._series_cache = {}

    @staticmethod
    def _normalize_costs(spec):
        """Every real cost, itemized so none can hide. All in basis points (1 bp =
        0.01%). A 'costs' block wins; otherwise fall back to legacy fee_bps/slippage.

        order_type 'taker' = market orders: you PAY the taker fee AND cross the
        spread (half_spread each side) — this is what quietly bleeds scalpers.
        order_type 'maker' = limit orders: you pay the (lower/rebate) maker fee and
        do NOT pay the spread — but fills are not guaranteed (see .fill_note).
        """
        has_block = "costs" in spec
        c = dict(spec.get("costs", {}))
        legacy_fee = spec.get("fee_bps", 60)
        return {
            "order_type": c.get("order_type", "taker"),
            "taker_fee_bps": c.get("taker_fee_bps", legacy_fee),
            "maker_fee_bps": c.get("maker_fee_bps", 25),
            # Only model spread when a costs block opts in; legacy files keep their
            # old all-in fee semantics so their numbers don't silently change.
            "half_spread_bps": c.get("half_spread_bps", 5 if has_block else 0),
            "slippage_bps": c.get("slippage_bps", spec.get("slippage_bps", 3)),
        }

    @property
    def side_rate(self):
        """Fraction charged against EACH fill (buy and sell), from the itemized
        costs. Round-trip cost ~= 2 * side_rate. This is the whole ballgame for
        high-frequency trading."""
        c = self.costs
        if c["order_type"] == "maker":
            return (c["maker_fee_bps"] + c["slippage_bps"]) / 10_000.0
        return (c["taker_fee_bps"] + c["half_spread_bps"] + c["slippage_bps"]) / 10_000.0

    @property
    def breakeven_move(self):
        """The price move a single round trip must clear just to break even."""
        return 2 * self.side_rate

    @property
    def fill_note(self):
        return ("maker/limit: fills NOT guaranteed — real results depend on your "
                "orders actually getting hit" if self.costs["order_type"] == "maker"
                else "taker/market: fills guaranteed, spread paid every time")

    @classmethod
    def load(cls, path):
        with open(path) as f:
            return cls(json.load(f))

    def _series(self, ref, prices, volumes=None, highs=None, lows=None):
        # Cache key includes id(prices)/id(highs)/id(lows), not just the ref name:
        # a Strategy instance reused across different coins' price arrays (as
        # paper_portfolio.tick() used to do) would otherwise have coin B silently
        # read coin A's cached "sma:20" series — a real bug this caused until
        # caught by reconcile.py. Keying on the arrays' identity makes that
        # impossible regardless of how callers reuse an instance, while still
        # caching correctly within repeated calls for the SAME arrays (the
        # normal, intended case).
        key = (str(ref), id(prices), id(highs), id(lows))
        if key not in self._series_cache:
            self._series_cache[key] = indicators.resolve_series(
                ref, prices, volumes, highs, lows)
        return self._series_cache[key]

    def _fires(self, when, i, prices, volumes=None, highs=None, lows=None):
        """Evaluate one {left, op, right} condition at bar i."""
        L = self._series(when["left"], prices, volumes, highs, lows)
        R = self._series(when["right"], prices, volumes, highs, lows)
        l_prev = L[i - 1] if i > 0 else None
        r_prev = R[i - 1] if i > 0 else None
        return self._cmp(when["op"], L[i], l_prev, R[i], r_prev)

    def _cmp(self, op, l_now, l_prev, r_now, r_prev):
        if l_now is None or r_now is None:
            return False
        if op == ">":  return l_now > r_now
        if op == "<":  return l_now < r_now
        if op == ">=": return l_now >= r_now
        if op == "<=": return l_now <= r_now
        if op in ("cross_above", "cross_below"):
            if l_prev is None or r_prev is None:
                return False
            if op == "cross_above":
                return l_prev <= r_prev and l_now > r_now
            return l_prev >= r_prev and l_now < r_now
        raise ValueError(f"unknown op: {op}")

    def entry_check(self, i, prices, volumes=None, highs=None, lows=None):
        """Composite entry: sum the weights of every signal firing at bar i and
        compare to the threshold. Returns (should_buy, fired_names, score, threshold).
        'Numerous things' — set threshold=1 to catch an uptrend on ANY signal, or
        higher to demand confirmation (e.g. volume AND momentum AND breakout)."""
        e = self.entry
        thr = e.get("threshold", 1)
        fired, score = [], 0.0
        for sig in e["signals"]:
            if self._fires(sig["when"], i, prices, volumes, highs, lows):
                w = sig.get("weight", 1)
                score += w
                fired.append(sig.get("name", "?"))
        return score >= thr, fired, score, thr

    @staticmethod
    def _describe(when):
        """Human-readable one-liner for a {left, op, right} condition, e.g.
        'rsi:10 cross_below 25' — so every logged trade can say exactly which
        condition fired, not just that a buy/sell happened. This is what lets
        the learning pass (and a human) later tell a bad ENTRY rule apart from
        a bad EXIT rule instead of guessing from price action alone."""
        return f"{when['left']} {when['op']} {when['right']}"

    def signals_at(self, i, prices, volumes=None, highs=None, lows=None):
        """Actions firing at bar i, each tagged with `why` (which condition
        fired). If an `entry` block is present it drives BUYs (multi-signal
        conviction) and `rules` supply SELLs; otherwise `rules` drive
        everything (back-compat with single-condition strategies)."""
        fired = []
        if self.entry:
            ok, names, score, thr = self.entry_check(i, prices, volumes, highs, lows)
            if ok:
                fired.append({"action": "buy", "size": self.entry.get("size", "100%"),
                              "why": ", ".join(names), "score": score})
            for rule in self.rules:
                if rule["then"].get("action") == "sell" and self._fires(rule["when"], i, prices, volumes, highs, lows):
                    fired.append({**rule["then"], "why": self._describe(rule["when"])})
        else:
            for rule in self.rules:
                if self._fires(rule["when"], i, prices, volumes, highs, lows):
                    fired.append({**rule["then"], "why": self._describe(rule["when"])})
        return fired
