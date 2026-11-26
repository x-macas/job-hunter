"""Big companies' own careers sites.

These are the feeds the careers pages themselves use. They aren't official APIs,
so they can change; a reader that breaks is skipped and shows up as "failed"
in the run log until it's fixed. We stay polite: India-only searches, a few
requests per company, once a day.

portals.json lists them. Types:
  workday   - one reader for every company on Workday (needs host, tenant, site)
  oraclehcm - Oracle Recruiting Cloud (needs host, site)
  amazon, atlassian - their own systems
"""

import json
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from .. import parse
from . import RawJob
from .http import FetchError

PORTALS_FILE = Path(__file__).with_name("portals.json")
UA = "Mozilla/5.0 (X11; Linux x86_64) job-hunter/0.1 (personal job search)"
SEARCH = "software"


def default_portals() -> list[dict]:
    return json.loads(PORTALS_FILE.read_text())


# At most 3 careers-site requests at the same time, so we never flood anyone.
_GATE = threading.BoundedSemaphore(3)
RETRY_WAITS = (3, 10)  # seconds to wait before the 2nd and 3rd try


def _req(url: str, body: dict | None = None, timeout: int = 30):
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(len(RETRY_WAITS) + 1):
        r = urllib.request.Request(url, data=data, headers={
            "User-Agent": UA, "Accept": "application/json", "Content-Type": "application/json"})
        try:
            with _GATE, urllib.request.urlopen(r, timeout=timeout) as f:
                return json.loads(f.read(20 * 1024 * 1024))
        except urllib.error.HTTPError as e:
            busy = e.code == 429 or e.code >= 500
            if not busy or attempt == len(RETRY_WAITS):
                raise FetchError(f"{url.split('?')[0]}: HTTP {e.code}") from e
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
            if attempt == len(RETRY_WAITS):
                raise FetchError(f"{url.split('?')[0]}: {e}") from e
        time.sleep(RETRY_WAITS[attempt])


def fetch_portal(p: dict, quick: bool = False) -> list[RawJob]:
    """quick=True reads only the newest jobs (first page or two). Used by the
    30-minute check; the morning run reads the full list."""
    kind = p["type"]
    if kind == "workday":
        return _workday(p, quick)
    if kind == "oraclehcm":
        return _oraclehcm(p, quick)
    if kind == "amazon":
        return _amazon(p, quick)
    if kind == "atlassian":
        return _atlassian(p)
    return []


# ---------- Workday ----------

def _days_ago(text: str) -> float | None:
    t = (text or "").lower()
    if "today" in t:
        days = 0
    elif "yesterday" in t:
        days = 1
    else:
        m = re.search(r"(\d+)\+?\s*days", t)
        if not m:
            return None
        days = int(m.group(1))
    return time.time() - days * 86400


def _workday_india_facet(base: str) -> tuple[str, list[str]] | None:
    """Find the India filter for this Workday site.

    Most sites have a 'country = India' filter. Some only list offices
    ("Bengaluru - GS India", "Hyderabad"...); then we pick every Indian office."""
    d = _req(f"{base}/jobs", {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""})
    offices: dict[str, list[str]] = {}

    def walk(facets):
        for f in facets or []:
            param = f.get("facetParameter", "")
            for v in f.get("values") or []:
                if "ountry" in param and v.get("descriptor") == "India":
                    return param, [v["id"]]
                if v.get("values"):  # e.g. "Location" > "Country" > "India"
                    found = walk([v])
                    if found:
                        return found
                elif "ocation" in param and v.get("id") and parse.is_india(v.get("descriptor") or ""):
                    offices.setdefault(param, []).append(v["id"])
        return None
    found = walk(d.get("facets"))
    if found:
        return found
    if offices:
        param, ids = max(offices.items(), key=lambda kv: len(kv[1]))
        return param, ids
    return None


def _workday(p: dict, quick: bool = False) -> list[RawJob]:
    base = f"https://{p['host']}/wday/cxs/{p['tenant']}/{p['site']}"
    facet = _workday_india_facet(base)
    applied = {facet[0]: facet[1]} if facet else {}
    jobs = []
    # Full run: search "software", up to 200 jobs. Quick check: no search word,
    # which Workday lists newest first, and only the first 40.
    for offset in range(0, 40 if quick else 200, 20):
        if quick:
            text = "" if facet else "india"
        else:
            text = SEARCH if facet else f"{SEARCH} india"  # no country filter: ask for India in words
        d = _req(f"{base}/jobs", {"appliedFacets": applied, "limit": 20, "offset": offset, "searchText": text})
        posts = d.get("jobPostings", [])
        for j in posts:
            if not j.get("externalPath"):
                continue
            loc = j.get("locationsText") or ""
            if facet and (not loc or "Locations" in loc):
                loc = "India"
            elif facet and not parse.is_india(loc):
                loc += ", India"
            jobs.append(RawJob(
                source="workday", source_id=f"{p['tenant']}/{p['site']}:{j['externalPath']}", source_name=f"{p['name']} careers",
                company=p["name"], title=j.get("title", "").strip(),
                url=f"https://{p['host']}/{p['site']}{j['externalPath']}",
                location=loc, posted_at=_days_ago(j.get("postedOn")),
                job_type=parse.job_type_from(j.get("title", "")),
            ))
        if len(posts) < 20:
            break
    return jobs


def workday_description(source_id: str) -> str:
    key, _, path = source_id.partition(":")
    tenant, _, site = key.partition("/")
    p = next((x for x in default_portals() if x["type"] == "workday" and x["tenant"] == tenant
              and (not site or x["site"] == site)), None)
    if p is None:
        raise FetchError(f"unknown Workday site {tenant}")
    info = _req(f"https://{p['host']}/wday/cxs/{tenant}/{p['site']}{path}").get("jobPostingInfo", {})
    return parse.html_to_text(info.get("jobDescription"))


# ---------- Oracle Recruiting Cloud ----------

def _oraclehcm(p: dict, quick: bool = False) -> list[RawJob]:
    host, site = p["host"], p["site"]
    jobs = []
    for offset in ((0,) if quick else (0, 100)):
        url = (f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true"
               f"&expand=requisitionList&finder=findReqs;siteNumber={site},keyword=%22{SEARCH}%22,"
               f"location=India,limit=100,offset={offset},sortBy=POSTING_DATES_DESC")
        items = _req(url).get("items") or [{}]
        reqs = items[0].get("requisitionList") or []
        for r in reqs:
            jobs.append(RawJob(
                source="oraclehcm", source_id=f"{host.split('.')[0]}/{site}:{r['Id']}", source_name=f"{p['name']} careers",
                company=p["name"], title=(r.get("Title") or "").strip(),
                url=f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{r['Id']}",
                location=r.get("PrimaryLocation") or "India",
                description=r.get("ShortDescriptionStr") or "",
                posted_at=parse.iso_to_ts(r.get("PostedDate")),
                job_type=parse.job_type_from(r.get("Title", ""), r.get("JobSchedule") or ""),
            ))
        if len(reqs) < 100:
            break
    return jobs


# ---------- Amazon ----------

def _amazon(p: dict, quick: bool = False) -> list[RawJob]:
    jobs = []
    for offset in ((0,) if quick else (0, 100, 200)):
        d = _req("https://www.amazon.jobs/en/search.json?base_query=software&normalized_country_code%5B%5D=IND"
                 f"&result_limit=100&offset={offset}&sort=recent")
        for j in d.get("jobs", []):
            try:
                posted = datetime.strptime(j.get("posted_date", ""), "%B %d, %Y").timestamp()
            except ValueError:
                posted = None
            desc = "\n\n".join(parse.html_to_text(j.get(k)) for k in
                               ("description", "basic_qualifications", "preferred_qualifications"))
            jobs.append(RawJob(
                source="amazon", source_id=str(j["id"]), source_name="Amazon careers",
                company=p["name"], title=(j.get("title") or "").strip(),
                url="https://www.amazon.jobs" + j.get("job_path", ""),
                location=f"{j.get('location', '')}, India", description=desc, posted_at=posted,
                job_type="intern" if j.get("is_intern") else parse.job_type_from(j.get("title", ""), j.get("job_schedule_type") or ""),
            ))
        if len(d.get("jobs", [])) < 100:
            break
    return jobs


# ---------- Atlassian ----------

def _atlassian(p: dict) -> list[RawJob]:
    jobs = []
    for j in _req("https://www.atlassian.com/endpoint/careers/listings"):
        locations = j.get("locations") or []
        desc = "\n\n".join(parse.html_to_text(j.get(k)) for k in ("overview", "responsibilities", "qualifications"))
        remote = any("remote" in loc.lower() for loc in locations)
        jobs.append(RawJob(
            source="atlassian", source_id=str(j.get("id")), source_name="Atlassian careers",
            company=p["name"], title=(j.get("title") or "").strip(),
            url=j.get("applyUrl") or "https://www.atlassian.com/company/careers",
            location="; ".join(locations), remote=remote and any(parse.is_india(l) for l in locations),
            description=desc, job_type=parse.job_type_from(j.get("title", ""), j.get("type") or ""),
        ))
    return jobs
