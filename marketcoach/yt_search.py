"""YouTube video discovery via the official YouTube Data API v3 (search.list).
Distinct from the caption/audio scraping that got IP-blocked earlier — this is a
normal Google Cloud API call with a key, not a scrape, so it isn't subject to
that block. Free tier quota is generous (10,000 units/day; each search ~100 units,
so ~100 searches/day) but not unlimited — the daily pipeline uses a small,
targeted query list rather than broad crawling.
"""
import json, os, time, urllib.request, urllib.parse

KEYS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "paper_state", "research.keys.json")
SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"

DEFAULT_QUERIES = [
    "algo trading bot strategy",
    "crypto trading bot backtest",
    "quant trading strategy python",
    "trend following trading strategy crypto",
    "trading bot risk management",
]


def _key():
    if os.environ.get("YOUTUBE_API_KEY"):
        return os.environ["YOUTUBE_API_KEY"]
    if os.path.exists(KEYS_FILE):
        return json.load(open(KEYS_FILE)).get("youtube_api_key")
    return None


def available():
    return bool(_key())


def search(query, max_results=5, days_back=None, order="relevance"):
    """Return [{video_id, title, channel, published}] or [] if no key / on error."""
    key = _key()
    if not key:
        return []
    params = {"part": "snippet", "q": query, "type": "video", "maxResults": max_results,
             "order": order, "key": key}
    if days_back:
        published_after = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                        time.gmtime(time.time() - days_back * 86400))
        params["publishedAfter"] = published_after
    url = SEARCH_URL + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            data = json.load(r)
    except Exception:
        return []
    out = []
    for item in data.get("items", []):
        vid = item.get("id", {}).get("videoId")
        if not vid:
            continue
        sn = item.get("snippet", {})
        out.append({"video_id": vid, "title": sn.get("title", ""),
                   "channel": sn.get("channelTitle", ""),
                   "published": sn.get("publishedAt", "")})
    return out


def discover(queries=None, per_query=5, days_back=14):
    """Run several queries, dedup by video_id, return the merged candidate list."""
    queries = queries or DEFAULT_QUERIES
    seen, out = set(), []
    for q in queries:
        for v in search(q, max_results=per_query, days_back=days_back):
            if v["video_id"] not in seen:
                seen.add(v["video_id"])
                v["query"] = q
                out.append(v)
        time.sleep(0.3)
    return out
