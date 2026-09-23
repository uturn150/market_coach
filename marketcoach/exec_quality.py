"""Execution-quality summary from the journaled fill records that carry the
realistic-paper-fill fields (signal price, expected fill, simulated fill,
spread/slippage/fee cost). Read-only. This is the dataset a future tiny real
trade would be compared against: does the simulated fill match what Kraken
actually gave us?"""
import glob, os
from . import journal

LOG_DIR = journal.LOG_DIR


def summarize(accounts=None):
    names = accounts or [os.path.basename(p)[:-6] for p in glob.glob(os.path.join(LOG_DIR, "*.jsonl"))]
    rows = []
    for n in names:
        for e in journal.read(n, event="kraken_fill"):
            if "sim_fill" in e:
                e = dict(e); e["account"] = n
                rows.append(e)
    if not rows:
        return {"n_fills": 0}
    def notional(e):
        return e.get("notional") if e["side"] == "buy" else e.get("proceeds", 0.0)
    total_notional = sum(notional(e) or 0 for e in rows) or 1e-9
    tot = lambda k: sum(e.get(k, 0.0) or 0 for e in rows)
    slip_vs_signal = [
        (e["sim_fill"] / e["signal_price"] - 1) * (1 if e["side"] == "buy" else -1) * 1e4
        for e in rows if e.get("signal_price")]
    return {
        "n_fills": len(rows),
        "book_fills": sum(1 for e in rows if e.get("source") == "book"),
        "fallback_fills": sum(1 for e in rows if str(e.get("source", "")).startswith("fallback")),
        "partial_fills": sum(1 for e in rows if (e.get("filled_fraction") or 1) < 0.999),
        "spread_cost_usd": round(tot("spread_cost_usd"), 4),
        "slippage_cost_usd": round(tot("slippage_cost_usd"), 4),
        "fee_usd": round(tot("fee_usd"), 4),
        "total_exec_cost_usd": round(tot("total_exec_cost_usd"), 4),
        "exec_cost_pct_of_notional": round(tot("total_exec_cost_usd") / total_notional * 100, 3),
        "fee_share_of_exec_cost_pct": round(tot("fee_usd") / (tot("total_exec_cost_usd") or 1e-9) * 100, 1),
        "avg_adverse_vs_signal_bps": round(sum(slip_vs_signal) / len(slip_vs_signal), 1) if slip_vs_signal else None,
    }


if __name__ == "__main__":
    for k, v in summarize().items():
        print(f"{k}: {v}")
