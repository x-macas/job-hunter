"""Collect jobs from all sources, save them, and run your rules on them."""

import re
import time
from concurrent.futures import ThreadPoolExecutor

from . import parse, rules
from .config import SECRETS_DIR
from .sources import RawJob, boards, job_sites, portals
from .sources.http import FetchError

ACTIVE_DAYS = 3          # a board job not seen for this long is treated as closed
DESCRIPTION_FETCHES = 200  # most job pages to download per run
WORKERS = 6


def all_boards(s: dict) -> list[dict]:
    return boards.default_boards() + boards.boards_from_setting(s["extra_boards"])


def wanted(src: dict, s: dict) -> bool:
    """Read this source in this run?
    - 'polite' sites (robots.txt asks bots to stay away, but they don't block): only if you allow it
      in Settings, and only every few hours, so we stay gentle.
    - sitemaps are whole job lists: every few hours too."""
    if src.get("polite"):
        if not s["read_no_bots_sites"]:
            return False
        hours = max(s["no_bots_hours"], s["sitemap_hours"] if src.get("type") == "sitemap" else 0)
        return portals.due(src, hours)
    if src.get("type") == "sitemap":
        return portals.due(src, s["sitemap_hours"])
    return True


def fetch_all(s: dict, log=print, only_boards: list[dict] | None = None, quick: bool = False) -> list[RawJob]:
    """Download jobs from every source. A source that fails is skipped, not fatal.

    quick=True is the 30-minute check: company boards and careers sites only
    (newest jobs), no general job sites, which ask for only a few calls a day."""
    tasks = [(f"{b['name']} ({b['ats']})", boards.fetch_board, (b,))
             for b in (only_boards if only_boards is not None else all_boards(s)) if wanted(b, s)]
    if only_boards is None:
        tasks += [(f"{p['name']} careers", portals.fetch_portal, ({**p, "pages_per_run": s["job_pages_per_run"]}, quick)) for p in portals.default_portals()
                  if wanted(p, s)]
    if only_boards is None and not quick:
        tasks += [("Remotive", job_sites.remotive, ()), ("Remote OK", job_sites.remoteok, ()),
                  ("Hacker News", job_sites.hn_who_is_hiring, ())]
        keys = job_sites.load_adzuna_keys(SECRETS_DIR)
        if keys:
            tasks.append(("Adzuna India", job_sites.adzuna_india, (keys,)))

    def run(task):
        name, fn, args = task
        try:
            found = fn(*args)
            if args and isinstance(args[0], dict) and args[0].get("polite"):
                portals.remember_read(args[0])
            return name, found, None
        except Exception as e:  # one broken source must never stop the run
            return name, [], str(e)[:200]

    jobs, failed = [], []
    with ThreadPoolExecutor(WORKERS) as pool:
        for name, found, error in pool.map(run, tasks):
            if error:
                failed.append(name)
            jobs.extend(found)
    log(f"Collected {len(jobs)} jobs from {len(tasks) - len(failed)} of {len(tasks)} sources."
        + (f" Failed: {', '.join(failed[:10])}" if failed else ""))
    return jobs


def _derived(raw_desc: str | None, salary_text: str, usd: float) -> dict:
    low, high = parse.salary_lpa(salary_text, usd)
    if high is None and raw_desc:
        low, high = parse.salary_lpa(_pay_lines(raw_desc), usd)
    return {"min_years": parse.min_years(raw_desc) if raw_desc else None,
            "salary_min_lpa": low, "salary_max_lpa": high}


def save(conn, jobs: list[RawJob], s: dict, added_by: str = "auto", now: float | None = None) -> list[int]:
    """Insert new jobs, refresh 'last seen' on known ones. Returns ids of NEW jobs."""
    now = now or time.time()
    new_ids = []
    for j in jobs:
        # Links come from the internet: keep only real web links (no "javascript:" tricks).
        if not _is_web_link(j.url) and j.url != "about:blank":
            continue
        if j.apply_url and not (_is_web_link(j.apply_url) or _is_apply_email(j.apply_url)):
            j.apply_url = None
        if not (j.title and j.company):
            continue
        row = conn.execute("SELECT id FROM jobs WHERE source = ? AND source_id = ?",
                           (j.source, j.source_id)).fetchone()
        if row:
            conn.execute("UPDATE jobs SET last_seen = ? WHERE id = ?", (now, row["id"]))
            continue
        d = _derived(j.description, j.salary_text, s["usd_to_inr"])
        cur = conn.execute(
            """INSERT INTO jobs (source, source_id, source_name, company, title, location, remote, url,
                   apply_url, description, posted_at, first_seen, last_seen, job_type, salary_text,
                   salary_min_lpa, salary_max_lpa, min_years, added_by)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (j.source, j.source_id, j.source_name, j.company[:120], j.title[:200], j.location[:300],
             int(j.remote), j.url, j.apply_url, j.description, j.posted_at, now, now, j.job_type,
             j.salary_text[:200], d["salary_min_lpa"], d["salary_max_lpa"], d["min_years"], added_by),
        )
        new_ids.append(cur.lastrowid)
    conn.commit()
    return new_ids


def apply_rules(conn, s: dict, log=print, now: float | None = None) -> int:
    """Run the rules on all open jobs. Downloads job pages only for jobs that pass.

    Rules are cheap, so they run again every time. Change a setting and the
    next run uses it straight away.
    """
    now = now or time.time()
    cutoff = now - ACTIVE_DAYS * 86400
    conn.execute(
        "UPDATE jobs SET rule_ok = 0, rule_reason = 'closed' "
        "WHERE status = 'new' AND added_by != 'link' AND last_seen < ?", (cutoff,))
    rows = conn.execute(
        "SELECT * FROM jobs WHERE status = 'new' AND (added_by = 'link' OR last_seen >= ?)", (cutoff,)
    ).fetchall()

    need_text = []
    for job in rows:
        ok, reason = rules.evaluate(job, s, now)
        if job["added_by"] == "link":
            # You added it yourself: always show it, the rule result is only a warning.
            conn.execute("UPDATE jobs SET rule_ok = 1, rule_reason = ? WHERE id = ?", (reason, job["id"]))
            continue
        if ok and job["description"] is None and job["source"] in ("greenhouse", "smartrecruiters", "workday", "rippling"):
            need_text.append(job)
            continue
        conn.execute("UPDATE jobs SET rule_ok = ?, rule_reason = ? WHERE id = ?", (int(ok), reason, job["id"]))
    conn.commit()

    fetched = _fill_descriptions(conn, need_text[:DESCRIPTION_FETCHES], s)
    for job_id in fetched:
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        ok, reason = rules.evaluate(job, s, now)
        conn.execute("UPDATE jobs SET rule_ok = ?, rule_reason = ? WHERE id = ?", (int(ok), reason, job_id))
    conn.commit()
    kept = conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'new' AND rule_ok = 1").fetchone()[0]
    log(f"Rules: {kept} jobs passed out of {len(rows)} open jobs.")
    return kept


def _fill_descriptions(conn, jobs, s) -> list[int]:
    def get(job):
        slug, _, ext_id = job["source_id"].partition(":")
        try:
            if job["source"] == "workday":
                return job["id"], portals.workday_description(job["source_id"])
            fetch = {"greenhouse": boards.greenhouse_description, "rippling": boards.rippling_description}.get(
                job["source"], boards.smartrecruiters_description)
            return job["id"], fetch(slug, ext_id)
        except FetchError:
            return job["id"], None

    done = []
    with ThreadPoolExecutor(4) as pool:
        for job_id, text in pool.map(get, jobs):
            if text is None:
                continue
            row = conn.execute("SELECT salary_text FROM jobs WHERE id = ?", (job_id,)).fetchone()
            d = _derived(text, row["salary_text"], s["usd_to_inr"])
            conn.execute(
                "UPDATE jobs SET description = ?, min_years = ?, salary_min_lpa = ?, salary_max_lpa = ? "
                "WHERE id = ?", (text, d["min_years"], d["salary_min_lpa"], d["salary_max_lpa"], job_id))
            done.append(job_id)
    conn.commit()
    return done


def _pay_lines(text: str) -> str:
    """Only the lines that talk about pay, so '$10M funding' isn't read as a salary."""
    keys = ("salary", "compensation", "base pay", "pay range", "ctc", "lpa", "per annum", "stipend")
    return "\n".join(line for line in text.splitlines() if any(k in line.lower() for k in keys))


def _is_web_link(url: str | None) -> bool:
    return bool(url) and url.lower().startswith(("https://", "http://")) and len(url) < 2000


def _is_apply_email(url: str) -> bool:
    """A job's official apply email (mailto: link from the job board's own apply field)."""
    return bool(re.fullmatch(r"mailto:[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}(\?[^\s]*)?", url or ""))
