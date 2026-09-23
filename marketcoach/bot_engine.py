"""Order-level bot engine: the kind of bots trading communities actually run (grid, DCA/martingale
cycle, momentum rotation) need RESTING ORDERS, laddered entries and rebalancing, which the signal
engine (engine.py: one long position per coin, all-in) cannot express. Same honesty rules:

  * No lookahead. A bot acts at the CLOSE of bar i. Market orders fill at the OPEN of bar i+1 (taker
    fee + half-spread + slippage). Limit orders rest from bar i+1 on.
  * A resting limit order fills only if price trades THROUGH it (low <= buy limit*(1-through), high >=
    sell limit*(1+through)); it fills at its limit price, maker fee, no price improvement, and an order
    cannot be placed and filled inside the same bar. (Sensitivity to `through` was the weak spot in
    the maker study, so the default is conservative: 5bps.)
  * Cash for a resting buy and coins for a resting sell are reserved; nothing can go negative.
  * Real fee tier (kraken_fees) unless overridden.
Bots implement on_bar(i, ctx) and optionally on_fill(i, ctx, order, price) and call ctx.limit/market/cancel.
"""
from collections import namedtuple

Result = namedtuple("Result", "equity ts trips stats fills ctx")


class Order:
    __slots__ = ("id", "coin", "side", "kind", "price", "units", "born", "ttl", "tag")

    def __init__(self, id, coin, side, kind, price, units, born, ttl, tag):
        self.id, self.coin, self.side, self.kind, self.price, self.units = id, coin, side, kind, price, units
        self.born, self.ttl, self.tag = born, ttl, tag


class Ctx:
    def __init__(self, bars, cash, maker, taker, hs, slip, through):
        self.bars, self.coins = bars, list(bars)
        self.n = len(bars[self.coins[0]])
        self.cash = cash
        self.pos = {c: 0.0 for c in self.coins}
        self.cost_basis = {c: 0.0 for c in self.coins}      # $ paid (incl. fees) for units currently held
        self.orders, self.pending_market = {}, []
        self.maker, self.taker, self.hs, self.slip, self.through = maker, taker, hs, slip, through
        self._next_id = 1
        self.trips = []            # (coin, hold_bars, net_return)
        self.fills = []            # every fill: dict(ts, coin, side, price, units, notional|proceeds)
        self._entry = {c: None for c in self.coins}
        self.stats = {"limit_fills": 0, "market_fills": 0, "placed": 0, "expired": 0}
        self.i = 0
        self.min_order = 0.0      # minimum notional per order in $ (exchange minimum); 0 = unlimited divisibility (old behaviour)
        self.skipped_min = 0
        self.rules = None         # per-coin Kraken rules {coin: {ordermin, costmin, lot_decimals, tick_size}}; None = old behaviour
        self.rule_rejects = {"below_min": 0, "dust_sell": 0}
        self.order_step = 0.0     # >0: every BUY is sized in whole multiples of this many dollars (e.g. 10), never below the coin's real minimum

    # ---- reads
    def close(self, coin, i=None):
        return self.bars[coin][self.i if i is None else i].close

    def reserved_cash(self):
        return sum(o.units * o.price * (1 + self.maker) for o in self.orders.values() if o.side == "buy")

    def reserved_units(self, coin):
        return sum(o.units for o in self.orders.values() if o.side == "sell" and o.coin == coin)

    def free_cash(self):
        return self.cash - self.reserved_cash()

    def free_units(self, coin):
        return self.pos[coin] - self.reserved_units(coin)

    def equity(self, i=None):
        return self.cash + sum(self.pos[c] * self.close(c, i) for c in self.coins)

    def orders_for(self, coin, side=None):
        return [o for o in self.orders.values() if o.coin == coin and (side is None or o.side == side)]

    # ---- Kraken rules
    def fit(self, coin, side, price, units):
        """Round an order to what Kraken accepts: price to the tick, size DOWN to the lot decimals, then reject if under the coin's
        minimum size or minimum cost. Returns (price, units) or None."""
        r = self.rules.get(coin) if self.rules else None
        if r is None:
            return price, units
        import math
        if r.get("tick_size"):
            price = round(round(price / r["tick_size"]) * r["tick_size"], 12)
        f = 10 ** r["lot_decimals"]
        if side == "buy" and self.order_step > 0:
            st = self.order_step
            q = max(round(units * price / st), 1) * st              # nearest whole step, at least one step
            for _ in range(20):                                    # bump one step at a time until Kraken would accept it
                u = math.floor(q / price * f + 1e-9) / f
                if u >= r["ordermin"] - 1e-12 and u * price >= r["costmin"]:
                    break
                q += st
            units = q / price
        units = math.floor(units * f + 1e-9) / f
        if units <= 0 or units < r["ordermin"] - 1e-12 or units * price < r["costmin"]:
            self.rule_rejects["dust_sell" if side == "sell" else "below_min"] += 1
            return None
        return price, units

    # ---- actions
    def limit(self, coin, side, price, units, ttl=None, tag=None):
        if units <= 0 or price <= 0:
            return None
        if self.rules:
            fitted = self.fit(coin, side, price, units)
            if fitted is None:
                return None
            price, units = fitted
        if units * price < self.min_order:
            self.skipped_min += 1
            return None
        if side == "buy" and units * price * (1 + self.maker) > self.free_cash() + 1e-9:
            return None
        if side == "sell" and units > self.free_units(coin) + 1e-12:
            return None
        o = Order(self._next_id, coin, side, "limit", price, units, self.i, ttl, tag)
        self._next_id += 1
        self.orders[o.id] = o
        self.stats["placed"] += 1
        return o

    def market(self, coin, side, units=None, notional=None):
        self.pending_market.append((coin, side, units, notional))

    def cancel(self, order):
        if order is not None:
            self.orders.pop(order.id, None)

    def cancel_all(self, coin=None, side=None):
        for o in list(self.orders.values()):
            if (coin is None or o.coin == coin) and (side is None or o.side == side):
                del self.orders[o.id]

    # ---- accounting
    def _buy(self, coin, units, price, fee_rate, i):
        cost = units * price * (1 + fee_rate)
        self.cash -= cost
        if self.pos[coin] < 1e-12:
            self._entry[coin] = (i, 0.0)
        self.pos[coin] += units
        self.cost_basis[coin] += cost
        self.fills.append({"ts": self.bars[coin][i].ts, "coin": coin, "side": "buy", "price": price, "units": units, "notional": cost})

    def _sell(self, coin, units, price, fee_rate, i):
        held = self.pos[coin]
        units = min(units, held)
        if units <= 0:
            return 0.0
        proceeds = units * price * (1 - fee_rate)
        frac = units / held
        basis = self.cost_basis[coin] * frac
        self.cash += proceeds
        self.fills.append({"ts": self.bars[coin][i].ts, "coin": coin, "side": "sell", "price": price, "units": units, "proceeds": proceeds})
        self.pos[coin] -= units
        self.cost_basis[coin] -= basis
        if basis > 0:
            e = self._entry[coin]
            self.trips.append((coin, i - (e[0] if e else i), proceeds / basis - 1))
        if self.pos[coin] < 1e-12:
            self.pos[coin], self.cost_basis[coin], self._entry[coin] = 0.0, 0.0, None
        return proceeds


def run(bot, bars, cash=100.0, maker=None, taker=None, half_spread=0.00024, slip=0.00005, through=0.0005, min_order=0.0, decide_last=False, rules=None, order_step=0.0):
    if maker is None or taker is None:
        from . import kraken_fees
        f = kraken_fees.get_fee_bps()
        maker = f["maker_bps"] / 1e4 if maker is None else maker
        taker = f["taker_bps"] / 1e4 if taker is None else taker
    ctx = Ctx(bars, cash, maker, taker, half_spread, slip, through)
    ctx.min_order = min_order
    ctx.rules = rules
    ctx.order_step = order_step
    n = ctx.n
    ts = [b.ts for b in bars[ctx.coins[0]]]
    eq = []
    for i in range(n):
        ctx.i = i
        # 1) market orders decided at the previous close -> fill at THIS open
        pend, ctx.pending_market = ctx.pending_market, []
        for coin, side, units, notional in pend:
            o = bars[coin][i].open
            if side == "buy":
                px = o * (1 + half_spread + slip)
                u = units if units is not None else (notional or 0) / (px * (1 + taker))
                budget = ctx.free_cash()
                u = min(u, budget / (px * (1 + taker)))
                if u * px < ctx.min_order:
                    ctx.skipped_min += 1
                    u = 0.0
                if u > 1e-12 and ctx.rules:
                    ft = ctx.fit(coin, "buy", px, u)
                    u = ft[1] if ft else 0.0
                if u > 1e-12:
                    ctx._buy(coin, u, px, taker, i); ctx.stats["market_fills"] += 1
            else:
                px = o * (1 - half_spread - slip)
                u = units if units is not None else ctx.free_units(coin)
                u = min(u, ctx.free_units(coin)) if units is not None else ctx.free_units(coin)
                if u > 1e-12 and ctx.rules:
                    ft = ctx.fit(coin, "sell", px, u)
                    u = ft[1] if ft else 0.0
                if u > 1e-12:
                    ctx._sell(coin, u, px, taker, i); ctx.stats["market_fills"] += 1
        # 2) resting limit orders vs this bar (only orders born before this bar)
        b_cache = {c: bars[c][i] for c in ctx.coins}
        fills = []
        for oid, o in list(ctx.orders.items()):
            if o.born >= i:
                continue
            b = b_cache[o.coin]
            if o.side == "buy" and b.low <= o.price * (1 - through):
                if o.units * o.price * (1 + maker) <= ctx.cash + 1e-9:
                    ctx._buy(o.coin, o.units, o.price, maker, i); fills.append(o); del ctx.orders[oid]
            elif o.side == "sell" and b.high >= o.price * (1 + through):
                if o.units <= ctx.pos[o.coin] + 1e-12:
                    ctx._sell(o.coin, o.units, o.price, maker, i); fills.append(o); del ctx.orders[oid]
        ctx.stats["limit_fills"] += len(fills)
        for o in fills:
            if hasattr(bot, "on_fill"):
                bot.on_fill(i, ctx, o, o.price)
        # 3) expire
        for oid, o in list(ctx.orders.items()):
            if o.ttl is not None and i - o.born >= o.ttl:
                del ctx.orders[oid]; ctx.stats["expired"] += 1
        # 4) mark to market at close, then let the bot act
        eq.append(ctx.equity(i))
        if i < n - 1 or decide_last:      # decide_last: live re-sim lets the bot act on the newest closed bar too, so its resting orders are visible
            bot.on_bar(i, ctx)
    if rules:
        ctx.stats.update(ctx.rule_rejects)
    return Result(eq, ts, ctx.trips, ctx.stats, ctx.fills, ctx)
