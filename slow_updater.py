"""Self-updating slow trader (paper only). See marketcoach/slow_updater.py.   python3 slow_updater.py [--apply | --rollback]"""
import sys
from marketcoach import slow_updater
if __name__ == "__main__":
    slow_updater.rollback() if "--rollback" in sys.argv else slow_updater.run(apply="--apply" in sys.argv)
