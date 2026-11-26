"""Gmail: read job-alert emails (IMAP) and, later, send your approved emails (SMTP).

It logs in with a Gmail "app password" (Google Account > Security > 2-Step
Verification > App passwords). That is a 16-letter password just for this
website; you can delete it in your Google account any time and this stops working.
It is saved in secrets/gmail.json, readable only by your Linux user, never shown again.

Only Python's own mail libraries are used, over encrypted connections.
"""

import email
import email.message
import email.policy
import email.utils
import imaplib
import json
import re
import smtplib
import ssl
from datetime import datetime, timedelta

from .config import SECRETS_DIR, write_private_file

FILE = SECRETS_DIR / "gmail.json"
IMAP_HOST, SMTP_HOST = "imap.gmail.com", "smtp.gmail.com"
TIMEOUT = 30
MAX_MESSAGE_BYTES = 2 * 1024 * 1024


class GmailError(Exception):
    pass


def load() -> dict:
    try:
        return json.loads(FILE.read_text())
    except (OSError, ValueError):
        return {}


def connected() -> bool:
    d = load()
    return bool(d.get("email") and d.get("app_password"))


def clean_app_password(raw: str) -> str | None:
    pw = re.sub(r"\s+", "", raw or "")
    return pw.lower() if re.fullmatch(r"[A-Za-z]{16}", pw) else None


def _imap(address: str, password: str) -> imaplib.IMAP4_SSL:
    try:
        box = imaplib.IMAP4_SSL(IMAP_HOST, ssl_context=ssl.create_default_context(), timeout=TIMEOUT)
        box.login(address, password)
        return box
    except imaplib.IMAP4.error as e:
        raise GmailError("Gmail said no to that email and app password.") from e
    except OSError as e:
        raise GmailError(f"Couldn't reach Gmail: {e}") from e


def connect(address: str, raw_password: str) -> None:
    """Check the app password works, then save it."""
    pw = clean_app_password(raw_password)
    if not pw:
        raise GmailError("An app password is 16 letters, like 'abcd efgh ijkl mnop'.")
    box = _imap(address, pw)
    box.logout()
    write_private_file(FILE, json.dumps({"email": address, "app_password": pw}).encode())


def disconnect() -> None:
    FILE.unlink(missing_ok=True)


def _quote(s: str) -> str:
    return '"' + re.sub(r'["\\\r\n]', "", s) + '"'


def recent_messages(senders: list[str], days: int, skip_ids: set[str], limit: int) -> list:
    """Emails from these senders in the last few days, newest first, as email.message objects.
    Read only: messages are opened with PEEK, so they stay unread in Gmail."""
    d = load()
    if not d:
        raise GmailError("Gmail isn't connected.")
    box = _imap(d["email"], d["app_password"])
    try:
        box.select("INBOX", readonly=True)
        since = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
        ids: set[bytes] = set()
        for sender in senders:
            sender = sender.strip()
            if not sender:
                continue
            typ, data = box.search(None, "SINCE", since, "FROM", _quote(sender))
            if typ == "OK" and data and data[0]:
                ids.update(data[0].split())
        out = []
        for num in sorted(ids, key=int, reverse=True):
            if len(out) >= limit:
                break
            typ, head = box.fetch(num, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)] RFC822.SIZE)")
            if typ != "OK" or not head or not isinstance(head[0], tuple):
                continue
            size = re.search(rb"RFC822\.SIZE (\d+)", head[0][0])
            msg_id = email.message_from_bytes(head[0][1]).get("Message-ID", "").strip()
            if not msg_id or msg_id in skip_ids or (size and int(size.group(1)) > MAX_MESSAGE_BYTES):
                continue
            typ, body = box.fetch(num, "(BODY.PEEK[])")
            if typ == "OK" and body and isinstance(body[0], tuple):
                out.append(email.message_from_bytes(body[0][1], policy=email.policy.default))
        return out
    finally:
        try:
            box.logout()
        except Exception:
            pass


def send(to: str, subject: str, body: str, attachment: tuple[str, bytes] | None = None,
         in_reply_to: str | None = None) -> str:
    """Send one plain-text email. Returns its Message-ID. Callers check the cap, approvals and the Stop button first."""
    d = load()
    if not d:
        raise GmailError("Gmail isn't connected.")
    msg = email.message.EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = d["email"], to, subject
    msg["Message-ID"] = email.utils.make_msgid(domain=d["email"].split("@")[-1])
    if in_reply_to:
        msg["In-Reply-To"] = msg["References"] = in_reply_to
    msg.set_content(body)
    if attachment:
        name, data = attachment
        msg.add_attachment(data, maintype="application", subtype="pdf", filename=name)
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, 465, context=ssl.create_default_context(), timeout=TIMEOUT) as s:
            s.login(d["email"], d["app_password"])
            s.send_message(msg)
    except smtplib.SMTPException as e:
        raise GmailError(f"Gmail didn't send it: {e}") from e
    except OSError as e:
        raise GmailError(f"Couldn't reach Gmail: {e}") from e
    return msg["Message-ID"]
