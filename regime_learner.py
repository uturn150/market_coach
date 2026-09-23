"""Regime learner. Default = dry run (learns/reports, changes nothing). --act persists memory and may
spawn paper challengers / add tighten-only restrictions. --light = live loop only (fast, hourly). Cron: light hourly, full every 6h."""
import sys
from marketcoach import regime_learner
if __name__ == "__main__":
    regime_learner.run(act="--act" in sys.argv, light="--light" in sys.argv)
