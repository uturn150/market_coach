"""Strategy factory: grows the paper fleet with STRUCTURALLY DIFFERENT strategies, in two tiers.

Families (fixed here BEFORE any results; each is a different market idea from the existing fleet):
  ema_trend, roc_mom, rsi_trend, stoch_pullback, vol_breakout, bb_dip, cci_pullback,
  macd_zero, kelt_dip, willr_pullback           x  {daily, 4h}  x  {large12, mid12 coin universes}
One account per (family, granularity, universe): the candidate with the best IN-SAMPLE profit
factor. All run behind the F1 market filter (pre-registered: it won the filter study), at the real
fee tier.

TIER 1  chal_fx_* / c4h_fx_*   pass fee+sample gates, out-of-sample excess > 0, a robust parameter
        family (>=3 survivors and >=25% of candidates), Monte Carlo, and (daily only) the unseen-coin
        hold-out test, AND are not redundant with any deployed account (backtest daily-return
        correlation < REDUNDANCY_CORR).
TIER 2  chal_fxe_* / c4h_fxe_*  'explorers': pass fee+sample gates only. Live paper results decide;
        marketcoach/regime_learner auto-pauses one that clearly bleeds (>=30 trips, t <= -3).
Nothing here can promote, place a real order, or touch a champion. Every rejected combo is written to
logs/research_rejections.jsonl. With this many bots some will look good by luck: graduation still
requires the full evidence chain, live sample included.
"""
import json, os, time
from . import data, kraken_fees, kraken_portfolio as kp, market_filters as mf, regime
from . import learner as L
from .optimize import candidates
from .monte_carlo import bootstrap, summarize_distribution
from .portfolio_optimize import DEFAULT_BASKET, _pooled

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REJECT_LOG = os.path.join(BASE, "logs", "research_rejections.jsonl")
OUT_DIR = os.path.join(BASE, "strategies", "factory")
REDUNDANCY_CORR = 0.85       # tier 1: must be less correlated than this with every deployed account
EXPLORER_REDUNDANCY = 0.90   # explorers: above this it is a near-duplicate bet and adds no evidence, so no bot
ROBUST_MIN_SURVIVORS, ROBUST_MIN_SHARE = 3, 0.25
MC_P5, MC_LOSS = 0.0, 0.10
MAX_TIER1, MAX_TIER2 = 40, 60
UNIVERSES = {"large": DEFAULT_BASKET[:12], "mid": DEFAULT_BASKET[12:24]}
FEES = {"order_type": "taker", "taker_fee_bps": 80, "half_spread_bps": 5, "slippage_bps": 3, "maker_fee_bps": 40}
RISK = {"trailing_stop_pct": "{trail}", "stop_loss_pct": 0.12}


def _tpl(name, params, entry, exits, constraints=None):
    """entry: list of (signal name, left, op, right); all must hold (threshold = len). exits: rules."""
    t = {"name": f"{name} (factory)", "costs": FEES, "risk": RISK, "params": {**params, "trail": [0.15, 0.20]},
         "entry": {"threshold": len(entry), "size": "100%",
                   "signals": [{"name": n, "when": {"left": l, "op": o, "right": r}, "weight": 1} for n, l, o, r in entry]},
         "rules": [{"when": {"left": l, "op": o, "right": r}, "then": {"action": "sell", "size": "all"}} for l, o, r in exits]}
    if constraints:
        t["constraints"] = constraints
    return t


# period-valued params are scaled x6 on 4h bars (1 day = 6 bars); thresholds are not.
PERIOD_PARAMS = {"f", "s", "n", "trend", "d", "x", "k_n", "sig"}

FAMILIES = {
    "ema_trend": _tpl("EMA trend", {"f": [8, 12, 21], "s": [34, 55, 89]},
                      [("up", "ema:{f}", ">", "ema:{s}"), ("above", "price", ">", "ema:{f}")],
                      [("price", "cross_below", "ema:{f}")], ["f < s"]),
    "roc_mom": _tpl("ROC momentum", {"n": [10, 20, 40], "t": [0.05, 0.10, 0.15], "trend": [50, 100]},
                    [("mom", "roc:{n}", ">", "{t}"), ("trend", "price", ">", "sma:{trend}")],
                    [("roc:{n}", "cross_below", 0)]),
    "rsi_trend": _tpl("RSI trend-hold", {"n": [14, 21], "t": [50, 55, 60], "trend": [50, 100]},
                      [("rsi_up", "rsi:{n}", ">", "{t}"), ("trend", "price", ">", "sma:{trend}")],
                      [("rsi:{n}", "cross_below", 45)]),
    "stoch_pullback": _tpl("Stochastic pullback in uptrend", {"n": [14], "lo": [20, 30], "trend": [50, 100, 150]},
                           [("dip", "stoch_k:{n}", "<", "{lo}"), ("trend", "price", ">", "sma:{trend}")],
                           [("stoch_k:{n}", "cross_above", 80), ("price", "cross_below", "sma:{trend}")]),
    "vol_breakout": _tpl("Volume breakout", {"d": [20, 40], "v": [1.5, 2.0], "x": [20, 50]},
                         [("brk", "price", ">", "donchian_high:{d}"), ("vol", "vol_ratio:20", ">", "{v}")],
                         [("price", "cross_below", "sma:{x}")]),
    "bb_dip": _tpl("Bollinger dip in uptrend", {"n": [20], "k": [2, 2.5], "trend": [50, 100, 150]},
                   [("dip", "price", "<", "bollinger_lower:{n}:{k}"), ("trend", "price", ">", "sma:{trend}")],
                   [("price", "cross_above", "bollinger_mid:{n}")]),
    "cci_pullback": _tpl("CCI pullback in uptrend", {"n": [20], "lo": [-100, -150], "trend": [50, 100, 150]},
                         [("dip", "cci:{n}", "<", "{lo}"), ("trend", "price", ">", "sma:{trend}")],
                         [("cci:{n}", "cross_above", 0), ("price", "cross_below", "sma:{trend}")]),
    "macd_zero": _tpl("MACD above zero", {"f": [12, 20], "s": [26, 50], "trend": [100, 150]},
                      [("pos", "macd_line:{f}:{s}", ">", 0), ("trend", "price", ">", "sma:{trend}")],
                      [("macd_line:{f}:{s}", "cross_below", 0)], ["f < s"]),
    "kelt_dip": _tpl("Keltner dip in uptrend", {"n": [20], "trend": [50, 100, 150]},
                     [("dip", "price", "<", "keltner_lower:{n}"), ("trend", "price", ">", "sma:{trend}")],
                     [("price", "cross_above", "sma:{n}"), ("price", "cross_below", "sma:{trend}")]),
    "willr_pullback": _tpl("Williams %R pullback in uptrend", {"n": [14], "lo": [-80, -90], "trend": [50, 100, 150]},
                           [("dip", "willr:{n}", "<", "{lo}"), ("trend", "price", ">", "sma:{trend}")],
                           [("willr:{n}", "cross_above", -20), ("price", "cross_below", "sma:{trend}")]),
}


def scaled(tpl, gran):
    """4h version: every period-valued param x6 (a day is 6 bars), same thresholds."""
    if gran == 86400:
        return tpl
    t = json.loads(json.dumps(tpl))
    for k, vals in t["params"].items():
        if k in PERIOD_PARAMS:
            t["params"][k] = [v * 6 for v in vals]
    # fixed literal period inside vol_ratio:20 scales too
    s = json.dumps(t).replace("vol_ratio:20", "vol_ratio:120")
    return json.loads(s)


def _log(**rec):
    os.makedirs(os.path.dirname(REJECT_LOG), exist_ok=True)
    with open(REJECT_LOG, "a") as f:
        f.write(json.dumps({"t": int(time.time()), "iso": time.strftime("%Y-%m-%d %H:%M:%S"), **rec}) + "\n")


def _market(gran):
    max_bars = 6570 if gran == 14400 else 1500
    bars = {f"{c}-USD": data.get_bars(f"{c}-USD", granularity=gran, max_bars=max_bars) for c in DEFAULT_BASKET}
    f0 = regime.risk_on_timestamps("BTC-USD", granularity=gran, max_bars=max_bars)
    daily = data.get_bars("BTC-USD", 86400, max_bars=1500)
    f1 = f0 & (mf.f1_timestamps(daily) if gran == 86400 else mf.f1_timestamps_4h(daily, bars["BTC-USD"]))
    return bars, f1


def account_name(fam, gran, uni, tier):
    return f"{'chal' if gran == 86400 else 'c4h'}_{'fx' if tier == 1 else 'fxe'}_{fam}_{uni}"


def deployed_series(bars_by_gran, allow_by_gran):
    """Backtest daily-sampled equity of every non-explorer deployed account on the large universe
    (its own granularity and filters), for redundancy checks."""
    from .portfolio_correlation import _dated_daily_equity
    from .regime_learner import account_allow
    out = {}
    for f in sorted(os.listdir(os.path.join(BASE, "paper_state"))):
        if not f.endswith(".kraken.json") or f.startswith(("arena_", "krakendemo", "chal_fxe_", "c4h_fxe_")):
            continue
        try:
            st = json.load(open(os.path.join(BASE, "paper_state", f)))
            g = st.get("granularity")
            if g not in bars_by_gran or st.get("needs_revalidation"):
                continue
            bars = bars_by_gran[g]
            out[st["account"]] = _dated_daily_equity(st["strategy_path"], bars, 50.0 / len(bars),
                                                     account_allow(st, allow_by_gran[g], bars), g == 14400)
        except Exception:
            continue
    return out


def evaluate_combo(fam, gran, uni, bars_all, f1, real_fees=True):
    tpl = scaled(FAMILIES[fam], gran)
    coins = {f"{c}-USD": bars_all[f"{c}-USD"] for c in UNIVERSES[uni]}
    cands = list(candidates(tpl))
    rows = []
    for values, spec in cands:
        real, _ = kraken_fees.apply_to_spec(spec)
        r = _pooled(real, coins, cash=50.0, split=0.6, allow_buy_ts=f1)
        if r and r["fee_gate_ok"] and r["sample_ok"]:
            rows.append((r["pf_in"], values, real, r))
    if not rows:
        return None, {"n_candidates": len(cands), "survivors": 0, "why": "no candidate clears fee gate + sample"}
    rows.sort(key=lambda t: -t[0])   # IN-SAMPLE only
    pf, values, spec, r = rows[0]
    beat = sum(1 for x in rows if x[3]["out_excess"] > 0)
    ev = {"n_candidates": len(cands), "survivors": len(rows), "params": values, "pf_in": round(pf, 2),
          "n_in": r["n_in"], "out_return_pct": round(r["out_return"] * 100, 1),
          "out_excess_pct": round(r["out_excess"] * 100, 1),
          "robust": len(rows) >= ROBUST_MIN_SURVIVORS and len(rows) / len(cands) >= ROBUST_MIN_SHARE and beat / len(rows) >= 0.5}
    return (spec, coins), ev


def redundancy(spec, gran, large_bars, f1, dep_series):
    from .portfolio_correlation import _series_corr
    mine = _dated_daily_equity_tmp(spec, large_bars, f1, gran == 14400)
    worst = max((((_series_corr(mine, s)[0] or 0), n) for n, s in dep_series.items()), default=(0, None))
    return round(worst[0], 2), worst[1]


def tier1_checks(fam, gran, spec, coins, f1, ev):
    """Returns (ok, reasons). Reasons list every failed check (max_corr already in ev)."""
    fails = []
    if ev["max_corr"] >= REDUNDANCY_CORR: fails.append(f"redundant with {ev['most_similar']} (corr {ev['max_corr']:.2f})")
    if ev["out_excess_pct"] <= 0: fails.append("no OOS edge vs hold")
    if not ev["robust"]: fails.append("not a robust family")
    trades = L.collect_trades_for(spec, coins, f1)
    dist = summarize_distribution(bootstrap(trades, n_sims=1500, starting_cash=50.0), 50.0, 0.0)
    if dist.get("n_sims") and (dist["p5_return_pct"] < MC_P5 or dist["probability_of_losing_money"] > MC_LOSS):
        fails.append("Monte Carlo fragile")
    if gran == 86400 and not fails:
        from . import holdout
        hb = holdout.load(holdout.holdout_products())
        h = holdout.evaluate(spec, hb, f1) if hb else None
        if h is None or holdout.verdict(h)[0] != "PASS":
            fails.append("unseen coins: " + (holdout.verdict(h)[1] if h else "no data"))
    return not fails, fails


def _dated_daily_equity_tmp(spec, large_bars, allow, four_hour=False):
    from .portfolio_correlation import _dated_daily_equity
    tmp = os.path.join(BASE, "paper_state", "_factory_tmp.json")
    json.dump(spec, open(tmp, "w"))
    try:
        return _dated_daily_equity(os.path.relpath(tmp, BASE), large_bars, 50.0 / len(large_bars), allow, four_hour)
    finally:
        os.remove(tmp)


def deploy(name, fam, gran, uni, spec, coins, ev, tier):
    os.makedirs(OUT_DIR, exist_ok=True)
    rel = f"strategies/factory/{name}.json"
    s = json.loads(json.dumps(spec))
    s["name"] = f"FACTORY {'TIER1' if tier == 1 else 'EXPLORER'} {fam} {uni} {'daily' if gran == 86400 else '4h'} + F1"
    json.dump(s, open(os.path.join(BASE, rel), "w"), indent=2)
    kp.open_account(name, rel, list(coins), granularity=gran, cash=50.0, paper=True, market_filter="F1")
    L._add_cron(name)
    L._log(L.LOG, action="factory_spawn", account=name, tier=tier, evidence=ev)


def existing(tier=None):
    names = [f[:-len(".kraken.json")] for f in os.listdir(os.path.join(BASE, "paper_state")) if f.endswith(".kraken.json")]
    tag = {1: "_fx_", 2: "_fxe_"}
    return [n for n in names if any(tag[t] in n for t in ([tier] if tier else [1, 2]))]


def run(act=False, only_gran=None, verbose=True):
    plan = []
    grans = [only_gran] if only_gran else [86400, 14400]
    market = {g: _market(g) for g in grans}
    if 86400 not in market:
        market[86400] = _market(86400)
    for g in (14400,):
        if g not in market and any(os.path.exists(os.path.join(BASE, "paper_state", f)) for f in os.listdir(os.path.join(BASE, "paper_state")) if f.startswith("c4h_") and f.endswith(".kraken.json")):
            market[g] = _market(g)
    big = {g: ({f"{c}-USD": m[0][f"{c}-USD"] for c in UNIVERSES["large"]}, m[1]) for g, m in market.items()}
    dep = deployed_series({g: b for g, (b, _) in big.items()}, {g: f for g, (_, f) in big.items()})
    for gran in grans:
        bars, f1 = market[gran]
        large = big[gran][0]
        for fam in FAMILIES:
            for uni in UNIVERSES:
                names = [account_name(fam, gran, uni, t) for t in (1, 2)]
                if any(os.path.exists(os.path.join(BASE, "paper_state", f"{n}.kraken.json")) for n in names):
                    continue
                res, ev = evaluate_combo(fam, gran, uni, bars, f1)
                if res is None:
                    _log(kind="factory", family=fam, granularity=gran, universe=uni, reason="FEE BLEED", detail=ev["why"])
                    plan.append((fam, gran, uni, "REJECT", ev["why"])); continue
                spec, coins = res
                ev["max_corr"], ev["most_similar"] = redundancy(spec, gran, large, f1, dep)
                if ev["max_corr"] >= EXPLORER_REDUNDANCY:
                    _log(kind="factory", family=fam, granularity=gran, universe=uni, reason="REDUNDANT",
                         detail=f"corr {ev['max_corr']} with {ev['most_similar']}")
                    plan.append((fam, gran, uni, "REDUNDANT", f"{ev['max_corr']} vs {ev['most_similar']}"))
                    if verbose:
                        print(f"  {account_name(fam, gran, uni, 2):38s} SKIP redundant (corr {ev['max_corr']} with {ev['most_similar']})", flush=True)
                    continue
                ok, fails = tier1_checks(fam, gran, spec, coins, f1, ev)
                tier = 1 if ok else 2
                if not ok:
                    _log(kind="factory", family=fam, granularity=gran, universe=uni, reason="TIER1 FAIL -> explorer", detail="; ".join(fails))
                if len(existing(tier)) >= (MAX_TIER1 if tier == 1 else MAX_TIER2):
                    plan.append((fam, gran, uni, "CAP", "")); continue
                name = account_name(fam, gran, uni, tier)
                plan.append((fam, gran, uni, f"TIER{tier}", "; ".join(fails) or f"PF {ev['pf_in']} OOS {ev['out_return_pct']}%"))
                if act:
                    deploy(name, fam, gran, uni, spec, coins, {**ev, "tier1_fails": fails}, tier)
                if verbose:
                    print(f"  {name:38s} TIER{tier}  PF_in {ev['pf_in']}  OOS {ev['out_return_pct']:+.1f}%  corr {ev['max_corr']}  {'; '.join(fails)}", flush=True)
    return plan
