"""How many years of history does each tradable coin have?  (Coinbase daily candles, coins that also trade on Kraken.)"""
import json, os, time
from marketcoach import data
from marketcoach.broker_kraken import PAIR_MAP

BASE = os.path.dirname(os.path.abspath(__file__))
out = {}
for p in PAIR_MAP:
    try:
        b = data.fetch_coinbase_history(p, 86400, 3300, True, cache_tag="_long")
        out[p] = {"bars": len(b), "start": time.strftime("%Y-%m-%d", time.gmtime(b[0].ts)), "years": round(len(b) / 365.25, 1)}
    except Exception as e:
        out[p] = {"bars": 0, "err": type(e).__name__}
json.dump(out, open(os.path.join(BASE, "logs", "coin_universe.json"), "w"), indent=1)
for th in (3, 4, 5, 6, 7, 8):
    ok = [p[:-4] for p, v in out.items() if v.get("years", 0) >= th]
    print(f">= {th}y: {len(ok):2d} coins  {' '.join(ok)}")
print("\n" + "  ".join(f"{p[:-4]}:{v.get('years', 0)}" for p, v in sorted(out.items(), key=lambda kv: -kv[1].get('years', 0))))
