"""Big companies' own careers sites.

These are the feeds the careers pages themselves use. They aren't official APIs,
so they can change; a reader that breaks is skipped and shows up as "failed"
in the run log until it's fixed. We stay polite: India-only searches, a few
requests per company, once a day.

portals.json lists them. Types:
  workday   - one reader for every company on Workday (needs host, tenant, site)
  oraclehcm - Oracle Recruiting Cloud (needs host, site)
  amazon, atlassian - their own systems
  sitemap   - companies whose job search asks bots to stay away, but whose
              robots.txt points bots to a sitemap: a plain list of every job
              page. We read only that list (never the job pages), take the
              title and city from each link, and a link we haven't seen before
              is a new job. It's a big file, so it's read every few hours
              (Settings), not every 30 minutes.
"""

import gzip
import html
import io
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from .. import parse
from ..config import DATA_DIR, write_private_file
from . import RawJob, robots
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
    if kind == "sitemap":
        return _sitemap(p)
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


FACETS_FILE = DATA_DIR / "workday_facets.json"
_FACETS_LOCK = threading.Lock()


def _remember_facet(p: dict, facet) -> None:
    """Keep each site's India filter, so the Check page can link to the site already filtered."""
    key = f"{p['tenant']}/{p['site']}"
    with _FACETS_LOCK:
        try:
            saved = json.loads(FACETS_FILE.read_text())
        except (OSError, ValueError):
            saved = {}
        value = list(facet) if facet else None
        if saved.get(key) != value:
            saved[key] = value
            write_private_file(FACETS_FILE, json.dumps(saved).encode())


def saved_facets() -> dict:
    try:
        return json.loads(FACETS_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _workday(p: dict, quick: bool = False) -> list[RawJob]:
    base = f"https://{p['host']}/wday/cxs/{p['tenant']}/{p['site']}"
    facet = _workday_india_facet(base)
    _remember_facet(p, facet)
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


# ---------- Sitemaps ----------

SITEMAP_MAX_BYTES = 40 * 1024 * 1024   # biggest sitemap we read (Optum's is about 6 MB)
SITEMAP_CHILDREN = 20                  # most sub-sitemaps read from one sitemap index
READS_FILE = DATA_DIR / "sitemap_reads.json"
_READS_LOCK = threading.Lock()
_CAPS = {"ai", "ml", "qa", "ui", "ux", "api", "aws", "gcp", "sde", "sre", "it", "sr", "ii", "iii", "iv",
         "devops", "sql", "etl", "bi", "hr", "erp", "sap", "nlp", "llm", "c", "ios", "gen"}
_NICE = {"devops": "DevOps", "ios": "iOS", "gen": "Gen", "sr": "Sr"}
# more Indian city names that show up as the first words of job links
_CITY_EXTRA = ["greater noida", "navi mumbai", "bengaluru urban"]


def _get_text(url: str) -> str:
    for attempt in range(len(RETRY_WAITS) + 1):
        r = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with _GATE, urllib.request.urlopen(r, timeout=60) as f:
                body = f.read(SITEMAP_MAX_BYTES + 1)
            if len(body) > SITEMAP_MAX_BYTES:
                raise FetchError(f"{url}: sitemap too big")
            if body[:2] == b"\x1f\x8b":
                with gzip.GzipFile(fileobj=io.BytesIO(body)) as z:
                    body = z.read(SITEMAP_MAX_BYTES + 1)
                if len(body) > SITEMAP_MAX_BYTES:
                    raise FetchError(f"{url}: sitemap too big")
            return body.decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            if not (e.code == 429 or e.code >= 500) or attempt == len(RETRY_WAITS):
                raise FetchError(f"{url}: HTTP {e.code}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt == len(RETRY_WAITS):
                raise FetchError(f"{url}: {e}") from e
        time.sleep(RETRY_WAITS[attempt])


def _nice_title(words: list[str]) -> str:
    out = []
    for w in words:
        low = w.lower()
        if low in _NICE:
            out.append(_NICE[low])
        elif low in _CAPS:
            out.append(low.upper())
        elif w.islower():
            out.append(w if low in ("and", "or", "of", "for", "in", "to", "the", "with") and out else w.capitalize())
        else:
            out.append(w)
    return " ".join(out).strip(" ,-")


def _lead_city(words: list[str]) -> int:
    """How many of the first words are an Indian city (0 if they aren't)."""
    cities = parse.INDIA_WORDS + _CITY_EXTRA
    for n in (3, 2, 1):
        if len(words) > n and " ".join(words[:n]).lower() in cities:
            return n
    return 0


def sitemap_job(url: str, style: str) -> tuple[str, str, str] | None:
    """(job id, title, city) for an Indian job link, None for anything else.

    talentbrew: /job/<city>/<title-words>/<org id>/<job id>
    rmk:        /job/<City>-<Title-Words>-<postcode or state>/<job id>/"""
    path = urllib.parse.unquote(urllib.parse.urlsplit(url).path)
    if style == "talentbrew":
        m = re.search(r"/job/([^/]+)/([^/]+)/\d+/(\d+)/?$", path)
        if not m:
            return None
        city = m.group(1).replace("-", " ")
        if not parse.is_india(city):
            return None
        return m.group(3), _nice_title(m.group(2).split("-")), city.title()
    if style == "rmk":
        m = re.search(r"/job/([^/]+)/(\d+)/?$", path)
        if not m:
            return None
        words = [w.replace("_", ".") for w in m.group(1).split("-") if w]  # "Sr_" was "Sr."
        lead = _lead_city(words)
        if lead:
            city, words = " ".join(words[:lead]), words[lead:]
        elif parse.is_india(" ".join(words)):
            city = "India"
        else:
            return None
        while words and (re.fullmatch(r"\d{5,6}", words[-1]) or re.fullmatch(r"[A-Z]{2}", words[-1])):
            words = words[:-1]  # postcode or state code at the end
        title = _nice_title(words)
        return (m.group(2), title, city) if title else None
    return None


def _sitemap_links(p: dict) -> list[str]:
    url = p["sitemap"]
    if urllib.parse.urlsplit(url).netloc != p["host"]:
        raise FetchError(f"{p['name']}: sitemap is not on {p['host']}")
    if not robots.allowed(url):
        raise FetchError(f"{p['name']}: robots.txt asks bots not to read its sitemap")
    text = _get_text(url)
    if "<sitemapindex" in text[:2000]:
        links = []
        for child in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", text)[:SITEMAP_CHILDREN]:
            child = html.unescape(child)
            if urllib.parse.urlsplit(child).netloc == p["host"] and robots.allowed(child):
                links += re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", _get_text(child))
        return [html.unescape(x) for x in links]
    return [html.unescape(x) for x in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", text)]


def _sitemap(p: dict) -> list[RawJob]:
    rules = robots.rules_for(p["host"])
    jobs = []
    for link in _sitemap_links(p):
        if urllib.parse.urlsplit(link).netloc != p["host"] or not robots.allowed_by(rules, link):
            continue
        found = sitemap_job(link, p["style"])
        if not found:
            continue
        job_id, title, city = found
        jobs.append(RawJob(
            source="sitemap", source_id=f"{p['host']}:{job_id}", source_name=f"{p['name']} careers",
            company=p["name"], title=title[:200], url=link,
            location="India" if city == "India" else f"{city}, India",
            job_type=parse.job_type_from(title),
        ))
    _remember_read(p)
    return jobs


def _remember_read(p: dict) -> None:
    with _READS_LOCK:
        try:
            saved = json.loads(READS_FILE.read_text())
        except (OSError, ValueError):
            saved = {}
        saved[p["host"]] = time.time()
        write_private_file(READS_FILE, json.dumps(saved).encode())


def sitemap_due(p: dict, hours: int, now: float | None = None) -> bool:
    try:
        last = json.loads(READS_FILE.read_text()).get(p["host"], 0)
    except (OSError, ValueError):
        last = 0
    return (now or time.time()) - last >= hours * 3600 - 300
