"""Alerting: the bot tells YOU when something happened, instead of you having to
go run dashboard.py and check. Cron jobs run as plain standalone scripts with no
way to call an assistant-side notification tool, so genuinely autonomous
alerting needs the bot's OWN channel. Two are supported:

  - Telegram (preferred when configured) — reuses this box's existing bot
    (@Uturn150bot, already used by other automation here) via the plain HTTP
    Bot API. No new app for the user to install.
  - ntfy.sh (fallback) — free, no-signup push notification service.

Every call is best-effort and NEVER raises — a notification failing must never
crash a cron job that's doing real trading logic.
"""
import json, os, urllib.request, urllib.parse

KEYS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "paper_state", "alert.keys.json")
NTFY_URL = "https://ntfy.sh/{topic}"
TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"


def _config():
    if os.path.exists(KEYS_FILE):
        try:
            return json.load(open(KEYS_FILE))
        except Exception:
            pass
    return {}


def enabled():
    cfg = _config()
    has_telegram = bool(os.environ.get("TELEGRAM_BOT_TOKEN") or cfg.get("telegram_bot_token"))
    has_ntfy = bool(os.environ.get("NTFY_TOPIC") or cfg.get("ntfy_topic"))
    return has_telegram or has_ntfy


def channel():
    cfg = _config()
    if os.environ.get("TELEGRAM_BOT_TOKEN") or cfg.get("telegram_bot_token"):
        return "telegram"
    if os.environ.get("NTFY_TOPIC") or cfg.get("ntfy_topic"):
        return "ntfy"
    return "none"


def _send_telegram(title, message, cfg):
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or cfg.get("telegram_bot_token")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID") or cfg.get("telegram_chat_id")
    if not (token and chat_id):
        return False
    text = f"*{title}*\n{message}"
    body = urllib.parse.urlencode({"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}).encode()
    req = urllib.request.Request(TELEGRAM_URL.format(token=token), data=body, method="POST")
    urllib.request.urlopen(req, timeout=10)
    return True


def _send_ntfy(title, message, priority, tags, cfg):
    topic = os.environ.get("NTFY_TOPIC") or cfg.get("ntfy_topic")
    if not topic:
        return False
    headers = {"Title": title, "Priority": priority}
    if tags:
        headers["Tags"] = ",".join(tags)
    req = urllib.request.Request(NTFY_URL.format(topic=topic), data=message.encode(),
                                 headers=headers, method="POST")
    urllib.request.urlopen(req, timeout=10)
    return True


def notify(title, message, priority="default", tags=None):
    """Best-effort push notification via Telegram (preferred) or ntfy (fallback).
    priority: min/low/default/high/urgent (ntfy only). tags: ntfy emoji-shortcodes,
    e.g. ["warning"] (ignored by Telegram). Returns True/False; never raises, so a
    notification failure can't take down a cron job."""
    cfg = _config()
    try:
        if _send_telegram(title, message, cfg):
            return True
    except Exception:
        pass
    try:
        return _send_ntfy(title, message, priority, tags, cfg)
    except Exception:
        return False
