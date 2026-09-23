"""Replay the learner over history without lookahead. Slow (~30-60 min). Results: logs/learner_replay.json"""
import sys
sys.path.insert(0, ".")
from marketcoach import learner_replay
if __name__ == "__main__":
    out = learner_replay.run()
    print("\nperiod", out["period"])
    print(f"{'policy':26s} {'return':>8s} {'max dd':>8s} {'green wk%':>10s} {'of active':>10s} {'worst wk':>9s} {'green day%':>10s}")
    for k, s in out["results"].items():
        if s:
            print(f"{k:26s} {s['total_return_pct']:>+7.1f}% {s['max_dd_pct']:>7.1f}% {s['green_week_pct']:>9.1f}% {s['green_of_active_wk_pct']:>9.1f}% {s['worst_week_pct']:>8.1f}% {s['green_day_pct']:>9.1f}%")
