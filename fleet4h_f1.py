"""Spawn F1-filtered 4H paper challengers. Dry run by default; --act to create accounts."""
import sys
from marketcoach import fleet4h_f1
if __name__ == "__main__":
    fleet4h_f1.run(act="--act" in sys.argv)
