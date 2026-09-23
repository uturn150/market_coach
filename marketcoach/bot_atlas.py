"""Bot Atlas: one plain-English map of EVERYTHING that runs here — what each bot does, whether it trades live (paper,
real prices, forward in time) or only learns from history (backtest / simulator), and what has been found so far.
Static text + live facts read from state files. Live PERFORMANCE numbers are joined in the page from the account snapshot,
so this module never touches the broker. Read-only; PAPER only."""
import glob, json, os, re, time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(BASE, "paper_state")
LOGS = os.path.join(BASE, "logs")

TF = {3600: "hourly", 14400: "4-hour", 86400: "daily"}
OPS = {"cross_above": "crosses above", "cross_below": "crosses below", "above": "is above", "below": "is below", ">": ">", "<": "<"}


def _load(path, default=None):
    try:
        return json.load(open(path))
    except Exception:
        return default


def _pct(x):
    return f"{x * 100:g}%"


def describe_spec(spec):
    """'BUY when rsi:14 crosses below 20; SELL when ...; stop 15%, trailing 20%'"""
    parts = []
    ent = spec.get("entry")
    if ent and ent.get("signals"):
        sg = " + ".join(f"{x['when']['left']} {OPS.get(x['when']['op'], x['when']['op'])} {x['when']['right']}" for x in ent["signals"])
        parts.append(f"BUY when {ent.get('threshold')} of these {len(ent['signals'])} agree: {sg}")
    for r in spec.get("rules", []):
        w, t = r.get("when", {}), r.get("then", {})
        cond = f"{w.get('left')} {OPS.get(w.get('op'), w.get('op'))} {w.get('right')}"
        parts.append(f"{str(t.get('action', '?')).upper()} when {cond}")
    risk = spec.get("risk", {})
    ex = []
    if risk.get("stop_loss_pct"): ex.append(f"stop-loss {_pct(risk['stop_loss_pct'])}")
    if risk.get("take_profit_pct"): ex.append(f"take-profit {_pct(risk['take_profit_pct'])}")
    if risk.get("trailing_stop_pct"): ex.append(f"trailing stop {_pct(risk['trailing_stop_pct'])}")
    return "; ".join(parts) + (" · " + ", ".join(ex) if ex else "")


def describe_bot(kind, p, ncoins):
    if kind == "grid":
        return (f"GRID: on each of {ncoins} coins it keeps {p['levels']} buy orders resting below the price, {_pct(p['step'])} apart. "
                f"When one fills it puts a sell one step ({_pct(p['step'])}) higher. Earns from price wobbling up and down.")
    if kind == "dca":
        s = f"DCA: buys a first slice of each of {ncoins} coins, then up to {p['safety']} extra 'safety' buys each time price drops {_pct(p['dev'])}, each {p['mult']}x bigger. "
        s += f"Sells the whole position at +{_pct(p['tp'])} on average cost"
        s += f"; gives up at -{_pct(p['stop'])}." if p.get("stop") else "."
        if p.get("base_limit"): s += " First buy is a resting limit order slightly under price (a real 'many small trades a day' design)."
        return s
    if kind == "dca_adaptive":
        return (f"ADAPTIVE DCA: same dip-buying idea on {ncoins} coins, but its dip size / take-profit / number of safety buys are picked from a table "
                f"that depends on the market regime (up / down / sideways / wild), and the table is re-learned weekly from history.")
    return kind


def _breaker_text(p):
    b = f"Safety: pauses all buying {p.get('breaker_pause', 30)} days if the account falls {_pct(p['breaker'])} from its peak." if p.get("breaker") else ""
    f = {"F1": "Only buys while the market is healthy (BTC above its 200-day average AND its 50-day average rising).",
         "F0": "Only buys while BTC is above its 200-day average.", "none": "No market filter."}.get(p.get("filter", "none"), "")
    return (f + " " + b).strip()


def _accounts():
    """name -> state dict for every kraken-connected paper account."""
    out = {}
    for f in sorted(glob.glob(os.path.join(STATE, "*.kraken.json"))) + sorted(glob.glob(os.path.join(STATE, "*.kraken_moon.json"))):
        s = _load(f)
        if isinstance(s, dict) and s.get("account"):
            s["_file"] = os.path.basename(f)
            out[s["account"]] = s
    return out


def _signal_member(name, s):
    spec = _load(os.path.join(BASE, s.get("strategy_path", "")), {}) or {}
    flags = []
    if s.get("market_filter") == "F1": flags.append("market filter F1")
    if s.get("avoid_regimes"): flags.append("skips " + "/".join(s["avoid_regimes"]).lower() + " markets")
    if s.get("regime_filter"): flags.append("BTC-trend gate")
    return {"name": name, "what": describe_spec(spec) or "breakout funnel",
            "tf": TF.get(s.get("granularity"), str(s.get("granularity"))), "coins": len(s.get("sleeves", {})) or None,
            "note": ", ".join(flags), "start": s.get("start_cash"), "spec_name": spec.get("name", "")}


def live_groups():
    acc = _accounts()
    G = []

    def take(pred):
        names = [n for n in sorted(acc) if pred(n) and not acc[n].get("_used")]
        for n in names: acc[n]["_used"] = True
        return names

    def sig(names):
        return [_signal_member(n, acc[n]) for n in names if "bot_kind" not in acc[n]]

    champs = take(lambda n: n == "realpaper" or (n.startswith("kraken") and not n.startswith("krakendemo") and n != "krakenmoon"))
    G.append({"id": "champions", "title": "Champions — the original 12 signal bots", "badge": "LIVE PAPER",
              "what": "Classic indicator bots (moving averages, RSI, MACD, Bollinger, Ichimoku, Donchian…). Daily candles, all-in on one coin at a time across ~24 coins. "
                      "Each passed the fee gate + edge gate on history before going live.",
              "verdict": "None has graduated. Most were later found weak at your real fees (0.80% taker / 0.40% maker): macd_cross and rsi were flagged/retired, "
                         "the rest need more live weeks. They are the baseline everything else must beat.",
              "members": sig(champs)})
    moon = take(lambda n: n == "krakenmoon")
    if moon:
        G.append({"id": "moon", "title": "Moon funnel — breakout scanner", "badge": "LIVE PAPER",
                  "what": "Scans all coins hourly for a breakout that clears four triggers, buys it with a ladder of orders and a stop, and only holds a few positions.",
                  "verdict": "Runs on real prices; rarely triggers.", "members": [{"name": n, "what": "breakout funnel", "tf": "hourly scan", "coins": None, "note": "", "start": acc[n].get("start_cash")} for n in moon]})
    ch = take(lambda n: re.match(r"chal_(kraken|realpaper)", n))
    G.append({"id": "learner", "title": "Learner challengers — variants the bot built itself", "badge": "LIVE PAPER",
              "what": "Copies of a champion with ONE change the regime learner found helpful in history: either skip SIDEWAYS markets, or only trade under filter F1. "
                      "They run next to their parent so the live result is a fair A/B test.",
              "verdict": "Learner says 'avoid SIDEWAYS' and 'F1' are supported on history; live A/B is still ~1 day old — no conclusion yet.",
              "members": sig(ch)})
    gr = take(lambda n: n.startswith("chal_gr_"))
    G.append({"id": "green", "title": "Green-search bots — built to be green often", "badge": "LIVE PAPER",
              "what": "Dip-buying strategies (Bollinger dip, CCI, stochastic, Williams %R) that a search picked because they were profitable in most weeks in held-out years and on unseen coins.",
              "verdict": "Backtest-selected; live green-week evidence needs 30+ days.", "members": sig(gr)})
    fx = take(lambda n: "_fxe_" in n)
    G.append({"id": "factory", "title": "Factory explorers", "badge": "LIVE PAPER",
              "what": "An automatic strategy factory tests 10 indicator families on daily/4h bars and large/mid coin groups; the few that survive get a live paper account.",
              "verdict": "Explorers, not champions. Removed automatically if they lose over enough trades.", "members": sig(fx)})
    c4 = take(lambda n: n.startswith("c4h_"))
    G.append({"id": "fleet4h", "title": "4-hour fleet", "badge": "LIVE PAPER",
              "what": "The champion ideas re-tuned for 4-hour candles (more trades than daily), some with the F1 market filter.",
              "verdict": "Only 1 of 16 MACD and 1 of 9 RSI candidates survived testing — flagged as possible overfits.", "members": sig(c4)})
    ar = take(lambda n: n.startswith("arena_"))
    G.append({"id": "arena", "title": "Arena — hourly, maker orders, experimental", "badge": "LIVE PAPER",
              "what": "The same 14 strategies at hourly speed with limit orders, to watch frequent trading happen live.",
              "verdict": "History says only 1 of 28 combinations survives fees, so these are expected to bleed. They provide most of the closed live trades (mean about −1.7% per trade).",
              "members": sig(ar)})
    de = take(lambda n: n.startswith("krakendemo"))
    G.append({"id": "demo", "title": "Demo bots — deliberately fee-failing", "badge": "LIVE PAPER",
              "what": "Strategies that FAILED the fee gate, kept running only so you can watch fee bleed on a live account.",
              "verdict": "Expected to lose. Never ranked.", "members": sig(de)})
    bots = take(lambda n: n.startswith("bot_"))
    mem = []
    for n in bots:
        s = acc[n]; p = s["bot_params"]
        mem.append({"name": n, "what": describe_bot(s["bot_kind"], p, len(s["coins"])), "tf": "daily candles, resting limit orders", "coins": len(s["coins"]),
                    "note": _breaker_text(p), "start": s.get("start_cash"), "kind": s["bot_kind"]})
    G.append({"id": "community", "title": "Community-style bots — grid, DCA, adaptive DCA", "badge": "LIVE PAPER",
              "what": "The kinds of bots crypto Discord/Reddit groups run (3Commas/Pionex style): real resting limit orders, safety buys, take-profit, plus a drawdown breaker. "
                      "Each live account re-simulates itself from a start day on real closed candles, so its results are reproducible. RULES ON: Kraken real per-coin minimum size, lot and price tick; $50 start; $10 order steps.",
              "verdict": "Now simulated the way Kraken really works: $50 start, every buy in $10 steps and never below the coin's real minimum. On history that is BAD for DCA: "
                         "$10 orders on a $50 account means only ~5 positions and no room to average down, so the DCA variants lose 29-94% over 3-4 years (see the Real-size results table). "
                         "The grid bots barely trade (3 trips). The old 'ideal' results (+3% to +7%) assumed divisible orders. Live: started 2026-09-20, no fills yet.",
              "members": mem})
    sl = take(lambda n: n == "slow50_v1" or n.startswith("slow50_challenger_"))
    G.append({"id": "slow50", "title": "slow50 — the $50 slow trader (+ auto-deployed challengers)", "badge": "LIVE PAPER",
              "what": "Your 'slow trade' bot: a handful of coins, trend/breakout rules, only under filter F1, fixed-size $10-step buys. "
                      "slow50_v1 (BCH/BTC/ETH) is retrained weekly and can update ITSELF (paper only) if a new version clearly beats it. "
                      "Challengers are different coin sets the trainer found and an AUTOMATED weekly deployer opened on its own (paper only): slow50_challenger_a (BCH/BTC/LINK, +14.7%/yr held-out, +10.5%/yr on real held-out coins), "
                      "slow50_challenger_b (BTC/ETH/XLM, +31.9%/yr held-out, +59.6%/yr on real held-out coins but a 42% worst drawdown) — each runs as its own paper account so LIVE results decide, not the backtest that got it deployed. "
                      "'Real held-out coins' = marketcoach.holdout.holdout_products(), the ~20 coins never used to build any strategy in this project (fixed 2026-09-22: the old 'unseen' check reused coins from the same basket).",
              "verdict": "Feasible at $50. F1 trend entries are rare, so fills are slow to arrive; check each account's own trade count above. See the fill-quality report below.",
              "members": sig(sl)})
    for g in G:
        if g["id"] in ("champions", "learner", "green", "factory", "fleet4h", "arena", "demo"):
            g["what"] += " SIZING (paper, real Kraken rules): $50 start, one shared cash pool, every buy is $10 (or the next $10 step that meets Kraken's minimum for that coin), so at most about 4 open positions."
        if g["id"] in ("moon", "slow50"):
            g["what"] += " Every buy is at least $10."
    G.append({"id": "virtual", "title": "Meta-allocator & A/B policies — virtual portfolios", "badge": "LIVE PAPER",
              "what": "Not bots: pretend $1,000 portfolios that split money across all the community bots by different rules (equal, inverse-volatility, regime-router, drawdown overlay) to test whether smart allocation beats equal weight.",
              "verdict": "All identical so far (too little history). History says allocation only lowers risk; it does not create returns.", "members": []})
    return G


def _refports():
    rows = []
    for f in sorted(glob.glob(os.path.join(LOGS, "refports_benchmark*.json"))) + [os.path.join(LOGS, "blueprint_benchmark.json"), os.path.join(LOGS, "blueprint_benchmark2.json")]:
        d = _load(f)
        if not isinstance(d, dict): continue
        for k, v in d.items():
            if isinstance(v, dict) and isinstance(v.get("ours"), list):
                rows.append((k, v["ours"][0], v.get("typical", [None])[0]))
    return rows


def history_labs():
    H = []
    md = _load(os.path.join(LOGS, "minute_download.json"), {}) or {}
    H.append({"id": "minutes", "title": "Minute-by-minute data + time machine", "badge": "HISTORY",
              "what": "Downloads every minute candle for the 27 coins with 5+ years of history so bots can be replayed through real days, hour by hour, like a simulated market.",
              "status": f"Download {md.get('pct', '?')}% done ({md.get('months_done', '?')}/{md.get('months_total', '?')} coin-months). Simulator and replay engine already built.",
              "feeds": "Will unlock the Hummingbot grid controllers and a minute-level sim learner."})
    sim = _load(os.path.join(STATE, "sim_learner.json"), {}) or {}
    hof = sim.get("hall_of_fame") or []
    H.append({"id": "simlearner", "title": "Sim learner — evolves DCA/grid bots on history", "badge": "HISTORY",
              "what": "An evolutionary search: it invents bot settings, replays them through the historical market, keeps the best and mutates them. No live trading.",
              "status": f"{sim.get('generations', 0)} generations, hall of fame {len(hof)}" + (f", best train score {hof[0].get('train_score')}" if hof else ""),
              "feeds": "Candidates feed the community bots once they hold up on unseen coins and years."})
    sl = _load(os.path.join(STATE, "slow_learner_v2.json"), {}) or {}
    ver = _load(os.path.join(STATE, "slow50_versions.json"), {}) or {}
    H.append({"id": "slowlearner", "title": "Slow-trader trainer + self-updater", "badge": "HISTORY → LIVE",
              "what": "Weekly it trains new versions of the $50 slow bot on history. A guarded updater swaps the live paper bot's settings only if the new one wins on future AND unseen coins, with a 30-day cooldown and rollback.",
              "status": f"{sl.get('generations', 0)} generations trained; live version v{(ver.get('current') or {}).get('version', '?')}",
              "feeds": "slow50_v1"})
    lm = _load(os.path.join(STATE, "learner_memory.json"), {}) or {}
    hy = ", ".join(f"{k}: {v.get('status')}" for k, v in (lm.get("hypotheses") or {}).items())
    H.append({"id": "regime", "title": "Regime learner — tests ideas hourly", "badge": "HISTORY + LIVE",
              "what": "Every hour it re-tests hypotheses like 'sitting out sideways markets helps' against random-day placebo controls, on history and on live results, and deploys challenger bots when an idea is supported.",
              "status": hy or "no data", "feeds": "Learner challengers"})
    rows = _refports()
    if rows:
        prof = sum(1 for _, o, _t in rows if o is not None and o > 0)
        prof10 = sum(1 for _, _o, t in rows if t is not None and t > 0)
        worst_best = sorted(rows, key=lambda r: -r[1])[:3]
        st = f"{len(rows)} results saved: {prof} profitable at your fees, {prof10} at 0.10% fees. Least bad: " + ", ".join(f"{k} {o:+.0f}%" for k, o, _ in worst_best)
    else:
        st = "no results"
    H.append({"id": "ports", "title": "Public bot code, re-run on your fees (~50 Freqtrade / Hummingbot ports)", "badge": "HISTORY",
              "what": "Real strategies from public bot projects were ported faithfully and backtested at Kraken's real fees to see whether anyone else's bot is better than ours.",
              "status": st + ". Detail: logs/refports_summary.md",
              "feeds": "Nothing live. Lesson: fees decide — public bots are built for ~0.10% fees; more trades per day = worse after fees."})
    ml = _load(os.path.join(LOGS, "minute_lab.json"), None)
    if ml:
        better = sum(1 for v in ml.values() if v["minute"][0] > v["daily"][0])
        st_ml = f"{len(ml)} bot/coin-set runs: minute-level fills beat the daily engine in {better}. Detail: logs/minute_lab.json"
    else:
        st_ml = "still running; partial results in logs/minute_lab.log"
    H.append({"id": "minutelab", "title": "Minute-level replay of the community bots (5 years)", "badge": "HISTORY",
              "what": "Same bots and real fees, but limit orders fill minute by minute instead of once per daily candle; includes tighter 'penny' variants. Tested on 9 tuned coins and 18 unseen coins.",
              "status": st_ml, "feeds": "Decides whether faster designs deserve a live paper account."})
    H.append({"id": "realsize", "title": "Real-size simulation (Kraken per-coin rules)", "badge": "HISTORY + LIVE",
              "what": "Every bot is re-simulated with Kraken's real minimum order size, lot size and tick per coin, $50 start and $10 order steps, so trades are the size Kraken would accept.",
              "status": "DCA designs lose 29-94% on history at $50 with $10 minimum orders; grids barely trade (3 trips). See the Real-size table.", "feeds": "All live paper accounts now use these rules."})
    H.append({"id": "labs", "title": "Research labs (grid/DCA backtests, stress, balance, frequency)", "badge": "HISTORY",
              "what": "Backtest labs that produced the community-bot settings: coin-set transfer tests, crash stress tests, small-balance and minimum-order tests, fee sensitivity, trades-per-day frontier.",
              "status": "Done; results in logs/*.json", "feeds": "Chose the live grid/DCA settings and the drawdown breaker."})
    H.append({"id": "rejected", "title": "Tried and rejected (kept on record)", "badge": "HISTORY",
              "what": "Experiments that did not survive testing: ML meta-labeling, regime-switching router (Chameleon), spot market making, time-decaying take-profit (1 of 6 held-out wins), per-coin cooldown after stop-outs, all 46+ public strategy ports at real fees.",
              "status": "Kept, never deleted — so we do not repeat them.", "feeds": "Nothing."})
    return H


def real_size():
    """Backtest of every live community bot at $50 with Kraken's real rules and $10 steps vs the old divisible-order 'ideal'."""
    d = _load(os.path.join(LOGS, "real_size_lab_50.json"), {}) or {}
    rows = []
    for name, r in sorted(d.items()):
        x = r[0] if r else None
        if x:
            rows.append({"name": name, "ideal_ret": round(x["ideal_ret"], 1), "real_ret": round(x["real_ret"], 1), "trips_real": x["trips_real"], "trips_ideal": x["trips_ideal"]})
    return {"rows": rows}


def rules_summary():
    try:
        from . import kraken_rules
        p = kraken_rules.load()
    except Exception:
        p = {}
    ex = [{"coin": c.replace("-USD", ""), "min_coins": p[c]["ordermin"], "min_usd_cost": p[c]["costmin"]} for c in ("BTC-USD", "ETH-USD", "SOL-USD", "DOGE-USD", "ADA-USD", "LTC-USD") if c in p]
    return {"start": 50, "order_step": 10, "text": "Every paper account starts with $50. Every buy is $10 or more, in $10 steps, and never below Kraken's real minimum for that coin. "
            "Sizes are rounded down to the coin's real lot size and prices to its tick. All paper: no real orders.", "examples": ex}


def flow():
    return [
        {"n": "1", "t": "History", "d": "Years of real prices (daily now, minute data downloading)."},
        {"n": "2", "t": "Learn & test", "d": "Labs and learners replay bots through history; hold-out years and unseen coins must agree."},
        {"n": "3", "t": "Live paper", "d": "Survivors trade fake money on real Kraken prices, forward in time, with real fees."},
        {"n": "4", "t": "Graduation gates", "d": "Fee gate, edge gate, live evidence (weeks). Nothing has passed."},
        {"n": "5", "t": "Human review", "d": "The FINAL state is 'eligible for human review'. Nothing goes live with real money automatically."},
    ]


def build():
    fq = None
    try:
        from . import slow50_fill_report
        fq = slow50_fill_report.latest()
    except Exception:
        pass
    return {"generated": int(time.time()), "flow": flow(), "live": live_groups(), "history": history_labs(), "fill_quality": fq, "rules": rules_summary(), "real_size": real_size(),
            "note": "All accounts are PAPER (fake money, real prices). No real orders are ever placed."}


if __name__ == "__main__":
    print(json.dumps(build(), indent=1)[:3000])
