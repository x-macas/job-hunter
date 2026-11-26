"""Company job boards: Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Jobvite, Freshteam.
The first five have free public feeds; Jobvite and Freshteam are plain public web pages
(their robots.txt allows the job list)."""

import html
import json
import re
from pathlib import Path

from .. import parse
from . import RawJob, robots
from .http import FetchError, fetch_bytes, fetch_json

BOARDS_FILE = Path(__file__).with_name("boards.json")
ATS = ("greenhouse", "lever", "ashby", "smartrecruiters", "workable", "jobvite", "freshteam")


def default_boards() -> list[dict]:
    return json.loads(BOARDS_FILE.read_text())


def boards_from_setting(lines: list[str]) -> list[dict]:
    """Extra boards you add in Settings, one per line: 'greenhouse:slug Company Name'."""
    out = []
    for line in lines:
        head, _, name = line.strip().partition(" ")
        ats, _, slug = head.partition(":")
        if ats in ATS and slug:
            out.append({"ats": ats, "slug": slug, "name": name.strip() or slug})
    return out


def fetch_board(board: dict) -> list[RawJob]:
    ats, slug, name = board["ats"], board["slug"], board["name"]
    if ats == "greenhouse":
        return _greenhouse(slug, name)
    if ats == "lever":
        return _lever(slug, name)
    if ats == "ashby":
        return _ashby(slug, name)
    if ats == "smartrecruiters":
        return _smartrecruiters(slug, name)
    if ats == "workable":
        return _workable(slug, name)
    if ats == "jobvite":
        return _jobvite(slug, name)
    if ats == "freshteam":
        return _freshteam(slug, name, board.get("polite", False))
    return []


def _greenhouse(slug, name):
    data = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
    jobs = []
    for j in data.get("jobs", []):
        location = (j.get("location") or {}).get("name", "") or ""
        jobs.append(RawJob(
            source="greenhouse", source_id=f"{slug}:{j['id']}", source_name="Greenhouse",
            company=name, title=j.get("title", "").strip(), url=j.get("absolute_url", ""),
            location=location, remote=parse.is_open_remote(location),
            posted_at=parse.iso_to_ts(j.get("first_published") or j.get("updated_at")),
            job_type=parse.job_type_from(j.get("title", "")),
            extra={"slug": slug, "id": j["id"]},
        ))
    return jobs


def greenhouse_description(slug: str, job_id) -> str:
    data = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}")
    return parse.html_to_text(data.get("content", ""))


def _lever(slug, name):
    data = fetch_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    jobs = []
    for j in data if isinstance(data, list) else []:
        cats = j.get("categories") or {}
        location = ", ".join(cats.get("allLocations") or [cats.get("location") or ""])
        remote = (j.get("workplaceType") == "remote")
        salary = j.get("salaryRange") or {}
        salary_text = ""
        if salary.get("min"):
            salary_text = f"{salary.get('currency', '')} {salary['min']} - {salary.get('max', '')} {salary.get('interval', '')}"
        description = "\n\n".join(filter(None, [
            j.get("descriptionPlain"),
            *[f"{x.get('text', '')}\n{parse.html_to_text(x.get('content', ''))}" for x in j.get("lists") or []],
            j.get("additionalPlain"),
        ]))
        jobs.append(RawJob(
            source="lever", source_id=f"{slug}:{j['id']}", source_name="Lever",
            company=name, title=j.get("text", "").strip(), url=j.get("hostedUrl", ""),
            apply_url=j.get("applyUrl"), location=location + (" (Remote)" if remote else ""),
            remote=remote, description=description,
            posted_at=(j["createdAt"] / 1000) if j.get("createdAt") else None,
            job_type=parse.job_type_from(j.get("text", ""), cats.get("commitment", "")),
            salary_text=salary_text,
        ))
    return jobs


def _ashby(slug, name):
    data = fetch_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true")
    jobs = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        places = [j.get("location") or ""] + [
            (s.get("location") or "") for s in j.get("secondaryLocations") or []
        ]
        remote = bool(j.get("isRemote")) or j.get("workplaceType") == "Remote"
        comp = j.get("compensation") or {}
        jobs.append(RawJob(
            source="ashby", source_id=f"{slug}:{j['id']}", source_name="Ashby",
            company=name, title=j.get("title", "").strip(), url=j.get("jobUrl", ""),
            apply_url=j.get("applyUrl"),
            location=", ".join(p for p in places if p) + (" (Remote)" if remote else ""),
            remote=remote, description=j.get("descriptionPlain") or parse.html_to_text(j.get("descriptionHtml")),
            posted_at=parse.iso_to_ts(j.get("publishedAt")),
            job_type=parse.job_type_from(j.get("title", ""), j.get("employmentType") or ""),
            salary_text=comp.get("scrapeableCompensationSalarySummary")
            or comp.get("compensationTierSummary") or "",
        ))
    return jobs


def _smartrecruiters(slug, name):
    jobs, offset = [], 0
    while offset < 1000:
        data = fetch_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset={offset}")
        items = data.get("content", [])
        for j in items:
            loc = j.get("location") or {}
            location = ", ".join(x for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x)
            remote = bool(loc.get("remote"))
            kind = (j.get("typeOfEmployment") or {}).get("label", "")
            jobs.append(RawJob(
                source="smartrecruiters", source_id=f"{slug}:{j['id']}", source_name="SmartRecruiters",
                company=name, title=j.get("name", "").strip(),
                url=f"https://jobs.smartrecruiters.com/{slug}/{j['id']}",
                location=location.replace(", in", ", India") + (" (Remote)" if remote else ""), remote=remote,
                posted_at=parse.iso_to_ts(j.get("releasedDate")),
                job_type=parse.job_type_from(j.get("name", ""), kind),
            ))
        if len(items) < 100:
            break
        offset += 100
    return jobs


def smartrecruiters_description(slug: str, job_id) -> str:
    data = fetch_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{job_id}")
    sections = (data.get("jobAd") or {}).get("sections") or {}
    return "\n\n".join(parse.html_to_text((sections.get(k) or {}).get("text"))
                       for k in ("jobDescription", "qualifications", "additionalInformation"))


def _workable(slug, name):
    data = fetch_json(f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true")
    jobs = []
    for j in data.get("jobs", []):
        location = ", ".join(x for x in (j.get("city"), j.get("state"), j.get("country")) if x)
        remote = bool(j.get("telecommuting"))
        jobs.append(RawJob(
            source="workable", source_id=f"{slug}:{j.get('shortcode')}", source_name="Workable",
            company=name, title=(j.get("title") or "").strip(), url=j.get("url") or j.get("shortlink") or "",
            apply_url=j.get("application_url"), location=location + (" (Remote)" if remote else ""),
            remote=remote, description=parse.html_to_text(j.get("description")),
            posted_at=parse.iso_to_ts(j.get("published_on") or j.get("created_at")),
            job_type=parse.job_type_from(j.get("title", ""), j.get("employment_type") or ""),
        ))
    return jobs


# ---------- Jobvite (public HTML list) ----------

_JV_ROW = re.compile(r'<td class="jv-job-list-name">\s*<a href="([^"]+)">(.*?)</a>.*?'
                     r'<td class="jv-job-list-location">(.*?)</td>', re.S)
_DEV_TITLE = re.compile(r"engineer|developer|sde|software|programmer", re.I)


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip(" ,")


def _jobvite(slug, name):
    page = fetch_bytes(f"https://jobs.jobvite.com/{slug}/jobs").decode("utf-8", "ignore")
    jobs, lookups = [], 0
    for href, title, loc in _JV_ROW.findall(page):
        title, loc = _clean(title), _clean(loc)
        url = "https://jobs.jobvite.com" + href
        # "5 Locations": open the post to see them, but only for dev titles and only a few
        if re.fullmatch(r"\d+ Locations", loc) and _DEV_TITLE.search(title) and lookups < 25:
            lookups += 1
            try:
                post = fetch_bytes(url).decode("utf-8", "ignore")
                m = re.search(r'<p class="jv-job-detail-meta">(.*?)</p>', post, re.S)
                if m:
                    parts = [_clean(x) for x in re.split(r"<span[^>]*jv-inline-separator[^>]*>\s*</span>", m.group(1))]
                    loc = "; ".join(x for x in parts[1:] if x and x != "NA, NA") or loc
            except Exception:
                pass
        jobs.append(RawJob(
            source="jobvite", source_id=f"{slug}:{href.rsplit('/', 1)[-1]}", source_name=f"{name} careers",
            company=name, title=title, url=url, location=loc,
            job_type=parse.job_type_from(title),
        ))
    return jobs


_FT_ROW = re.compile(r'<a href="(/jobs/[A-Za-z0-9_-]+/[^"]*)"[^>]*data-portal-location="([^"]*)"[^>]*'
                     r'data-portal-remote-location=(\w+)[^>]*>.*?<div class="job-title">(.*?)</div>', re.S)


def _freshteam(slug, name, polite=False):
    host = slug if "." in slug else f"{slug}.freshteam.com"   # some use their own domain
    url = f"https://{host}/jobs"
    if not polite and not robots.allowed(url):
        raise FetchError(f"{name}: robots.txt asks bots not to read {url}")
    page = fetch_bytes(url).decode("utf-8", "ignore")
    jobs = []
    for href, loc, remote, title in _FT_ROW.findall(page):
        title, loc = _clean(title), _clean(loc)
        jobs.append(RawJob(
            source="freshteam", source_id=f"{slug}:{href.split('/')[2]}", source_name=f"{name} careers",
            company=name, title=title, url=f"https://{host}{href}", location=loc,
            remote=remote == "true" and parse.is_open_remote(loc + " remote", True),
            job_type=parse.job_type_from(title),
        ))
    return jobs
