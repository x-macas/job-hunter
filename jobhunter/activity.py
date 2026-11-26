"""The activity log: a record of everything the website does."""

import time


def log(conn, kind: str, message: str) -> None:
    conn.execute(
        "INSERT INTO activity (ts, kind, message) VALUES (?, ?, ?)",
        (time.time(), kind, message[:500]),
    )
    conn.commit()


def recent(conn, limit: int = 200):
    return conn.execute(
        "SELECT ts, kind, message FROM activity ORDER BY ts DESC, id DESC LIMIT ?",
        (limit,),
    ).fetchall()
