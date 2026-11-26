"""Your saved answers to application-form questions.

Write an answer once, and the form filler reuses it whenever a form asks
the same thing in other words. {company} and {role} in an answer are
replaced with the job's company and title.
"""

import re
import time
from difflib import SequenceMatcher

QUESTION_MAX = 300
ANSWER_MAX = 3000

# Shown once, with empty answers, so you can fill the common ones.
COMMON = [
    "Why do you want to work at {company}?",
    "Tell us about yourself.",
    "Why are you a good fit for this role?",
    "Describe a project you are proud of.",
    "What is your notice period?",
    "What is your current CTC?",
    "What is your expected CTC?",
    "Are you willing to relocate?",
    "Are you legally allowed to work in India?",
    "Will you now or in the future need visa sponsorship?",
    "How did you hear about this job?",
    "Are you open to working from the office?",
    "How many years of experience do you have?",
    "What is your gender?",
    "Do you have any disability?",
]

_STOP = {"a", "an", "the", "to", "of", "in", "on", "for", "and", "or", "you", "your", "are", "is", "do",
         "does", "what", "please", "with", "at", "this", "that", "have", "has", "be", "we", "our", "us",
         "will", "would", "can", "if", "any", "it", "as", "by", "about", "tell"}


def seed(conn) -> None:
    """Add the common questions the first time the page is opened."""
    if conn.execute("SELECT COUNT(*) FROM answers").fetchone()[0]:
        return
    now = time.time()
    conn.executemany("INSERT INTO answers (question, answer, updated_at) VALUES (?, '', ?)",
                     [(q, now) for q in COMMON])
    conn.commit()


def all_answers(conn) -> list:
    return conn.execute("SELECT * FROM answers ORDER BY (answer = ''), used DESC, id").fetchall()


def save(conn, answer_id: int | None, question: str, answer: str) -> str | None:
    """Returns an error message, or None when saved."""
    question, answer = question.strip(), answer.strip()
    if not question:
        return "Write the question."
    if len(question) > QUESTION_MAX or len(answer) > ANSWER_MAX:
        return f"Keep the question under {QUESTION_MAX} and the answer under {ANSWER_MAX} characters."
    if answer_id is None:
        conn.execute("INSERT INTO answers (question, answer, updated_at) VALUES (?, ?, ?)",
                     (question, answer, time.time()))
    else:
        conn.execute("UPDATE answers SET question = ?, answer = ?, updated_at = ? WHERE id = ?",
                     (question, answer, time.time(), answer_id))
    conn.commit()
    return None


def delete(conn, answer_id: int) -> None:
    conn.execute("DELETE FROM answers WHERE id = ?", (answer_id,))
    conn.commit()


def _words(text: str) -> list[str]:
    text = re.sub(r"\{(company|role)\}", " ", text.lower())
    words = [w for w in re.findall(r"[a-z0-9]+", text) if w not in _STOP]
    return [w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words]   # "years" = "year"


def similarity(a: str, b: str) -> float:
    """0..1: how alike two questions are, by their main words (small words like 'the' are ignored)."""
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return 0.0
    sa, sb = set(wa), set(wb)
    overlap = len(sa & sb) / len(sa | sb)
    order = SequenceMatcher(None, wa, wb).ratio()   # same words in the same order
    return max(overlap, order)


def fill(answer: str, company: str, role: str) -> str:
    return answer.replace("{company}", company).replace("{role}", role)


def best(question: str, rows, min_score: float) -> tuple[object, float] | tuple[None, float]:
    """The saved answer that best fits a form question, if it's close enough."""
    top, top_score = None, 0.0
    for r in rows:
        if not r["answer"]:
            continue
        sc = similarity(question, r["question"])
        if sc > top_score:
            top, top_score = r, sc
    return (top, top_score) if top_score >= min_score else (None, top_score)


def mark_used(conn, answer_id: int) -> None:
    conn.execute("UPDATE answers SET used = used + 1 WHERE id = ?", (answer_id,))
    conn.commit()
