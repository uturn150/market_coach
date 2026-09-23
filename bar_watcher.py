#!/usr/bin/env python3
"""Bar watcher: shrinks the lag between "a bar actually closed" and "a bot
noticed and acted" from up to ~59 minutes (waiting for its one fixed cron
minute per hour) down to under ~1 minute. Does NOT change trade frequency or
fees — see marketcoach/bar_watcher.py's docstring.

Meant to be cron'd every minute.
"""
from marketcoach import bar_watcher


if __name__ == "__main__":
    bar_watcher.run(verbose=True)
