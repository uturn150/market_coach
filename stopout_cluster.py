"""Do DCA stop-outs cluster (same coin / same period)?  If losses on a coin are followed by more losses on that coin
sooner than chance, a per-coin cooldown after a stop could help.  python3 stopout_cluster.py"""
import random
import bot_universes as bu
from marketcoach import market_sim as ms, bot_engine as be
from bot_lab import configs, CASH
NAME = "dca SO5 dev5% x2.0 tp3% stop0.2 F1"

def events(r):
    sells = [f for f in r.fills if f["side"] == "sell"]
    assert len(sells) >= len(r.trips)
    return [(s["ts"], t[0], t[2]) for s, t in zip(sells, r.trips)]      # (ts, coin, net return)

def main():
    base = ms.load_long(); f0, f1 = ms.filters_for(base)
    U = {"core9": base, "large12": bu.load(bu.UNIVERSES["large"]), "mid12": bu.load(bu.UNIVERSES["mid"]),
         "new20": bu.load([p[:-4] for p in bu.holdout.holdout_products()])}
    D = 86400; rng = random.Random(7)
    for uni, bars in U.items():
        ts = [b.ts for b in next(iter(bars.values()))]
        C = configs({"none": None, "F0": (lambda i, s=f0, t=ts: t[i] in s), "F1": (lambda i, s=f1, t=ts: t[i] in s)})
        r = be.run(C[NAME][1](), bars, cash=CASH); ev = sorted(events(r))
        loss = [e for e in ev if e[2] < -0.02]                              # stop-outs / deep losers
        pnl_all = sum(e[2] for e in ev); pnl_loss = sum(e[2] for e in loss)
        print(f"\n{uni}: {len(ev)} trips, {len(loss)} stop-outs ({100*len(loss)/len(ev):.1f}%), avg stop {100*pnl_loss/max(1,len(loss)):+.1f}%, sum stops {pnl_loss*100:+.0f}% vs sum all {pnl_all*100:+.0f}%")
        # 1) same-coin follow-up: after a stop on coin X, is the NEXT trip on X a loser more often than base?
        by = {}
        for e in ev: by.setdefault(e[1], []).append(e)
        nxt = [(l, seq[k+1]) for seq in by.values() for k, l in enumerate(seq[:-1]) if l[2] < -0.02]
        base_p = sum(1 for e in ev if e[2] < -0.02) / len(ev)
        if nxt:
            p_after = sum(1 for _, n in nxt if n[2] < -0.02) / len(nxt)
            avg_after = sum(n[2] for _, n in nxt) / len(nxt)
            print(f"   same coin: P(next trip loses | prior stop) {p_after*100:.1f}% vs base {base_p*100:.1f}%; avg next-trip return {avg_after*100:+.2f}% vs overall {100*pnl_all/len(ev):+.2f}%  (n={len(nxt)})")
        # 2) cross-coin: stops within 7 days of another stop, vs shuffled-coin placebo
        def near(evs):
            t = sorted(e[0] for e in evs); c = 0
            for k, x in enumerate(t):
                if (k and x - t[k-1] <= 7*D) or (k+1 < len(t) and t[k+1] - x <= 7*D): c += 1
            return c / max(1, len(t))
        span0, span1 = ev[0][0], ev[-1][0]
        plac = []
        for _ in range(200):
            fake = [(rng.uniform(span0, span1),) for _ in loss]
            plac.append(near(fake))
        print(f"   time clustering: {near(loss)*100:.0f}% of stops have another stop within 7d; placebo (random dates) {100*sum(plac)/len(plac):.0f}%")
        # 3) would a cooldown have helped?  skip coin re-entry for N days after a stop: pnl of trips that START within N days
        for nd in (7, 14, 30):
            sk = []
            for seq in by.values():
                for k in range(1, len(seq)):
                    prev_loss = [p for p in seq[:k] if p[2] < -0.02]
                    # trip k entered roughly at (exit_k - hold); we only have exit ts, so use gap between exits as proxy
                    if prev_loss and seq[k][0] - prev_loss[-1][0] <= nd*D: sk.append(seq[k][2])
            if sk: print(f"   cooldown {nd:2d}d would skip {len(sk)} trips, their summed return {sum(sk)*100:+.1f}% (positive = cooldown would have cost money)")

main()
