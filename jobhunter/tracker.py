"""The Tracker board: every job you applied to, and where it stands."""

import time

STAGES = [
    ("applied", "Applied"),
    ("emailed", "Emailed"),
    ("replied", "Replied"),
    ("interview", "Interview"),
    ("offer", "Offer"),
    ("rejected", "Rejected"),
]
STAGE_NAMES = dict(STAGES)
# The board has 5 columns: offers and rejections share the last one.
COLUMNS = [("applied", "Applied"), ("emailed", "Emailed"), ("replied", "Replied"),
           ("interview", "Interview"), ("done", "Offer / Rejected")]
NOTE_MAX = 1000


def mark_applied(conn, job_id: int, now: float | None = None) -> bool:
    """Returns False if it was already on the board."""
    now = now or time.time()
    cur = conn.execute("INSERT OR IGNORE INTO applications (job_id, stage, applied_at, updated_at) "
                       "VALUES (?, 'applied', ?, ?)", (job_id, now, now))
    conn.execute("UPDATE jobs SET status = 'applied' WHERE id = ?", (job_id,))
    conn.commit()
    return cur.rowcount == 1


def undo_applied(conn, job_id: int) -> None:
    conn.execute("DELETE FROM applications WHERE job_id = ?", (job_id,))
    conn.execute("UPDATE jobs SET status = 'new' WHERE id = ? AND status = 'applied'", (job_id,))
    conn.commit()


def move(conn, job_id: int, stage: str) -> bool:
    if stage not in STAGE_NAMES:
        return False
    cur = conn.execute("UPDATE applications SET stage = ?, updated_at = ? WHERE job_id = ?",
                       (stage, time.time(), job_id))
    conn.commit()
    return cur.rowcount == 1


def set_note(conn, job_id: int, note: str) -> None:
    conn.execute("UPDATE applications SET note = ?, updated_at = ? WHERE job_id = ?",
                 (note.strip()[:NOTE_MAX], time.time(), job_id))
    conn.commit()


def board(conn, s: dict, now: float | None = None) -> list[dict]:
    """The columns, each with its cards (newest change first)."""
    now = now or time.time()
    rows = conn.execute(
        "SELECT a.*, j.title, j.company, j.url, j.apply_url, j.location FROM applications a "
        "JOIN jobs j ON j.id = a.job_id ORDER BY a.updated_at DESC").fetchall()
    cols = {key: {"key": key, "name": name, "cards": []} for key, name in COLUMNS}
    for r in rows:
        days = int((now - r["applied_at"]) // 86400)
        waiting = r["stage"] in ("applied", "emailed") and (now - r["updated_at"]) >= s["followup_1_days"] * 86400
        col = "done" if r["stage"] in ("offer", "rejected") else r["stage"]
        cols[col]["cards"].append({"row": r, "days": days, "waiting": waiting,
                                   "stage_name": STAGE_NAMES[r["stage"]]})
    return list(cols.values())


def applied_today(conn, now: float | None = None) -> int:
    t = time.localtime(now or time.time())
    start = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
    return conn.execute("SELECT COUNT(*) FROM applications WHERE applied_at >= ?", (start,)).fetchone()[0]
