"""Job sites: Remotive, Remote OK, Hacker News "Who is hiring", Adzuna India.

Remotive and Remote OK ask that we show their name and link to their page,
and check them only a few times a day. The daily run does exactly that.
"""

import json
import re
import time
from urllib.parse import urlencode

from .. import parse
from . import RawJob
from .http import fetch_json


def remotive() -> list[RawJob]:
    data = fetch_json("https://remotive.com/api/remote-jobs?category=software-dev")
    jobs = []
    for j in data.get("jobs", []):
        location = j.get("candidate_required_location") or ""
        jobs.append(RawJob(
            source="remotive", source_id=str(j["id"]), source_name="Remotive",
            company=j.get("company_name", "").strip(), title=j.get("title", "").strip(),
            url=j.get("url", ""), location=f"Remote ({location})" if location else "Remote",
            remote=True, description=parse.html_to_text(j.get("description")),
            posted_at=parse.iso_to_ts(j.get("publication_date")),
            job_type=parse.job_type_from(j.get("title", ""), j.get("job_type", "")),
            salary_text=j.get("salary") or "",
        ))
    return jobs


def remoteok() -> list[RawJob]:
    data = fetch_json("https://remoteok.com/api")
    jobs = []
    for j in data[1:] if isinstance(data, list) else []:
        if not j.get("id"):
            continue
        salary_text = ""
        if j.get("salary_min"):
            salary_text = f"${j['salary_min']} - ${j.get('salary_max') or j['salary_min']} per year"
        location = j.get("location") or ""
        jobs.append(RawJob(
            source="remoteok", source_id=str(j["id"]), source_name="Remote OK",
            company=(j.get("company") or "").strip(), title=(j.get("position") or "").strip(),
            url=j.get("url", ""), apply_url=j.get("apply_url"),
            location=f"Remote ({location})" if location else "Remote", remote=True,
            description=parse.html_to_text(j.get("description")),
            posted_at=float(j["epoch"]) if j.get("epoch") else None,
            job_type=parse.job_type_from(j.get("position", "")), salary_text=salary_text,
        ))
    return jobs


def hn_who_is_hiring() -> list[RawJob]:
    """This month's Hacker News 'Who is hiring?' thread. Each top comment is one job post."""
    search = fetch_json(
        "https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring"
        "&query=who%20is%20hiring&hitsPerPage=5"
    )
    story = next((h for h in search.get("hits", []) if "who is hiring" in h.get("title", "").lower()), None)
    if not story:
        return []
    thread = fetch_json(f"https://hn.algolia.com/api/v1/items/{story['objectID']}", timeout=60)
    jobs = []
    for c in thread.get("children", []):
        text = parse.html_to_text(c.get("text"))
        if not text:
            continue
        first_line = text.splitlines()[0][:200]
        parts = [p.strip() for p in first_line.split("|")]
        if len(parts) < 2:
            continue
        jobs.append(RawJob(
            source="hn", source_id=str(c["id"]), source_name="Hacker News",
            company=parts[0][:80], title=" | ".join(parts[1:3])[:150],
            url=f"https://news.ycombinator.com/item?id={c['id']}",
            location=first_line, remote=parse.is_open_remote(first_line),
            description=text[:8000], posted_at=c.get("created_at_i") or time.time(),
            job_type=parse.job_type_from(first_line), salary_text=first_line,
        ))
    return jobs


def adzuna_india(keys: dict, max_pages: int = 4) -> list[RawJob]:
    """Adzuna collects jobs from many Indian sites. Needs a free key in secrets/adzuna.json."""
    jobs = []
    for page in range(1, max_pages + 1):
        query = urlencode({
            "app_id": keys["app_id"], "app_key": keys["app_key"],
            "what_or": "software developer backend sde engineer python java node",
            "max_days_old": 7, "results_per_page": 50, "sort_by": "date",
            "content-type": "application/json",
        })
        data = fetch_json(f"https://api.adzuna.com/v1/api/jobs/in/search/{page}?{query}")
        results = data.get("results", [])
        for j in results:
            salary_text = ""
            if j.get("salary_min") and j.get("salary_is_predicted") in (0, "0"):
                salary_text = f"₹{int(j['salary_min'])} - ₹{int(j.get('salary_max') or j['salary_min'])} per year"
            jobs.append(RawJob(
                source="adzuna", source_id=str(j["id"]), source_name="Adzuna",
                company=((j.get("company") or {}).get("display_name") or "").strip(),
                title=re.sub(r"<[^>]+>", "", j.get("title", "")).strip(),
                url=j.get("redirect_url", ""),
                location=(j.get("location") or {}).get("display_name", "") + ", India",
                description=parse.html_to_text(j.get("description")),
                posted_at=parse.iso_to_ts(j.get("created")),
                job_type=parse.job_type_from(j.get("title", ""), " ".join(
                    filter(None, [j.get("contract_time"), j.get("contract_type")]))),
                salary_text=salary_text,
            ))
        if len(results) < 50:
            break
    return jobs


def load_adzuna_keys(secrets_dir) -> dict | None:
    path = secrets_dir / "adzuna.json"
    if not path.exists():
        return None
    keys = json.loads(path.read_text())
    return keys if keys.get("app_id") and keys.get("app_key") else None
