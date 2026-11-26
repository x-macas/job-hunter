"""Turns scores into today's list of top jobs."""

import json
import time
from datetime import date

from . import rules

FRESH_DAYS = 2
FRESH_BOOST = 5
UNKNOWN_PAY_PENALTY = 10


def final_score(job, ai: dict, s: dict, now: float) -> tuple[float | None, list[str]]:
    """Returns (score, tags), or (None, [reason]) if the job should not be shown."""
    if job["added_by"] == "link":
        note = [f"Note: {job['rule_reason']}"] if job["rule_reason"] else []
        return float(job["ai_score"]) + 1000, ["Added by you"] + note
    if job["ai_score"] < s["min_score"]:
        return None, ["score too low"]
    if not ai.get("is_dev_role", True):
        return None, ["not a dev role"]
    if ai.get("has_bond") or ai.get("pay_to_join"):
        return None, ["bond or fee"]
    needs = ai.get("needs_years")
    if needs is not None and needs > s["max_experience_years"]:
        return None, [f"needs {needs:g}+ years"]

    score = float(job["ai_score"])
    tags = []
    big = rules.name_in(job["company"], s["big_companies"])
    ctype = ai.get("company_type", "unknown")
    if big:
        score += s["big_company_boost"]
        tags.append("Big company")

    pay = job["salary_max_lpa"]
    if not big and ctype in ("startup", "service", "unknown"):
        if pay is not None and job["job_type"] in ("full_time", "unknown") and pay < s["min_lpa_startup_service"]:
            return None, [f"{ctype} paying under {s['min_lpa_startup_service']} LPA"]
        if pay is None:
            score -= UNKNOWN_PAY_PENALTY
            tags.append("Pay unknown")

    if job["posted_at"] and now - job["posted_at"] < FRESH_DAYS * 86400:
        score += FRESH_BOOST
        tags.append("NEW")
    if job["added_by"] == "watch":
        tags.append("Watch list")
    return score, tags


def pick_today(conn, s: dict, now: float | None = None) -> int:
    """Choose today's top jobs, keeping spots for big companies. Returns how many."""
    now = now or time.time()
    today = date.today().isoformat()
    shown_before = {r[0] for r in conn.execute("SELECT job_id FROM picks WHERE day < ?", (today,))}
    rows = conn.execute(
        "SELECT * FROM jobs WHERE rule_ok = 1 AND ai_score IS NOT NULL AND status = 'new'"
    ).fetchall()

    ranked = []
    for job in rows:
        if job["id"] in shown_before:
            continue
        final, _tags = final_score(job, json.loads(job["ai_json"] or "{}"), s, now)
        if final is not None:
            big = rules.name_in(job["company"], s["big_companies"])
            ranked.append((final, big, job["id"]))
    ranked.sort(reverse=True)

    n, big_spots = s["daily_top_n"], min(s["big_company_spots"], s["daily_top_n"])
    bigs = [r for r in ranked if r[1]][:big_spots]
    chosen_ids = {r[2] for r in bigs}
    rest = [r for r in ranked if r[2] not in chosen_ids][: n - len(bigs)]
    chosen = sorted(bigs + rest, reverse=True)

    conn.execute("DELETE FROM picks WHERE day = ?", (today,))
    conn.executemany(
        "INSERT INTO picks (day, job_id, rank, final) VALUES (?, ?, ?, ?)",
        [(today, job_id, i + 1, final) for i, (final, _big, job_id) in enumerate(chosen)],
    )
    conn.commit()
    return len(chosen)
