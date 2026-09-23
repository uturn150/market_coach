#!/usr/bin/env python3
"""Daily research pipeline: search YouTube for trading/bot content, fetch
transcripts (best-effort — YouTube may soft-block this box on any given day),
analyze whatever transcripts land (auto-fetched OR manually pasted), and log a
digest. NEVER auto-applies anything to a live strategy — findings are for you
to review, and any concrete idea worth testing becomes an optimizer TEMPLATE
CANDIDATE at most, which still has to clear the fee gate + walk-forward like
everything else.

Usage:
  python3 research_pipeline.py daily              # search + fetch + analyze + digest
  python3 research_pipeline.py daily --no-search   # skip search, just process the inbox
  python3 research_pipeline.py inbox               # analyze anything dropped in research_inbox/
  python3 research_pipeline.py digest              # print today's findings
  python3 research_pipeline.py add <video_id> <path_to_pasted_transcript.txt>

Drop a pasted transcript into research_inbox/<video_id>.txt any time (name it
whatever — video_id is inferred from filename minus extension) and 'inbox' or
the next 'daily' run will pick it up automatically.
"""
import glob, json, os, sys, time
from marketcoach import yt_search, transcript, research, journal, alerts

BASE = os.path.dirname(__file__)
INBOX_DIR = os.path.join(BASE, "research_inbox")
SEEN_FILE = os.path.join(BASE, "paper_state", "research_seen.json")


def _seen():
    if os.path.exists(SEEN_FILE):
        try:
            return set(json.load(open(SEEN_FILE)))
        except Exception:
            pass
    return set()


def _mark_seen(video_id, seen):
    seen.add(video_id)
    os.makedirs(os.path.dirname(SEEN_FILE), exist_ok=True)
    json.dump(sorted(seen), open(SEEN_FILE, "w"))


def _stub(video_id, title):
    os.makedirs(INBOX_DIR, exist_ok=True)
    stub = os.path.join(INBOX_DIR, f"{video_id}.needs_paste.txt")
    if not os.path.exists(stub):
        open(stub, "w").write(f"# Could not auto-fetch: {title}\n"
                              f"# https://www.youtube.com/watch?v={video_id}\n"
                              f"# Paste the transcript below, rename to {video_id}.txt\n")


def process_inbox(seen):
    os.makedirs(INBOX_DIR, exist_ok=True)
    analyzed = 0
    for path in glob.glob(os.path.join(INBOX_DIR, "*.txt")):
        video_id = os.path.splitext(os.path.basename(path))[0]
        if video_id in seen:
            continue
        text = open(path, errors="replace").read()
        if len(text.strip()) < 200:
            continue  # too short to be a real transcript
        finding = research.analyze_transcript(text, title=video_id)
        research.log_finding(video_id, video_id, finding)
        _mark_seen(video_id, seen)
        analyzed += 1
        print(f"  [inbox] analyzed {video_id} ({len(text)} chars)")
    return analyzed


def run_daily(do_search=True, per_query=4, days_back=14):
    seen = _seen()
    journal.log("research", "run_start", search=do_search)

    fetched, blocked, no_captions = 0, 0, 0
    if do_search:
        if not yt_search.available():
            print("No YOUTUBE_API_KEY configured — skipping search. "
                  "(paper_state/research.keys.json or env YOUTUBE_API_KEY)")
        else:
            candidates = yt_search.discover(per_query=per_query, days_back=days_back)
            print(f"Search found {len(candidates)} candidate videos.")
            consecutive_blocks = 0
            for v in candidates:
                vid = v["video_id"]
                if vid in seen:
                    continue
                if consecutive_blocks >= 3:
                    # Adaptive circuit breaker: 3 blocks in a row means this box's IP
                    # is having a bad day network-wide — stop burning retries on every
                    # remaining candidate, just stub them all for manual paste instead.
                    _stub(vid, v["title"])
                    continue
                try:
                    text = transcript.fetch(vid, retries=1, backoff=1.5)
                    finding = research.analyze_transcript(text, title=v["title"])
                    research.log_finding(vid, v["title"], finding)
                    _mark_seen(vid, seen)
                    fetched += 1
                    consecutive_blocks = 0
                    print(f"  [auto] {v['title'][:60]} -> analyzed")
                except transcript.BlockedOrUnavailable as e:
                    blocked += 1
                    consecutive_blocks += 1
                    journal.log("research", "fetch_blocked", video_id=vid, title=v["title"],
                                error=str(e))
                    _stub(vid, v["title"])
                except ValueError:
                    no_captions += 1  # video genuinely has no captions; not worth logging noisily
                time.sleep(0.5)
            if consecutive_blocks >= 3:
                journal.log("research", "circuit_breaker", note="3+ consecutive fetch blocks — "
                            "stopped live-fetch attempts for the rest of this run, stubbed the rest")

    inbox_analyzed = process_inbox(seen)

    journal.log("research", "run_complete", auto_fetched=fetched, blocked=blocked,
                no_captions=no_captions, inbox_analyzed=inbox_analyzed)
    print(f"\nRun complete: {fetched} auto-fetched+analyzed, {blocked} blocked "
          f"(stubs in research_inbox/ for manual paste), {inbox_analyzed} from inbox.")
    total_analyzed = fetched + inbox_analyzed
    if total_analyzed > 0:
        alerts.notify("market_coach: daily research digest",
                     f"{total_analyzed} video(s) analyzed today, {blocked} blocked "
                     f"(paste transcripts into research_inbox/ if you want them anyway). "
                     f"Nothing auto-applied to any live strategy.",
                     priority="low", tags=["mag"])
    digest()


def digest(days=1):
    cutoff = time.time() - days * 86400
    rows = [r for r in journal.read("research", event="analyzed") if r["t"] >= cutoff]
    if not rows:
        print("No analyzed findings in this window.")
        return
    print(f"\n=== Research digest (last {days}d, {len(rows)} videos) ===")
    all_ideas, all_flags = [], []
    for r in rows:
        print(f"\n{r['title'][:70]}")
        print(f"  {r.get('summary','')}")
        for idea in r.get("ideas", []):
            print(f"    idea [{idea.get('category')}]: {idea.get('idea')} — {idea.get('detail')}")
            all_ideas.append(idea)
        for flag in r.get("red_flags", []):
            print(f"    RED FLAG: {flag}")
            all_flags.append(flag)
    print(f"\n{len(all_ideas)} concrete ideas, {len(all_flags)} red flags surfaced. "
          "Nothing auto-applied — review and, if worth testing, build a strategy "
          "template and run it through optimize.py like any other candidate.")


def main():
    if len(sys.argv) < 2:
        print(__doc__); return
    cmd = sys.argv[1]
    if cmd == "daily":
        run_daily(do_search="--no-search" not in sys.argv)
    elif cmd == "inbox":
        n = process_inbox(_seen())
        print(f"Analyzed {n} inbox transcript(s).")
    elif cmd == "digest":
        digest(days=int(sys.argv[2]) if len(sys.argv) > 2 else 1)
    elif cmd == "add":
        vid, path = sys.argv[2], sys.argv[3]
        os.makedirs(INBOX_DIR, exist_ok=True)
        text = open(path, errors="replace").read()
        open(os.path.join(INBOX_DIR, f"{vid}.txt"), "w").write(text)
        print(f"Added to inbox: {vid}.txt ({len(text)} chars). Run 'inbox' to analyze.")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
