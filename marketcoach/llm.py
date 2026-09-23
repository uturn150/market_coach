"""Minimal stdlib LLM client for the research pipeline (Groq primary, OpenAI
fallback — same providers lorcana_video_bot uses, kept self-contained here so
market_coach has no cross-project dependency). Used to extract STRUCTURED,
skeptical findings from video transcripts — never to freely narrate them.
"""
import json, os, urllib.request

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"
OPENAI_MODEL = "gpt-4o-mini"

KEYS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "paper_state", "research.keys.json")


def _keys():
    d = {}
    if os.path.exists(KEYS_FILE):
        try:
            d = json.load(open(KEYS_FILE))
        except Exception:
            pass
    return {
        "groq": os.environ.get("GROQ_API_KEY") or d.get("groq_api_key"),
        "openai": os.environ.get("OPENAI_API_KEY") or d.get("openai_api_key"),
    }


def available():
    k = _keys()
    return bool(k["groq"] or k["openai"])


def chat_json(system, user, max_tokens=1500):
    """Call the LLM asking for a JSON object back. Returns the parsed dict, or
    None if no key is configured or the call/parse fails (caller should degrade
    gracefully — this pipeline must never crash a scheduled run)."""
    k = _keys()
    if k["groq"]:
        url, key, model = GROQ_URL, k["groq"], GROQ_MODEL
    elif k["openai"]:
        url, key, model = OPENAI_URL, k["openai"], OPENAI_MODEL
    else:
        return None

    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.1,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.load(r)
        text = resp["choices"][0]["message"]["content"]
        return json.loads(text)
    except Exception as e:
        return {"_error": f"{type(e).__name__}: {str(e)[:200]}"}
