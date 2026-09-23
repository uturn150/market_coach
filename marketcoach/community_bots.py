"""The bots trading communities actually run, built on bot_engine (order-level, real fees).

GridBot        capital split per coin; buy-limit ladder below price, each fill places a sell one step up.
               Profits from chop, bleeds in a one-way drop -> hard range-break stop + optional market filter.
DCACycleBot    'martingale' cycle bot: base order at market, safety-order ladder below (bigger each step),
               take-profit limit above the running average cost; new cycle when flat. Very high win rate,
               fat left tail -> optional stop.
RotationBot    every `every` days rank coins by momentum, hold the top K equal-weight (market orders),
               cash when the market filter is off.
Parameters are fixed grids in bot_lab.py BEFORE results are looked at.
"""


class GridBot:
    def __init__(self, levels=6, step=0.03, coin_filter=None, market_ok=None, stop_steps=1.0, cooldown=10, cap_per_coin=None):
        self.levels, self.step, self.market_ok, self.stop_steps, self.cooldown = levels, step, market_ok, stop_steps, cooldown
        self.grid = {}          # coin -> {"center":, "budget":, "cool_until":}
        self.sell_of = {}       # buy-level price key -> None (unused; sells placed on fill)

    def _start(self, ctx, coin, i):
        c = ctx.close(coin, i)
        budget = ctx.free_cash() / max(1, len(ctx.coins) - sum(1 for g in self.grid.values() if g.get("active")))
        budget = min(budget, ctx.equity(i) / len(ctx.coins))
        per = budget / self.levels
        g = {"active": True, "center": c, "per": per, "cool_until": 0}
        self.grid[coin] = g
        for k in range(1, self.levels + 1):
            p = c * (1 - self.step * k)
            ctx.limit(coin, "buy", p, per / p / (1 + ctx.maker), tag=("lvl", k))
        return g

    def on_bar(self, i, ctx):
        ok = self.market_ok(i) if self.market_ok else True
        for coin in ctx.coins:
            g = self.grid.get(coin)
            c = ctx.close(coin, i)
            if g and g.get("active"):
                floor = g["center"] * (1 - self.step * (self.levels + self.stop_steps))
                if c < floor or not ok:
                    ctx.cancel_all(coin)
                    if ctx.pos[coin] > 1e-12:
                        ctx.market(coin, "sell")
                    g["active"] = False
                    g["cool_until"] = i + self.cooldown
                elif not ctx.orders_for(coin) and ctx.pos[coin] < 1e-12:
                    g["active"] = False          # everything cycled out: re-center next bar
                    g["cool_until"] = i
            elif ok and (not g or i >= g["cool_until"]) and ctx.pos[coin] < 1e-12:
                self._start(ctx, coin, i)

    def on_fill(self, i, ctx, order, price):
        g = self.grid.get(order.coin)
        if not g or not g.get("active"):
            return
        if order.side == "buy":
            ctx.limit(order.coin, "sell", price * (1 + self.step), order.units, tag=("sell", order.tag))
        else:   # sold one step up: re-arm the buy at the level below
            lvl = price / (1 + self.step)
            ctx.limit(order.coin, "buy", lvl, g["per"] / lvl / (1 + ctx.maker), tag=("lvl", None))


class BollinGridBot:
    """Faithful-mechanics port of Hummingbot's bollingrid controller (controllers/directional_trading/bollingrid.py):
    on a Bollinger %B dip (BBP(bb_length, bb_std) < long_threshold, i.e. price at/through the lower band), open a GRID
    of buy orders spanning [close*(1-w*start_coef), close] where w = band width / mid-price, with a matching sell ladder
    one grid-spacing above each fill (classic grid mechanics, reused from GridBot) -- and a hard stop at
    close*(1-w*limit_coef) that cancels the whole grid and exits if price falls through it. Long side only (the
    original's short side needs margin/perps, out of scope here). Deviations: bar-close BBP trigger (not a
    resting/algorithmic order placement schedule); levels count is a parameter, not derived from order_frequency."""
    def __init__(self, bb_len=100, bb_std=2.0, long_thresh=0.0, start_coef=0.25, end_coef=0.75, limit_coef=0.35,
                 levels=5, market_ok=None, cooldown=5):
        self.bb_len, self.bb_std, self.long_thresh = bb_len, bb_std, long_thresh
        self.start_coef, self.end_coef, self.limit_coef, self.levels = start_coef, end_coef, limit_coef, levels
        self.market_ok, self.cooldown = market_ok, cooldown
        self.grid = {}     # coin -> {"active", "step", "stop", "cool_until"}
        self._bb = {}      # coin -> (mid, upper, lower) memo per bar index, recomputed lazily

    def _bbp(self, ctx, coin, i):
        closes = [b.close for b in ctx.bars[coin][:i + 1]]
        if len(closes) < self.bb_len:
            return None, None
        win = closes[-self.bb_len:]
        mid = sum(win) / len(win)
        var = sum((x - mid) ** 2 for x in win) / len(win)
        sd = var ** 0.5
        upper, lower = mid + self.bb_std * sd, mid - self.bb_std * sd
        c = closes[-1]
        bbp = (c - lower) / (upper - lower) if upper > lower else 0.5
        width = (upper - lower) / mid if mid else 0.0
        return bbp, width

    def _start(self, ctx, coin, i, c, width):
        budget = min(ctx.free_cash() / max(1, len(ctx.coins) - sum(1 for g in self.grid.values() if g.get("active"))),
                     ctx.equity(i) / len(ctx.coins))
        lo = c * (1 - width * self.start_coef)
        hi = c * (1 + width * self.end_coef)
        stop = c * (1 - width * self.limit_coef)
        step = (c - lo) / self.levels if self.levels else 0
        per = budget / self.levels
        g = {"active": True, "stop": stop, "hi": hi, "step": max(step, c * 1e-4), "cool_until": 0}
        self.grid[coin] = g
        for k in range(1, self.levels + 1):
            p = c - g["step"] * k
            if p > 0:
                ctx.limit(coin, "buy", p, per / p / (1 + ctx.maker), tag=("lvl", k))

    def on_bar(self, i, ctx):
        ok = self.market_ok(i) if self.market_ok else True
        for coin in ctx.coins:
            g = self.grid.get(coin)
            c = ctx.close(coin, i)
            if g and g.get("active"):
                if c < g["stop"] or not ok:
                    ctx.cancel_all(coin)
                    if ctx.pos[coin] > 1e-12:
                        ctx.market(coin, "sell")
                    g["active"] = False; g["cool_until"] = i + self.cooldown
                elif not ctx.orders_for(coin) and ctx.pos[coin] < 1e-12:
                    g["active"] = False; g["cool_until"] = i
            elif ok and (not g or i >= g["cool_until"]) and ctx.pos[coin] < 1e-12:
                bbp, width = self._bbp(ctx, coin, i)
                if bbp is not None and bbp < self.long_thresh:
                    self._start(ctx, coin, i, c, width)

    def on_fill(self, i, ctx, order, price):
        g = self.grid.get(order.coin)
        if not g or not g.get("active"):
            return
        if order.side == "buy":
            sell_px = min(price + g["step"], g["hi"])
            if sell_px > price:
                ctx.limit(order.coin, "sell", sell_px, order.units, tag=("sell", order.tag))


class DCACycleBot:
    def __init__(self, safety=4, dev=0.04, mult=1.6, tp=0.02, stop=None, market_ok=None, base_frac=None, max_days=60, base_limit=None, tp_decay=None, coin_trend_ok=None):
        self.safety, self.dev, self.mult, self.tp, self.stop, self.market_ok, self.max_days = safety, dev, mult, tp, stop, market_ok, max_days
        self.tp_decay = tp_decay              # list of (days_in_cycle, tp): Freqtrade-style time-decaying ROI table, e.g. [(0,.03),(10,.015),(30,.005)]
        self.coin_trend_ok = coin_trend_ok    # optional per-coin filter f(coin, i) -> bool (e.g. coin above its own 200-day average)
        self.base_limit = base_limit          # e.g. 0.002 = rest the base order 0.2% below the close as a MAKER order (saves the taker fee)
        self.st = {}

    def params(self, i):
        """(safety, dev, mult, tp) for a cycle STARTING at bar i. Subclasses can make this regime-dependent."""
        return self.safety, self.dev, self.mult, self.tp

    def on_bar(self, i, ctx):
        ok = self.market_ok(i) if self.market_ok else True
        for coin in ctx.coins:
            s = self.st.setdefault(coin, {"phase": "flat", "start": 0, "so": []})
            c = ctx.close(coin, i)
            held = ctx.pos[coin] > 1e-12
            if s["phase"] == "cycle":
                if not held and not ctx.orders_for(coin, "sell"):      # tp filled: cycle done
                    ctx.cancel_all(coin); s["phase"] = "flat"
                elif held:
                    avg = ctx.cost_basis[coin] / ctx.pos[coin]
                    if self.tp_decay:                                   # slide the take-profit down as the cycle ages
                        age = i - s["start"]; tp_now = [t for d, t in self.tp_decay if age >= d][-1]
                        if abs(tp_now - s.get("tp_cur", s["tp"])) > 1e-12:
                            for o in ctx.orders_for(coin, "sell"): ctx.cancel(o)
                            ctx.limit(coin, "sell", avg * (1 + tp_now), ctx.free_units(coin), tag="tp"); s["tp_cur"] = tp_now
                    if (self.stop is not None and c < avg * (1 - self.stop)) or i - s["start"] > self.max_days or not ok:
                        ctx.cancel_all(coin); ctx.market(coin, "sell"); s["phase"] = "flat"
            if s["phase"] == "flat" and ok and not held and not ctx.orders_for(coin) and (self.coin_trend_ok is None or self.coin_trend_ok(coin, i)):
                so_n, dev, mult, tp = self.params(i)
                if so_n is None:            # regime says: do not start a cycle now
                    continue
                budget = min(ctx.free_cash(), ctx.equity(i) / len(ctx.coins))
                tot = sum(mult ** k for k in range(so_n + 1))
                base = budget / tot
                s.update(phase="await_base", start=i, base=base, so_n=so_n, dev=dev, mult=mult, tp=tp)
                if self.base_limit:
                    px = c * (1 - self.base_limit)
                    if ctx.limit(coin, "buy", px, base / px / (1 + ctx.maker), ttl=2, tag="base") is None:
                        s["phase"] = "flat"
                else:
                    ctx.market(coin, "buy", notional=base)
            elif s["phase"] == "await_base" and held:
                # base order filled at this open: lay the safety ladder and the take-profit
                avg = ctx.cost_basis[coin] / ctx.pos[coin]
                s["phase"] = "cycle"
                px, size = avg, s["base"]
                for k in range(1, s["so_n"] + 1):
                    px = px * (1 - s["dev"] * (1 + 0.5 * (k - 1)))
                    size = size * s["mult"]
                    ctx.limit(coin, "buy", px, size / px / (1 + ctx.maker), tag=("so", k))
                ctx.limit(coin, "sell", avg * (1 + s["tp"]), ctx.free_units(coin), tag="tp")
            elif s["phase"] == "await_base" and not held:
                if not ctx.orders_for(coin):
                    s["phase"] = "flat"       # base order could not fill (no cash) or the limit expired unfilled

    def on_fill(self, i, ctx, order, price):
        if order.side == "buy" and ctx.pos[order.coin] > 1e-12:
            for o in ctx.orders_for(order.coin, "sell"):
                ctx.cancel(o)
            avg = ctx.cost_basis[order.coin] / ctx.pos[order.coin]
            tp = self.st.get(order.coin, {}).get("tp", self.tp)
            ctx.limit(order.coin, "sell", avg * (1 + tp), ctx.free_units(order.coin), tag="tp")


class RotationBot:
    def __init__(self, top=3, lookback=30, every=7, market_ok=None, min_trade=0.02):
        self.top, self.lb, self.every, self.market_ok, self.min_trade = top, lookback, every, market_ok, min_trade

    def on_bar(self, i, ctx):
        if i < self.lb or i % self.every:
            return
        ok = self.market_ok(i) if self.market_ok else True
        eq = ctx.equity(i)
        if not ok:
            targets = {}
        else:
            mom = {c: ctx.close(c, i) / ctx.close(c, i - self.lb) - 1 for c in ctx.coins}
            best = [c for c, m in sorted(mom.items(), key=lambda kv: -kv[1])[:self.top] if m > 0]
            targets = {c: eq / max(1, len(best)) * 0.98 for c in best}
        for c in ctx.coins:
            cur = ctx.pos[c] * ctx.close(c, i)
            want = targets.get(c, 0.0)
            if cur - want > self.min_trade * eq:
                ctx.market(c, "sell", units=(cur - want) / ctx.close(c, i))
        for c, want in targets.items():
            cur = ctx.pos[c] * ctx.close(c, i)
            if want - cur > self.min_trade * eq:
                ctx.market(c, "buy", notional=want - cur)


class Breaker:
    """Account-level drawdown circuit breaker around ANY bot. If total equity falls `dd_limit` below its
    peak: cancel every resting order, market-sell everything, and stay out for `pause_days`; then re-arm
    with a fresh peak. Exits are taker orders (real costs). Paper-only, like everything here."""
    def __init__(self, bot, dd_limit=0.12, pause_days=30):
        self.bot, self.dd_limit, self.pause_days = bot, dd_limit, pause_days
        self.peak, self.until, self.trips = None, -1, 0

    def on_bar(self, i, ctx):
        eq = ctx.equity(i)
        if i < self.until:
            return
        if self.until >= 0 and i >= self.until:
            self.peak, self.until = eq, -1          # re-arm
        self.peak = eq if self.peak is None else max(self.peak, eq)
        if eq / self.peak - 1 <= -self.dd_limit:
            ctx.cancel_all()
            for c in ctx.coins:
                if ctx.pos[c] > 1e-12:
                    ctx.market(c, "sell")
            self.until, self.trips = i + self.pause_days, self.trips + 1
            # bots keep internal state (cycles/grids); reset it so they restart cleanly after the pause
            for attr in ("grid", "st"):
                if hasattr(self.bot, attr):
                    setattr(self.bot, attr, {})
            return
        self.bot.on_bar(i, ctx)

    def on_fill(self, i, ctx, order, price):
        if i >= self.until >= 0 or self.until < 0:
            if hasattr(self.bot, "on_fill"):
                self.bot.on_fill(i, ctx, order, price)


class SeasonalBot:
    """Calendar effect: buy all coins (equal weight) at the close before `buy_dow`, sell `hold` days later."""
    def __init__(self, buy_dow=6, hold=2, market_ok=None):
        self.dow, self.hold, self.market_ok = buy_dow, hold, market_ok
        self.exit_at = None

    def on_bar(self, i, ctx):
        dow = ((ctx.bars[ctx.coins[0]][i].ts // 86400) + 4) % 7      # 0=Mon
        if self.exit_at is not None and i >= self.exit_at:
            for c in ctx.coins:
                if ctx.pos[c] > 1e-12: ctx.market(c, "sell")
            self.exit_at = None
        elif self.exit_at is None and (dow + 1) % 7 == self.dow and (self.market_ok is None or self.market_ok(i)):
            per = ctx.free_cash() / len(ctx.coins) * 0.98
            for c in ctx.coins: ctx.market(c, "buy", notional=per)
            self.exit_at = i + self.hold


class CatchUpBot:
    """Relative value without shorting: every `every` days buy the K coins that lagged BTC the most over
    `lb` days (only if still above their own 50-day average), hold `hold` days."""
    def __init__(self, k=3, lb=14, hold=14, every=7, market_ok=None):
        self.k, self.lb, self.hold, self.every, self.market_ok = k, lb, hold, every, market_ok
        self.exit_at = {}

    def on_bar(self, i, ctx):
        for c, t in list(self.exit_at.items()):
            if i >= t:
                if ctx.pos[c] > 1e-12: ctx.market(c, "sell")
                del self.exit_at[c]
        if i < 60 or i % self.every or (self.market_ok and not self.market_ok(i)):
            return
        btc = ctx.close("BTC-USD", i) / ctx.close("BTC-USD", i - self.lb) - 1
        cand = []
        for c in ctx.coins:
            if c == "BTC-USD" or c in self.exit_at: continue
            cl = [ctx.close(c, j) for j in range(i - 49, i + 1)]
            if ctx.close(c, i) < sum(cl) / 50: continue
            cand.append((ctx.close(c, i) / ctx.close(c, i - self.lb) - 1 - btc, c))
        for _, c in sorted(cand)[: self.k]:
            per = ctx.free_cash() / max(1, self.k) * 0.95
            ctx.market(c, "buy", notional=per); self.exit_at[c] = i + self.hold


class VolSpikeBot:
    """Panic-day reversion: a coin's 1-day drop <= -k x its own 20-day stdev -> buy, exit after `hold` days or at +tp."""
    def __init__(self, k=2.5, hold=3, tp=0.05, market_ok=None):
        self.k, self.hold, self.tp, self.market_ok = k, hold, tp, market_ok
        self.exit_at = {}

    def on_bar(self, i, ctx):
        for c, t in list(self.exit_at.items()):
            if ctx.pos[c] <= 1e-12: del self.exit_at[c]; continue
            if i >= t: ctx.cancel_all(c); ctx.market(c, "sell"); del self.exit_at[c]
        if i < 30 or (self.market_ok and not self.market_ok(i)):
            return
        for c in ctx.coins:
            if c in self.exit_at: continue
            r = [ctx.close(c, j) / ctx.close(c, j - 1) - 1 for j in range(i - 19, i + 1)]
            m = sum(r[:-1]) / 19; sd = (sum((x - m) ** 2 for x in r[:-1]) / 18) ** 0.5
            if sd > 0 and r[-1] <= -self.k * sd and r[-1] < -0.03:
                ctx.market(c, "buy", notional=min(ctx.free_cash(), ctx.equity(i) / len(ctx.coins)) * 0.95)
                self.exit_at[c] = i + self.hold


class AdaptiveDCA(DCACycleBot):
    """DCA cycle bot whose ladder spacing / take-profit / safety count are CHOSEN BY MARKET REGIME from a learned
    table {regime: (safety, dev, mult, tp) | None}. `table_at(i)` returns the table valid at bar i (it may change
    over time as the table is re-learned); `regime_at(i)` returns the PREVIOUS day's regime label."""
    def __init__(self, table_at, regime_at, default=(5, 0.05, 2.0, 0.03), **kw):
        super().__init__(**kw)
        self.table_at, self.regime_at, self.default = table_at, regime_at, default

    def params(self, i):
        lab = self.regime_at(i)
        tbl = self.table_at(i)
        if lab in tbl:
            return tbl[lab] if tbl[lab] is not None else (None, 0, 0, 0)
        return self.default


class SlotBot:
    """Small-account dip trader with FIXED-SIZE positions ("slots"): at most `max_slots` cycles at once, each risking
    `slot_usd` (base order + `ladder_n` equal safety orders), take-profit `tp` above average cost, optional hard stop.
    Each day it starts cycles in the flat coins that fell the most over 3 days (needs a 1-day drop of at least `dip_min`).
    Respects a minimum order value (Kraken's real minimums are ~$4-15 per order)."""
    def __init__(self, slot_usd=10.0, max_slots=5, ladder_n=0, dev=0.04, tp=0.02, stop=None, dip_min=0.02, min_order=7.0, market_ok=None, max_days=30):
        self.slot, self.max_slots, self.n, self.dev, self.tp, self.stop = slot_usd, max_slots, ladder_n, dev, tp, stop
        self.dip_min, self.min_order, self.market_ok, self.max_days = dip_min, min_order, market_ok, max_days
        self.st = {}

    def on_bar(self, i, ctx):
        ok = self.market_ok(i) if self.market_ok else True
        active = 0
        for coin in ctx.coins:
            s = self.st.setdefault(coin, {"phase": "flat"})
            held = ctx.pos[coin] > 1e-12
            if s["phase"] == "await" and held:
                avg = ctx.cost_basis[coin] / ctx.pos[coin]
                per = self.slot / (1 + self.n)
                for k in range(1, self.n + 1):
                    px = avg * (1 - self.dev * k)
                    if per >= self.min_order:
                        ctx.limit(coin, "buy", px, per / px / (1 + ctx.maker), tag=("so", k))
                ctx.limit(coin, "sell", avg * (1 + self.tp), ctx.free_units(coin), tag="tp")
                s.update(phase="cycle", start=i)
            elif s["phase"] == "await" and not held:
                s["phase"] = "flat"
            if s["phase"] == "cycle":
                if not held and not ctx.orders_for(coin, "sell"):
                    ctx.cancel_all(coin); s["phase"] = "flat"
                elif held:
                    avg = ctx.cost_basis[coin] / ctx.pos[coin]
                    if (self.stop is not None and ctx.close(coin, i) < avg * (1 - self.stop)) or i - s["start"] > self.max_days or not ok:
                        ctx.cancel_all(coin); ctx.market(coin, "sell"); s["phase"] = "flat"
            if s["phase"] in ("cycle", "await"):
                active += 1
        if not ok or active >= self.max_slots:
            return
        cands = []
        for coin in ctx.coins:
            s = self.st[coin]
            if s["phase"] != "flat" or ctx.pos[coin] > 1e-12 or ctx.orders_for(coin) or i < 4:
                continue
            d1 = ctx.close(coin, i) / ctx.close(coin, i - 1) - 1
            d3 = ctx.close(coin, i) / ctx.close(coin, i - 3) - 1
            if d1 <= -self.dip_min:
                cands.append((d3, coin))
        for _, coin in sorted(cands)[: self.max_slots - active]:
            base = self.slot / (1 + self.n)
            if base < self.min_order or ctx.free_cash() < self.slot * 0.98:
                continue
            ctx.market(coin, "buy", notional=base)
            self.st[coin] = {"phase": "await"}
            active += 1

    def on_fill(self, i, ctx, order, price):
        if order.side == "buy" and ctx.pos[order.coin] > 1e-12:
            for o in ctx.orders_for(order.coin, "sell"):
                ctx.cancel(o)
            avg = ctx.cost_basis[order.coin] / ctx.pos[order.coin]
            ctx.limit(order.coin, "sell", avg * (1 + self.tp), ctx.free_units(order.coin), tag="tp")


class NFIXLite:
    """NostalgiaForInfinityX-STYLE dip system (a design-inspired reimplementation, NOT the GPL code): many independent dip
    entry modes OR'd together, each guarded by protections; profit-dependent RSI exits + trailing take-profit; a small
    'grinding' rebuy ladder on losing positions; a doom stop; several concurrent slots over many coins.
    Runs on ANY bar size (indicator periods are in bars; `bpd` = bars per day scales the day-based rules).
      entry modes  m1 RSI14<30 & below lower Bollinger & volume spike     m2 RSI4<12 & 3% under EMA50 & CCI<-150
                   m3 24h drop >=6% & close in the lowest quarter of the 24h range     m4 Williams %R<-92 & StochK<10 & 5% under SMA20
                   m5 12% below the 3-day high & RSI14<35
      protections  BTC not dumping (24h > -6%), optional market filter, coin 7-day return > -35%, per-coin cooldown after a stop,
                   max concurrent slots
      exits        profit>=2% & RSI>65 | profit>=4% & RSI>55 | profit>=6% | trailing TP (arms at +3%, exits on 0.8% pullback)
                   | time stop | doom stop -18%
    """
    def __init__(self, slots=8, modes=("m1", "m2", "m3", "m4", "m5"), rebuys=2, rebuy_dev=0.06, bpd=24, maker_entry=None,
                 market_ok=None, doom=0.18, cooldown_days=3, max_hold_days=10, min_order=0.0):
        self.slots, self.modes, self.rebuys, self.rebuy_dev, self.bpd = slots, modes, rebuys, rebuy_dev, bpd
        self.maker_entry, self.market_ok, self.doom, self.cool, self.max_hold = maker_entry, market_ok, doom, cooldown_days * bpd, max_hold_days * bpd
        self.min_order = min_order
        self.ind, self.st = None, {}

    def _prep(self, ctx):
        from . import indicators as I
        self.ind = {}
        for c in ctx.coins:
            b = ctx.bars[c]
            cl = [x.close for x in b]; hi = [x.high for x in b]; lo = [x.low for x in b]; vo = [x.volume for x in b]
            self.ind[c] = {"rsi14": I.rsi(cl, 14), "rsi4": I.rsi(cl, 4), "cci": I.cci(hi, lo, cl, 20), "bbl": I.bollinger_lower(cl, 20, 2),
                           "ema50": I.ema(cl, 50), "sma20": I.sma(cl, 20), "willr": I.willr(hi, lo, cl, 14), "stochk": I.stoch_k(hi, lo, cl, 14),
                           "vr": I.vol_ratio(vo, 20)}
            self.st[c] = {"phase": "flat", "cool_until": -1}

    def _entry_signal(self, c, i, ctx):
        d = self.ind[c]; b = ctx.bars[c]; bpd = self.bpd
        if i < max(60, 3 * bpd + 2):
            return None
        cl = b[i].close
        v = lambda k: d[k][i]
        if None in (v("rsi14"), v("rsi4"), v("cci"), v("bbl"), v("ema50"), v("sma20"), v("willr"), v("stochk"), v("vr")):
            return None
        hi24 = max(x.high for x in b[i - bpd + 1:i + 1]); lo24 = min(x.low for x in b[i - bpd + 1:i + 1])
        ret24 = cl / b[i - bpd].close - 1
        hi3d = max(x.high for x in b[i - 3 * bpd + 1:i + 1])
        m = self.modes
        if "m1" in m and v("rsi14") < 30 and cl < v("bbl") and v("vr") > 1.5: return "m1"
        if "m2" in m and v("rsi4") < 12 and cl < v("ema50") * 0.97 and v("cci") < -150: return "m2"
        if "m3" in m and ret24 <= -0.06 and hi24 > lo24 and (cl - lo24) / (hi24 - lo24) < 0.25: return "m3"
        if "m4" in m and v("willr") < -92 and v("stochk") < 10 and cl < v("sma20") * 0.95: return "m4"
        if "m5" in m and cl < hi3d * 0.88 and v("rsi14") < 35: return "m5"
        return None

    def on_bar(self, i, ctx):
        if self.ind is None:
            self._prep(ctx)
        bpd = self.bpd
        btc_ok = True
        if "BTC-USD" in ctx.coins and i >= bpd:
            btc_ok = ctx.close("BTC-USD", i) / ctx.close("BTC-USD", i - bpd) - 1 > -0.06
        mkt_ok = (self.market_ok(i) if self.market_ok else True) and btc_ok
        open_n = 0
        for c in ctx.coins:
            s = self.st.setdefault(c, {"phase": "flat", "cool_until": -1})
            held = ctx.pos[c] > 1e-12
            if s["phase"] == "await":
                if held:
                    s.update(phase="open", entry_i=i, peak=ctx.close(c, i), rebuys_done=0, trail=False)
                    avg = ctx.cost_basis[c] / ctx.pos[c]
                    for k in range(1, self.rebuys + 1):
                        px = avg * (1 - self.rebuy_dev * k)
                        ctx.limit(c, "buy", px, s["unit"] * 0.5 / px, tag=("rebuy", k))
                elif not ctx.orders_for(c, "buy"):
                    s["phase"] = "flat"
            if s["phase"] == "open":
                if not held:
                    ctx.cancel_all(c); s["phase"] = "flat"; continue
                open_n += 1
                cl = ctx.close(c, i); avg = ctx.cost_basis[c] / ctx.pos[c]; prof = cl / avg - 1
                s["peak"] = max(s["peak"], cl)
                rsi = self.ind[c]["rsi14"][i] or 50
                sell = None
                if prof >= 0.02 and rsi > 65: sell = "rsi65"
                elif prof >= 0.04 and rsi > 55: sell = "rsi55"
                elif prof >= 0.06: sell = "tp6"
                if prof >= 0.03: s["trail"] = True
                if sell is None and s["trail"] and cl <= s["peak"] * (1 - 0.008) and prof > 0.005: sell = "trail"
                if sell is None and prof <= -self.doom: sell = "doom"
                if sell is None and i - s["entry_i"] > self.max_hold and prof > -0.05: sell = "time"
                if sell:
                    ctx.cancel_all(c); ctx.market(c, "sell"); s["phase"] = "flat"
                    if sell == "doom": s["cool_until"] = i + self.cool
        if not mkt_ok or open_n + sum(1 for c in ctx.coins if self.st.get(c, {}).get("phase") == "await") >= self.slots:
            return
        cands = []
        for c in ctx.coins:
            s = self.st.setdefault(c, {"phase": "flat", "cool_until": -1})
            if s["phase"] != "flat" or ctx.pos[c] > 1e-12 or ctx.orders_for(c) or i < s["cool_until"]:
                continue
            if i >= 7 * bpd and ctx.close(c, i) / ctx.close(c, i - 7 * bpd) - 1 < -0.35:
                continue                                                       # crash protection: not a dip, a collapse
            m = self._entry_signal(c, i, ctx)
            if m:
                cands.append((ctx.close(c, i) / ctx.close(c, max(0, i - bpd)) - 1, c))
        room = self.slots - open_n - sum(1 for c in ctx.coins if self.st.get(c, {}).get("phase") == "await")
        for _, c in sorted(cands)[:max(0, room)]:
            eq = ctx.equity(i)
            unit = eq / self.slots / (1 + 0.5 * self.rebuys)            # base order; rebuys are half-size each
            unit = min(unit, ctx.free_cash() / (1 + 0.5 * self.rebuys) * 0.98)
            if unit <= 0 or unit < self.min_order:
                continue
            if self.maker_entry:
                px = ctx.close(c, i) * (1 - self.maker_entry)
                if ctx.limit(c, "buy", px, unit / px / (1 + ctx.maker), ttl=2, tag="entry") is None:
                    continue
            else:
                ctx.market(c, "buy", notional=unit)
            self.st.setdefault(c, {"cool_until": -1}).update(phase="await", unit=unit)

    def on_fill(self, i, ctx, order, price):
        # a rebuy fill lowers the average cost: nothing to re-arm (exits are evaluated on profit vs average cost)
        pass
