"""Self-built YouTube transcript fetcher — pure stdlib, no third-party paid API.
Parses the caption track URL out of the watch page's embedded player data (the
modern approach; the old video.google.com/timedtext?type=list endpoint is
deprecated and returns empty for everyone now, block or no block), then fetches
that track and flattens it to plain text.

Reality check (found by testing, not assumed): this box's outbound IP sits behind
shared cloud infrastructure whose reputation with YouTube/Cloudflare FLUCTUATES —
it can work one day and get soft-blocked the next, independent of this code. So
every call here is retried a few times with backoff and treated as a SKIP (not a
crash) on failure — the pipeline logs it and moves on. A run on a clean IP (a
laptop, a normal home connection) will succeed far more reliably; this module
works identically there, no changes needed.
"""
import html, json, re, time, urllib.request, urllib.parse
import xml.etree.ElementTree as ET

WATCH_URL = "https://www.youtube.com/watch?v={vid}"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
          "Accept-Language": "en-US,en;q=0.9"}


class BlockedOrUnavailable(Exception):
    pass


def _get(url, timeout=15):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def _caption_tracks(video_id):
    body = _get(WATCH_URL.format(vid=video_id))
    if "confirm you" in body.lower() or "unusual traffic" in body.lower():
        raise BlockedOrUnavailable(f"{video_id}: watch page served a bot-check page")
    m = re.search(r'"captionTracks":(\[.*?\])', body)
    if not m:
        return []  # genuinely no captions on this video — not necessarily a block
    return json.loads(m.group(1))


def fetch(video_id, lang="en", retries=2, backoff=3.0):
    """Fetch + flatten a transcript to plain text. Retries transient failures;
    raises BlockedOrUnavailable if all attempts are refused, or ValueError if the
    video simply has no captions (a real, non-block outcome)."""
    last_err = None
    for attempt in range(retries + 1):
        try:
            tracks = _caption_tracks(video_id)
            if not tracks:
                raise ValueError(f"{video_id}: no caption tracks (video has none, not a block)")
            match = next((t for t in tracks if t.get("languageCode", "").startswith(lang)), tracks[0])
            url = html.unescape(match["baseUrl"])
            body = _get(url)
            if not body.strip():
                raise BlockedOrUnavailable(f"{video_id}: empty caption body")
            root = ET.fromstring(body)
            parts = []
            for node in root.findall(".//text"):
                text = "".join(node.itertext())
                text = re.sub(r"&#?\w+;", " ", text)
                if text.strip():
                    parts.append(text.strip())
            if not parts:
                raise BlockedOrUnavailable(f"{video_id}: caption body had no text nodes")
            return " ".join(parts)
        except ValueError:
            raise  # genuine "no captions" — retrying won't help
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(backoff * (attempt + 1))
    raise BlockedOrUnavailable(f"{video_id}: failed after {retries+1} attempts — "
                               f"{type(last_err).__name__}: {last_err}")
