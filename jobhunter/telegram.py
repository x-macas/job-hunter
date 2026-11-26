"""Telegram: alerts to your phone, and a bot you can send links and commands to.

Security (rule 8): the bot only listens to ONE chat, yours. You connect it by
sending a 6-digit code shown on the Settings page. Messages from anyone else
are ignored.
Security (rule 10): alerts are short. No full emails, no resume.
"""

import json
import secrets as pysecrets
import urllib.error
import urllib.parse
import urllib.request

from .config import SECRETS_DIR, write_private_file

FILE = SECRETS_DIR / "telegram.json"
API = "https://api.telegram.org/bot{token}/{method}"


class TelegramError(Exception):
    pass


def load() -> dict:
    if not FILE.exists():
        return {}
    return json.loads(FILE.read_text())


def store(data: dict) -> None:
    write_private_file(FILE, json.dumps(data).encode())


def set_token(token: str) -> str:
    """Save a new bot token and make a fresh pairing code. Returns the code."""
    code = f"{pysecrets.randbelow(10**6):06d}"
    store({"token": token.strip(), "chat_id": None, "pair_code": code, "offset": 0})
    return code


def connected() -> bool:
    d = load()
    return bool(d.get("token") and d.get("chat_id"))


def call(method: str, params: dict, timeout: int = 20) -> dict:
    d = load()
    if not d.get("token"):
        raise TelegramError("No Telegram bot token saved.")
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(API.format(token=d["token"], method=method), data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.load(resp)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
        # Never put the URL in the error: it contains the token.
        raise TelegramError(f"Telegram request failed ({type(e).__name__}).") from None
    if not body.get("ok"):
        raise TelegramError(f"Telegram said no: {body.get('description', '')[:120]}")
    return body["result"]


def send(text: str) -> bool:
    """Send a short message to you. Returns False if Telegram isn't connected."""
    d = load()
    if not (d.get("token") and d.get("chat_id")):
        return False
    call("sendMessage", {"chat_id": d["chat_id"], "text": text[:1500],
                         "disable_web_page_preview": "true"})
    return True
