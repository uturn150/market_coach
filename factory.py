"""Strategy factory (see marketcoach/factory.py). Dry run by default; --act creates paper accounts.
   python3 factory.py [--act] [--daily|--4h]"""
import sys
from marketcoach import factory
if __name__ == "__main__":
    g = 86400 if "--daily" in sys.argv else 14400 if "--4h" in sys.argv else None
    plan = factory.run(act="--act" in sys.argv, only_gran=g)
    from collections import Counter
    print(Counter(p[3] for p in plan))
