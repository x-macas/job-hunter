"""Your details, filled once on the Profile page and reused in every form."""

import json
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str = "text"  # "text", "email", "url" or "long"
    help: str = ""


FIELDS = [
    Field("full_name", "Full name"),
    Field("email", "Email", "email"),
    Field("phone", "Phone"),
    Field("city", "City you live in"),
    Field("country", "Country you live in", help="For example: India"),
    Field("current_company", "Current company", help="Leave empty if you're not working now"),
    Field("linkedin_url", "LinkedIn link", "url"),
    Field("github_url", "GitHub link", "url"),
    Field("portfolio_url", "Portfolio or project link", "url"),
    Field("college", "College"),
    Field("degree", "Degree and branch"),
    Field("grad_year", "Graduation year"),
    Field("cgpa", "CGPA"),
    Field("class12", "Class 12 school, board, year and %"),
    Field("class10", "Class 10 school, board, year and %"),
    Field("notice_period", "Notice period", help="For example: Immediate"),
    Field("current_ctc", "Current pay (CTC)"),
    Field("expected_ctc", "Expected pay (CTC)"),
    Field("relocate", "Ready to relocate?", help="For example: Yes, anywhere in India"),
    Field("about", "A few lines about you", "long",
          "Plain words. Used as a base for emails and form answers."),
]

BY_KEY = {f.key: f for f in FIELDS}


def get_all(conn) -> dict:
    rows = conn.execute("SELECT key, value FROM profile").fetchall()
    stored = {r["key"]: json.loads(r["value"]) for r in rows}
    return {f.key: stored.get(f.key, "") for f in FIELDS} | {
        k: v for k, v in stored.items() if k not in BY_KEY
    }


def put(conn, key: str, value) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO profile (key, value, updated_at) VALUES (?, ?, ?)",
        (key, json.dumps(value), time.time()),
    )


def parse_form(form) -> tuple[dict, dict]:
    values, errors = {}, {}
    for f in FIELDS:
        raw = form.get(f.key, "").strip()
        limit = 2000 if f.kind == "long" else 200
        if len(raw) > limit:
            errors[f.key] = f"Keep it under {limit} characters."
            continue
        if raw and f.kind == "url" and not raw.startswith("https://"):
            errors[f.key] = "Link must start with https://"
            continue
        if raw and f.kind == "email" and ("@" not in raw or " " in raw):
            errors[f.key] = "That doesn't look like an email address."
            continue
        values[f.key] = raw
    return values, errors


def facts_block(prof: dict) -> str:
    """Up-to-date facts for every writing prompt. They beat the resume, which may be older."""
    from datetime import date
    return (f"Today's date: {date.today():%d %B %Y}\n"
            f"Graduation year: {prof.get('grad_year') or 'not given'}\n"
            f"Current company: {prof.get('current_company') or 'none (not working right now, open to join)'}\n"
            f"Notice period: {prof.get('notice_period') or 'not given'}\n"
            "If the resume says something different (like 'present' for an old job, or 'student' after the\n"
            "graduation year), trust these facts, not the resume.")
