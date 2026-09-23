"""Train the $50 slow trader in the simulator. Train on 2020-2023 (the 9 coins with 6+ years of real history), judge
survivors on 2024-26 (same coins) and on REAL held-out coins (marketcoach.holdout.holdout_products(): the ~20 coins
never used to build any strategy in this project) -- not a second sample of coins the search already knows.
    python3 train_slow50.py [generations]"""
import calendar, random, sys, time
from marketcoach import market_sim as ms, slow_learner as sl


def T(y, m, d): return calendar.timegm((y, m, d, 0, 0, 0))


def main():
    gens = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    bars = ms.load_long(coins=[c[:-4] for c in sl.TRAIN_COINS]); f0, f1 = ms.filters_for(bars)
    W = sl.World(bars, {"F0": f0, "F1": f1})
    e_train = W.episodes(T(2020, 3, 1), T(2023, 12, 31)); e_future = W.episodes(T(2024, 1, 1), T(2026, 9, 1))
    print(f"{len(e_train)} training episodes (2020-23, coins {sl.TRAIN_COINS}) | {len(e_future)} future episodes (2024-26) "
          f"| unseen pool: {len(sl.UNSEEN_COINS)} real held-out coins\n")
    t = time.time()
    top, curve = sl.evolve(W, e_train, generations=gens)
    print(f"\nlearning curve: {curve}  ({time.time()-t:.0f}s)\n")
    print(f"{'genome':118s} train | FUTURE 2024-26: med yr  avg dd  worst dd | UNSEEN COINS (real held-out): med yr  avg dd  worst dd")
    rng = random.Random(5)
    for r in top[:6]:
        g = r["genome"]
        ef = W.evaluate(g, e_future)
        eu = []
        subsets = [sorted(rng.sample(sl.UNSEEN_COINS, g["ncoins"] if g["ncoins"] <= 4 else 4)) for _ in range(3)]
        for s in subsets:
            wu = sl.unseen_world(s, {"F0": f0, "F1": f1})
            if wu is None:
                continue
            eps = wu.episodes(wu.ts[0], wu.ts[-1])
            if eps:
                eu += wu.evaluate(g, eps, coins=s)
        f = lambda e: (sorted(x["ret"] for x in e)[len(e) // 2] * 100, sum(abs(x["dd"]) for x in e) / len(e) * 100, max(abs(x["dd"]) for x in e) * 100) if e else (0.0, 0.0, 0.0)
        a, b = f(ef), f(eu)
        note = "" if eu else "  (no held-out coin combo had enough history)"
        print(f"{sl.key(g)[:118]:118s} {r['train_score']:5.1f} |              {a[0]:+6.1f}% {a[1]:6.1f}% {a[2]:8.1f}% |                                 {b[0]:+6.1f}% {b[1]:6.1f}% {b[2]:8.1f}%{note}")


if __name__ == "__main__":
    main()
