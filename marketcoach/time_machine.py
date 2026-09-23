"""Time machine: replay real 24-hour days minute by minute, with the bot running inside the simulated market.

  * The market is real 1-minute history (data/minute, see minute_data.py) for every coin in the chosen universe.
  * The bot keeps deciding on DAILY bars built from those minutes (same bot classes as everywhere: grid, DCA, adaptive...),
    but ORDERS ARE FILLED MINUTE BY MINUTE: a resting limit order fills at the first minute price trades through it
    (order of events inside the day is now real: stop vs target, several grid cycles in one day, gaps at 00:00).
  * Market orders decided at a day's close fill at the next day's first minute (taker fee + half-spread + slippage).
  * A counter-order placed by a fill can fill from the NEXT minute on, never inside the same minute.
  * Real fee tier (kraken_fees), reserved cash/coins, no lookahead: the bot only ever sees closed daily bars.
Speed: a coin's minutes are only scanned on days it has a resting order, so a year of 27 coins replays in seconds-minutes.
"""
import calendar, time
from . import minute_data as md
from .bot_engine import Ctx, Order
from .data import Bar

DAY = 86400


class MinuteDays:
    """Lazy per-coin, per-day minute rows from the month files."""
    def __init__(self, coins):
        self.coins = coins
        self._month = {}

    def _rows(self, coin, y, m):
        k = (coin, y, m)
        if k not in self._month:
            self._month[k] = md.load_month(coin, y, m)
        return self._month[k]

    def day(self, coin, day_ts):
        """Minute rows [(ts,o,h,l,c,v)] for the UTC day starting at day_ts, ascending (gaps stay gaps)."""
        g = time.gmtime(day_ts)
        rows = self._rows(coin, g.tm_year, g.tm_mon)
        # rows are ascending: binary search the day slice
        lo, hi = 0, len(rows)
        while lo < hi:
            mid = (lo + hi) // 2
            if rows[mid][0] < day_ts: lo = mid + 1
            else: hi = mid
        a = lo
        hi = len(rows)
        while lo < hi:
            mid = (lo + hi) // 2
            if rows[mid][0] < day_ts + DAY: lo = mid + 1
            else: hi = mid
        return rows[a:lo]

    def drop_before(self, y, m):
        for k in [k for k in self._month if (k[1], k[2]) < (y, m)]:
            del self._month[k]


def daily_bar(rows, day_ts):
    if not rows:
        return None
    return Bar(day_ts, rows[0][1], max(r[2] for r in rows), min(r[3] for r in rows), rows[-1][4], sum(r[5] for r in rows))


class TMCtx(Ctx):
    """Ctx whose orders are stamped with an absolute minute clock so counter-orders fill from the next minute on."""
    now = 0

    def limit(self, coin, side, price, units, ttl=None, tag=None):
        o = super().limit(coin, side, price, units, ttl=ttl, tag=tag)
        if o is not None:
            o.born = self.now                      # active for minutes strictly after `now`
            o.ttl = None if ttl is None else ttl * 1440
        return o


def run(bot, coins, first_day_ts, last_day_ts, cash=100.0, maker=None, taker=None, half_spread=0.00024, slip=0.00005,
        through=0.0001, store=None):
    """Replay days [first_day_ts, last_day_ts] (UTC midnights). Returns (equity per day, day timestamps, ctx)."""
    if maker is None or taker is None:
        from . import kraken_fees
        f = kraken_fees.get_fee_bps()
        maker = f["maker_bps"] / 1e4 if maker is None else maker
        taker = f["taker_bps"] / 1e4 if taker is None else taker
    store = store or MinuteDays(coins)
    days = list(range(first_day_ts, last_day_ts + DAY, DAY))
    # daily bars for the bot are appended as days close; Ctx wants bars[coin][i] -> we keep growing lists
    hist = {c: [] for c in coins}
    ctx = TMCtx(hist, cash, maker, taker, half_spread, slip, through)
    ctx.n = len(days)
    eq, kept_days = [], []
    last_close = {c: None for c in coins}
    for di, d in enumerate(days):
        ctx.i = di - 1 if di > 0 else 0
        rows_today = {c: store.day(c, d) for c in coins}
        # 1) market orders queued at yesterday's close fill at today's first traded minute
        pend, ctx.pending_market = ctx.pending_market, []
        for coin, side, units, notional in pend:
            r = rows_today[coin]
            if not r:
                continue
            o = r[0][1]
            if side == "buy":
                px = o * (1 + half_spread + slip)
                u = units if units is not None else (notional or 0) / (px * (1 + taker))
                u = min(u, ctx.free_cash() / (px * (1 + taker)))
                if u > 1e-12:
                    ctx.now = di * 1440
                    ctx._buy(coin, u, px, taker, max(0, di - 1)); ctx.stats["market_fills"] += 1
            else:
                px = o * (1 - half_spread - slip)
                u = ctx.free_units(coin) if units is None else min(units, ctx.free_units(coin))
                if u > 1e-12:
                    ctx._sell(coin, u, px, taker, max(0, di - 1)); ctx.stats["market_fills"] += 1
        # 2) minute-by-minute limit-order matching (only coins that actually have resting orders)
        active = [c for c in coins if ctx.orders_for(c)]
        for c in active:
            for k, (t, o, h, l, cl, v) in enumerate(rows_today[c]):
                if not ctx.orders_for(c):
                    break
                ctx.now = di * 1440 + int((t - d) // 60)
                for oid, od in list(ctx.orders.items()):
                    if oid not in ctx.orders:            # cancelled by an earlier fill's on_fill in this same minute
                        continue
                    if od.coin != c or od.born >= ctx.now:
                        continue
                    if od.ttl is not None and ctx.now - od.born >= od.ttl:
                        del ctx.orders[oid]; ctx.stats["expired"] += 1; continue
                    if od.side == "buy" and l <= od.price * (1 - through):
                        if od.units * od.price * (1 + maker) <= ctx.cash + 1e-9:
                            ctx._buy(c, od.units, od.price, maker, max(0, di - 1)); del ctx.orders[oid]; ctx.stats["limit_fills"] += 1
                            if hasattr(bot, "on_fill"): bot.on_fill(di, ctx, od, od.price)
                    elif od.side == "sell" and h >= od.price * (1 + through):
                        if od.units <= ctx.pos[c] + 1e-12:
                            ctx._sell(c, od.units, od.price, maker, max(0, di - 1)); del ctx.orders[oid]; ctx.stats["limit_fills"] += 1
                            if hasattr(bot, "on_fill"): bot.on_fill(di, ctx, od, od.price)
        # 3) close of day: build the daily bar, mark to market, let the bot decide (orders rest from the next minute)
        for c in coins:
            b = daily_bar(rows_today[c], d)
            if b is None:
                lc = last_close[c]
                if lc is None:
                    b = Bar(d, 0.0, 0.0, 0.0, 0.0, 0.0)
                else:
                    b = Bar(d, lc, lc, lc, lc, 0.0)
            hist[c].append(b)
            if b.close > 0:
                last_close[c] = b.close
        ctx.i = di
        ctx.now = di * 1440 + 1439
        eq.append(ctx.equity(di)); kept_days.append(d)
        if di < len(days) - 1:
            bot.on_bar(di, ctx)
        if di % 31 == 0:
            g = time.gmtime(d); store.drop_before(g.tm_year, g.tm_mon)
    return eq, kept_days, ctx
