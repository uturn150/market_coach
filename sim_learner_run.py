"""Let the bot LEARN in the simulator. Train on 2020-2023 episodes (fast, daily-bar engine), then judge the
survivors on (a) 2024-26 years never seen (same coins) and (b) REAL held-out coins (marketcoach.holdout.
holdout_products(): never used to build any strategy in this project -- fixed 2026-09-22, the old check reused
in-basket coins). The top 3 finalists then get a MINUTE-ENGINE check (real fills, minute by minute, not once-a-day)
on both legs, now that the minute-data download is effectively complete.
    python3 sim_learner_run.py [generations]"""
import sys, time, calendar
from marketcoach import market_sim as ms, sim_learner as sl

def T(y, m, d): return calendar.timegm((y, m, d, 0, 0, 0))

def main():
    gens = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    bars = ms.load_long()
    train_coins = list(bars)                                            # all 9-12 coins with deep daily history
    f0, f1 = ms.filters_for(bars)
    W_train = sl.World({c: bars[c] for c in train_coins}, f0, f1)
    eps_train = W_train.episodes(T(2020, 3, 1), T(2023, 12, 31))
    eps_future = W_train.episodes(T(2024, 1, 1), T(2026, 9, 1))
    print(f"train coins {train_coins} | {len(eps_train)} training episodes (12-month, monthly starts, 2020-2023)")
    t = time.time()
    top, curve = sl.evolve(W_train, eps_train, generations=gens)
    print(f"\nlearning curve (best training score per generation): {curve}  ({time.time()-t:.0f}s)\n")

    print(f"{'genome':100s} train  | FUTURE yrs (daily): score medRet avgDD green% | UNSEEN coins (daily, REAL held-out): score medRet avgDD green%")
    import random
    rng = random.Random(7)
    rows = []
    for r in top[:6]:
        g = r["genome"]
        ef = W_train.evaluate(g, eps_future)
        eu = []
        # real held-out coin subsets (5 at a time, 3 draws for more coverage)
        from marketcoach.holdout import holdout_products
        pool = holdout_products()
        for _ in range(3):
            s = sorted(rng.sample(pool, min(5, len(pool))))
            wu = sl.unseen_world(s, f0, f1)
            if wu is None:
                continue
            eps_u = wu.episodes(wu.ts[0], wu.ts[-1])
            if eps_u:
                eu += wu.evaluate(g, eps_u)
        f = lambda e: (sl.score(e), sorted(x["ret"] for x in e)[len(e) // 2] * 100, sum(abs(x["dd"]) for x in e) / len(e) * 100, sum(x["green_active"] for x in e) / len(e) * 100) if e else (0, 0, 0, 0)
        a, b = f(ef), f(eu)
        note = "" if eu else "  (no held-out combo had enough history)"
        print(f"{sl.key(g)[:100]:100s} {r['train_score']:6.1f} | {a[0]:7.1f} {a[1]:+6.1f}% {a[2]:5.1f}% {a[3]:5.1f}% | {b[0]:7.1f} {b[1]:+6.1f}% {b[2]:5.1f}% {b[3]:5.1f}%{note}")
        rows.append((g, r["train_score"], a, b))

    print(f"\n== minute-engine check on the top 3 (real fills, real held-out coins, 2024-26) ==")
    top3 = rows[:3]
    for g, tscore, a, b in top3:
        mw_train = sl.MinuteWorld(train_coins[:5], f0, f1)
        eps_mt = mw_train.episodes(T(2024, 1, 1), T(2026, 9, 1))
        t0 = time.time()
        em_t = mw_train.evaluate(g, eps_mt)
        from marketcoach.holdout import holdout_products
        pool = holdout_products()
        held_5 = sorted(pool)[:5]
        mw_u = sl.MinuteWorld(held_5, f0, f1)
        eps_mu = mw_u.episodes(T(2024, 1, 1), T(2026, 9, 1))
        em_u = mw_u.evaluate(g, eps_mu) if eps_mu else []
        f = lambda e: (sorted(x["ret"] for x in e)[len(e) // 2] * 100, sum(abs(x["dd"]) for x in e) / len(e) * 100) if e else (0.0, 0.0)
        mt, mu = f(em_t), f(em_u)
        print(f"{sl.key(g)[:100]:100s} minute/train(5 coins) med {mt[0]:+6.1f}% dd {mt[1]:5.1f}% | minute/held-out(5 coins) med {mu[0]:+6.1f}% dd {mu[1]:5.1f}%  [{time.time()-t0:.0f}s]")

if __name__ == "__main__":
    main()
