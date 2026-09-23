"""Daily progress snapshot + report. --no-push to skip the Telegram message."""
import sys
from marketcoach import progress
if __name__ == "__main__":
    progress.run(push="--no-push" not in sys.argv)
