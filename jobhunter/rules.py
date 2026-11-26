"""Your rules. Every job goes through these before Claude ever sees it.

evaluate() returns (True, "") to keep a job, or (False, "why") to drop it.
It's plain code: no internet, no AI, so it is instant and free.
"""

import re
import time

from . import parse

LEVEL_2_PLUS = re.compile(
    r"\b(?:sde|swe|engineer|developer|eng|mts)[\s-]*(?:ii|iii|iv|v|[2-9])\b|\b(?:ii|iii|iv)\b|\bl[4-9]\b",
    re.I,
)
BOND_OR_FEE = re.compile(
    r"service (?:agreement|bond)|\bbond (?:of|period)\b|\bsign(?:ing)? a bond\b|"
    r"training fee|registration fee|security deposit|refundable deposit|pay (?:a|the) fee|"
    r"\bunpaid\b",
    re.I,
)


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def name_in(company: str, names: list[str]) -> bool:
    """Does this company match a name on the list? 'Google India' matches 'Google'."""
    c = norm(company)
    if not c:
        return False
    for n in names:
        n2 = norm(n)
        if n2 and (c == n2 or c.startswith(n2) or (len(n2) >= 5 and n2 in c)):
            return True
    return False


def _word_match(title: str, words: list[str]) -> bool:
    low = title.lower()
    for w in words:
        w = w.strip().lower()
        if w and re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", low):
            return True
    return False


def evaluate(job, s: dict, now: float | None = None) -> tuple[bool, str]:
    """job: a row (or dict) from the jobs table. s: all settings."""
    now = now or time.time()
    title, company = job["title"] or "", job["company"] or ""
    big = name_in(company, s["big_companies"])

    if name_in(company, s["blocked_companies"]):
        return False, "blocked company"
    if name_in(company, s["hidden_companies"]):
        return False, "hidden company"
    if not _word_match(title, s["roles"]):
        return False, "role not in your list"
    if _word_match(title, s["title_blocklist"]):
        return False, "title has a hidden word"
    if s["hide_level_2_plus"] and LEVEL_2_PLUS.search(title):
        return False, "level II+ title"
    if not parse.location_ok(job["location"] or "", bool(job["remote"]), s["locations"]):
        return False, "location"
    posted = job["posted_at"]
    if posted and now - posted > s["max_job_age_days"] * 86400:
        return False, "too old"

    job_type = job["job_type"]
    if s["other_companies_fulltime_only"] and not big and job_type in ("intern", "part_time", "contract"):
        return False, f"{job_type.replace('_', '-')} at a non-big company"

    years = job["min_years"]
    if years is not None and years > s["max_experience_years"]:
        return False, f"asks {years:g}+ years"

    if job["description"] and BOND_OR_FEE.search(job["description"]):
        return False, "bond, fee or unpaid"

    # Pay: only checked when the post lists a salary, and only for full-time jobs.
    high = job["salary_max_lpa"]
    if high is not None and job_type in ("full_time", "unknown") and high < s["min_lpa_fulltime"]:
        return False, f"pay {high:g} LPA is below {s['min_lpa_fulltime']}"

    return True, ""
