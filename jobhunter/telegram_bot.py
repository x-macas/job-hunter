"""The Telegram bot: python -m jobhunter.telegram_bot

What you can send it (only from YOUR Telegram account):
  a job link or job text  -> added to today's list and scored
  /today                  -> today's top 5 jobs
  /stop                   -> emergency stop (pauses everything)
  /resume                 -> turn the stop off
  /help                   -> this list

Pairing: on the Settings page you get a 6-digit code. Send it to the bot.
The first chat that sends the right code becomes the only chat it listens to.
"""

import re
import time
from datetime import date

from . import activity, linkadd, settings_store, telegram
from .config import DATA_DIR, DB_PATH, make_private_dirs
from .db import connect, init_db

URL = re.compile(r"https?://\S+")
HELP = ("Send me a job link (or paste the job text) and I'll add it to today's list.\n"
        "/today - today's top jobs\n/stop - stop everything\n/resume - start again")


def handle(conn, chat_id: int, text: str) -> str | None:
    data = telegram.load()
    owner = data.get("chat_id")

    if owner is None:
        if data.get("pair_code") and text.strip() == data["pair_code"]:
            data["chat_id"], data["pair_code"] = chat_id, None
            telegram.store(data)
            activity.log(conn, "security", "Telegram connected to your chat.")
            return "Connected. I'll only listen to you from now on.\n\n" + HELP
        if data.get("pair_code"):
            data["wrong_codes"] = data.get("wrong_codes", 0) + 1
            if data["wrong_codes"] >= 10:  # someone guessing: kill the code
                data["pair_code"] = None
                activity.log(conn, "security", "Telegram pairing code cancelled after 10 wrong tries.")
            telegram.store(data)
        return None  # stay silent to strangers
    if chat_id != owner:
        return None  # rule 8: ignore everyone else

    t = text.strip()
    if t in ("/start", "/help"):
        return HELP
    if t == "/stop":
        settings_store.put(conn, settings_store.PAUSED_KEY, True)
        conn.commit()
        activity.log(conn, "security", "EMERGENCY STOP from Telegram.")
        return "Stopped. Nothing will run or be sent until you send /resume."
    if t == "/resume":
        settings_store.put(conn, settings_store.PAUSED_KEY, False)
        conn.commit()
        activity.log(conn, "security", "Resumed from Telegram.")
        return "Running again."
    if t == "/today":
        rows = conn.execute(
            "SELECT j.title, j.company, j.ai_score FROM picks p JOIN jobs j ON j.id = p.job_id "
            "WHERE p.day = ? AND j.status = 'new' ORDER BY p.rank LIMIT 5", (date.today().isoformat(),)).fetchall()
        if not rows:
            return "No list yet today."
        return "Today's top jobs:\n" + "\n".join(f"- {r['title']} at {r['company']} ({r['ai_score']})" for r in rows)

    if settings_store.get(conn, settings_store.PAUSED_KEY):
        return "Everything is stopped. Send /resume first."
    s = settings_store.get_all(conn)
    try:
        m = URL.search(t)
        job = linkadd.from_url(m.group(0), s["ai_model"]) if m else linkadd.from_text(t, s["ai_model"])
        job_id = linkadd.add(conn, job, s)
    except linkadd.AddError as e:
        return str(e)
    row = conn.execute("SELECT title, company, ai_score, rule_reason FROM jobs WHERE id = ?", (job_id,)).fetchone()
    activity.log(conn, "job", f"Added from Telegram: {row['title']} at {row['company']}.")
    note = f"\nNote: {row['rule_reason']}" if row["rule_reason"] else ""
    score = row["ai_score"] if row["ai_score"] is not None else "not scored yet"
    return f"Added: {row['title']} at {row['company']}\nScore: {score}{note}"


def main() -> None:
    make_private_dirs()
    init_db(DB_PATH, DATA_DIR)
    print("Telegram bot running.", flush=True)
    while True:
        data = telegram.load()
        if not data.get("token"):
            time.sleep(30)  # waiting for you to add a token on the Settings page
            continue
        try:
            updates = telegram.call("getUpdates", {"offset": data.get("offset", 0), "timeout": 50}, timeout=60)
        except telegram.TelegramError as e:
            print(e, flush=True)
            time.sleep(15)
            continue
        conn = connect(DB_PATH)
        try:
            for u in updates:
                data = telegram.load()
                data["offset"] = u["update_id"] + 1
                telegram.store(data)
                msg = u.get("message") or {}
                text, chat = msg.get("text"), (msg.get("chat") or {}).get("id")
                if not text or chat is None:
                    continue
                reply = handle(conn, chat, text)
                if reply:
                    telegram.call("sendMessage", {"chat_id": chat, "text": reply[:3000],
                                                  "disable_web_page_preview": "true"})
        except telegram.TelegramError as e:
            print(e, flush=True)
        finally:
            conn.close()


if __name__ == "__main__":
    main()
