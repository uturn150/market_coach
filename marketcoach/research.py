"""Transcript analysis: extract STRUCTURED, skeptical findings from a trading/crypto
video transcript. This is the 'read the internet for us' step — it never decides
anything on its own. Findings are logged for human review and, at most, turned
into an optimizer TEMPLATE CANDIDATE — never applied directly to a live strategy.

Why so guarded: YouTube trading content is full of unverified claims, survivorship
bias, and outright hype. An LLM asked to "find good ideas" will happily invent
confidence it doesn't have. So the prompt forces it to separate concrete/testable
claims from hype, and every concrete claim still has to clear OUR gates (fee gate +
sample-size gate + walk-forward) before it means anything.
"""
from . import llm, journal

SYSTEM = """You are a skeptical quant research assistant reviewing a YouTube video \
transcript about trading/crypto/coding trading bots. Your job is NOT to summarize \
the video enthusiastically — it is to extract only what is concrete and testable, \
and to flag hype separately. Assume the creator may be exaggerating, selling a \
product, or reporting unverified/self-reported results.

Return a JSON object with exactly these fields:
{
  "relevant": true/false,   // does this actually discuss trading strategy/bot mechanics?
  "one_line_summary": "...",
  "concrete_ideas": [        // ONLY specific, testable rules/indicators/risk practices
    {"idea": "short name", "detail": "what exactly to test", "category": "entry|exit|risk|data|fees"}
  ],
  "coins_or_assets_mentioned": ["BTC", ...],
  "red_flags": [ "unverifiable claim, product pitch, etc." ],
  "credibility_notes": "one honest sentence on how much to trust this video's claims"
}
If nothing concrete/testable is present, return an empty concrete_ideas list — do
not invent substance. Be terse. Output ONLY the JSON object."""


def analyze_transcript(text, title="", max_chars=12000):
    """Run the transcript through the LLM. Returns the parsed finding dict, or a
    dict with an '_error'/'_no_llm' key if analysis wasn't possible — callers
    should log and skip rather than crash."""
    if not llm.available():
        return {"_no_llm": True}
    snippet = text[:max_chars]
    user = f"Video title: {title}\n\nTranscript:\n{snippet}"
    result = llm.chat_json(SYSTEM, user)
    if result is None:
        return {"_no_llm": True}
    return result


def log_finding(video_id, title, finding, stream="research"):
    if finding.get("_no_llm") or finding.get("_error"):
        journal.log(stream, "analyze_skipped", video_id=video_id, title=title,
                    reason=finding.get("_error", "no LLM key configured"))
        return
    journal.log(stream, "analyzed", video_id=video_id, title=title,
                relevant=finding.get("relevant"), summary=finding.get("one_line_summary"),
                n_ideas=len(finding.get("concrete_ideas", [])),
                ideas=finding.get("concrete_ideas", []),
                coins=finding.get("coins_or_assets_mentioned", []),
                red_flags=finding.get("red_flags", []),
                credibility=finding.get("credibility_notes"))
