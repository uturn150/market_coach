"""Master Risk Manager — portfolio-level limits that NO individual bot can
bypass. SIMULATION-ONLY for now: it plugs into portfolio_sim.simulate() so
each limit's real cost and benefit can be measured on history before it ever
gates a live account (and per project rules, real-money activation would be a
separate, manual, explicit step — nothing here touches an account or order).

Two entry points, both called by whatever owns the shared pool:
  approve(request, state) -> fraction in [0,1]
      Every entry a strategy wants to make is presented here first. The
      manager returns how much of the requested size is allowed: 0 = veto,
      <1 = scaled down to the tightest limit's headroom, 1 = fine. A bot has
      no path around this — it never places the order itself.
  on_bar(state) -> {"liquidate": bool}
      Called once per bar after mark-to-market. Runs the pool-level state
      machine: daily-loss entry block, drawdown breaker (liquidate + halt,
      then re-arm from current equity after a cooling-off period, standing in
      for the human reset the live circuit breakers require), and the kill
      switch (liquidate and stay dead).

Limits (fractions of pool equity; None disables one):
  max_total_exposure      all invested value
  max_coin_exposure       value in any single coin, across every strategy
  max_strategy_exposure   value held by any single strategy
  max_correlated_exposure value held by a strategy's CLUSTER: every strategy
                          whose TRAILING return correlation with it is >=
                          correlation_threshold (no lookahead — computed only
                          from history up to now). Before enough history
                          exists it falls back to a-priori strategy-family
                          groups. Measured correlation among the champions is
                          ~0.8, so this cluster is nearly the whole fleet:
                          in practice it is a second total-exposure cap, and
                          that is the honest finding, not a bug.
  max_positions           simultaneous open positions
  daily_loss_limit        one-bar equity drop that blocks new entries briefly
  drawdown_limit          peak-to-trough drop that liquidates and halts

Every veto/scale is counted by which limit bound, so results say which rule
actually did the work.

Order bias, stated plainly: entries are approved first-come within a bar, in
the pool's fixed sleeve order, so when headroom runs out the earlier
strategies in the list win it. A live version should randomize or
round-robin; the simulator keeps it deterministic so runs are reproducible.
"""
import time
from collections import defaultdict
from . import risk

DEFAULT_LIMITS = dict(
    max_total_exposure=0.60, max_coin_exposure=0.10, max_strategy_exposure=0.20,
    max_correlated_exposure=0.40, correlation_threshold=0.75, corr_window=90,
    max_positions=100, daily_loss_limit=0.05, drawdown_limit=0.25,
    halt_bars_after_drawdown=30, loss_block_bars=2,
)


class MasterRiskManager:
    def __init__(self, limits=None, family_of=None, kill_bar=None):
        self.limits = {**DEFAULT_LIMITS, **(limits or {})}
        self.family_of = family_of or {}
        self.kill_bar = kill_bar          # simulation test hook for the kill switch
        self.killed = False
        self.peak = None
        self.halted_until = 0
        self.block_until = 0
        self.last_equity = None
        self.history = defaultdict(list)  # strategy -> trailing equity values
        self.clusters = {}
        self.events = []
        self.binding = defaultdict(int)   # which limit vetoed/scaled entries

    # ---- controls ---------------------------------------------------------
    def kill(self, reason="manual"):
        self.killed = True
        self.events.append({"type": "KILL_SWITCH", "reason": reason})

    # ---- helpers ----------------------------------------------------------
    def _clusters(self):
        thr, w = self.limits["correlation_threshold"], self.limits["corr_window"]
        names = list(self.history)
        if not names or min(len(self.history[n]) for n in names) < 31:
            fam = defaultdict(set)
            for a in names or self.family_of:
                fam[self.family_of.get(a, a)].add(a)
            return {a: fam[self.family_of.get(a, a)] for a in (names or self.family_of)}
        rets = {}
        for n in names:
            h = self.history[n][-(w + 1):]
            rets[n] = [h[i] / h[i - 1] - 1 for i in range(1, len(h)) if h[i - 1] > 0]
        out = {n: {n} for n in names}
        for x in range(len(names)):
            for y in range(x + 1, len(names)):
                c = risk.correlation(rets[names[x]], rets[names[y]])
                if c is not None and c >= thr:
                    out[names[x]].add(names[y])
                    out[names[y]].add(names[x])
        return out

    # ---- entry gate -------------------------------------------------------
    def approve(self, request, state):
        L = self.limits
        bar = request["bar"]
        if self.killed:
            self.binding["kill_switch"] += 1
            return 0.0
        if bar < self.halted_until:
            self.binding["drawdown_halt"] += 1
            return 0.0
        if bar < self.block_until:
            self.binding["daily_loss_block"] += 1
            return 0.0
        eq, want = state["equity"], request["notional"]
        if eq <= 0 or want <= 0:
            return 0.0
        positions = state["positions"]
        if L["max_positions"] is not None and len(positions) >= L["max_positions"]:
            self.binding["max_positions"] += 1
            return 0.0
        strat, coin = request["strategy"], request["coin"]
        heads = []
        if L["max_total_exposure"] is not None:
            heads.append(("total_exposure", L["max_total_exposure"] * eq - sum(v for _, _, v in positions)))
        if L["max_coin_exposure"] is not None:
            heads.append(("coin_exposure", L["max_coin_exposure"] * eq - sum(v for _, c, v in positions if c == coin)))
        if L["max_strategy_exposure"] is not None:
            heads.append(("strategy_exposure", L["max_strategy_exposure"] * eq - sum(v for a, _, v in positions if a == strat)))
        if L["max_correlated_exposure"] is not None:
            members = self.clusters.get(strat) or {strat}
            heads.append(("correlated_exposure", L["max_correlated_exposure"] * eq - sum(v for a, _, v in positions if a in members)))
        if not heads:
            return 1.0
        name, room = min(heads, key=lambda t: t[1])
        if room <= 0:
            self.binding[name] += 1
            return 0.0
        if room < want:
            self.binding["scaled:" + name] += 1
            return room / want
        return 1.0

    # ---- per-bar state machine -------------------------------------------
    def on_bar(self, state):
        L, i, eq = self.limits, state["i"], state["equity"]
        liquidate = False
        for a, v in state.get("strategy_equity", {}).items():
            self.history[a].append(v)
            if len(self.history[a]) > L["corr_window"] + 5:
                self.history[a] = self.history[a][-(L["corr_window"] + 5):]
        self.clusters = self._clusters()

        if self.kill_bar is not None and i == self.kill_bar and not self.killed:
            self.kill("simulated emergency")
            liquidate = True
        if self.killed:
            return {"liquidate": True}

        if self.halted_until and i >= self.halted_until:            # cooling-off over: re-arm
            self.peak, self.halted_until = eq, 0
            self.events.append({"type": "REARMED", "bar": i, "equity": round(eq, 2)})
        self.peak = eq if self.peak is None else max(self.peak, eq)

        if L["daily_loss_limit"] is not None and self.last_equity:
            if eq / self.last_equity - 1 <= -L["daily_loss_limit"] and i >= self.block_until:
                self.block_until = i + 1 + L["loss_block_bars"]
                self.events.append({"type": "DAILY_LOSS_BLOCK", "bar": i, "drop_pct": round((eq / self.last_equity - 1) * 100, 1)})
        if L["drawdown_limit"] is not None and not self.halted_until:
            dd = eq / self.peak - 1
            if dd <= -L["drawdown_limit"]:
                self.halted_until = i + L["halt_bars_after_drawdown"]
                self.events.append({"type": "DRAWDOWN_BREAKER", "bar": i, "drawdown_pct": round(dd * 100, 1)})
                liquidate = True
        self.last_equity = eq
        return {"liquidate": liquidate}

    def summary(self):
        counts = defaultdict(int)
        for e in self.events:
            counts[e["type"]] += 1
        return {"events": dict(counts), "entries_limited_by": dict(self.binding),
                "event_log": self.events[:40]}
