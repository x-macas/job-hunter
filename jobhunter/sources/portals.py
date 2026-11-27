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
              One exception, style "jsonld": when the links carry no city
              (Teradata), each NEW job page is opened once, slowly, for its
              city; the robots.txt of those sites allows that.
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
    if kind == "avature":
        return _avature(p, quick)
    if kind == "cards":
        return _cards(p)
    if kind == "jsonlist":
        return _jsonlist(p)
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
    rmk:        /job/<City>-<Title-Words>-<postcode or state>/<job id>/
    jid:        /job/<title-words>-in-<city>-<state>-india-jid-<job id>        (Renesas)
    team:       /jobs/<team>/<city>/<title-words>/                            (Optiver)
    avature:    /careers/JobDetail/<Title-Words-City>/<job id>                (Bloomberg)"""
    path = urllib.parse.unquote(urllib.parse.urlsplit(url).path)
    if style == "talentbrew":
        m = re.search(r"/job/([^/]+)/([^/]+)/\d+/(\d+)/?$", path)
        if not m:
            return None
        city = m.group(1).replace("-", " ")
        if not parse.is_india(city):
            return None
        return m.group(3), _nice_title(m.group(2).split("-")), city.title()
    if style == "jid":
        m = re.search(r"/job/([^/]+)-in-([^/]+)-jid-(\d+)/?$", path)
        if not m or not parse.is_india(m.group(2).replace("-", " ")):
            return None
        place = [w for w in m.group(2).split("-") if w.lower() != "india"]
        city = " ".join(place[:_lead_city(place + [""]) or 1]).title()
        return m.group(3), _nice_title(m.group(1).split("-")), city
    if style == "team":
        m = re.search(r"/jobs/([^/]+)/([^/]+)/([^/]+)/?$", path)
        if not m or not parse.is_india(m.group(2).replace("-", " ")):
            return None
        return f"{m.group(1)}/{m.group(2)}/{m.group(3)}", _nice_title(m.group(3).split("-")), m.group(2).title()
    if style == "avature":
        m = re.search(r"/JobDetail/([^/]+)/(\d+)/?$", path)
        if not m:
            return None
        words = [w for w in m.group(1).split("-") if w]
        if not parse.is_india(" ".join(words)):
            return None
        city = "India"
        for n in (3, 2, 1):   # the city is at the end
            if len(words) > n and " ".join(words[-n:]).lower() in parse.INDIA_WORDS + _CITY_EXTRA:
                city, words = " ".join(words[-n:]), words[:-n]
                break
        title = _nice_title(words)
        return (m.group(2), title, city) if title else None
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
    polite = p.get("polite")   # robots.txt asks bots to stay away; read anyway only if you allow it (Settings)
    if not polite and not robots.allowed(url):
        raise FetchError(f"{p['name']}: robots.txt asks bots not to read its sitemap")
    text = _get_text(url)
    if "<sitemapindex" in text[:2000]:
        links = []
        for child in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", text)[:SITEMAP_CHILDREN]:
            child = html.unescape(child)
            if urllib.parse.urlsplit(child).netloc == p["host"] and (polite or robots.allowed(child)):
                links += re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", _get_text(child))
        return [html.unescape(x) for x in links]
    return [html.unescape(x) for x in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", text)]


JOB_PAGES_FILE = DATA_DIR / "job_pages.json"
_LD = re.compile(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", re.S | re.I)


def _ld_job(page: str) -> dict | None:
    """The JobPosting block that careers pages put in for Google (schema.org)."""
    for block in _LD.findall(page):
        try:
            d = json.loads(block.strip(), strict=False)   # some sites leave raw line breaks in the text
        except ValueError:
            continue
        for x in d if isinstance(d, list) else d.get("@graph", [d]) if isinstance(d, dict) else []:
            if isinstance(x, dict) and x.get("@type") == "JobPosting":
                return x
    return None


def _ld_place(job: dict) -> tuple[str, bool]:
    """('Hyderabad, Telangana, India', remote?) from a JobPosting block."""
    places = job.get("jobLocation") or []
    places = places if isinstance(places, list) else [places]
    names = []
    for pl in places:
        a = (pl or {}).get("address") or {} if isinstance(pl, dict) else {}
        if isinstance(a, dict):
            country = a.get("addressCountry")
            country = country.get("name", "") if isinstance(country, dict) else country or ""
            bits = [str(a.get(k) or "") for k in ("addressLocality", "addressRegion")] + [str(country)]
            names.append(", ".join(b for b in bits if b))
    remote = str(job.get("jobLocationType", "")).upper() == "TELECOMMUTE"
    return " / ".join(n for n in names if n)[:200], remote


def _job_pages(p: dict, links: list[str], rules) -> list[RawJob]:
    """style "jsonld": the sitemap links have no city, so we open each job page once
    (new links only, at most a few per run, one at a time) and read the JobPosting
    block the page has for Google. What we read is saved, so a job page is never
    opened twice."""
    pat = re.compile(p["job_link"])
    with _READS_LOCK:
        try:
            saved = json.loads(JOB_PAGES_FILE.read_text())
        except (OSError, ValueError):
            saved = {}
    live = set(links)
    mine = {u: v for u, v in saved.get(p["host"], {}).items() if u in live}   # drop jobs taken down
    budget = int(p.get("pages_per_run", 100))
    for link in links:
        if link in mine or budget <= 0 or not pat.search(link):
            continue
        if urllib.parse.urlsplit(link).netloc != p["host"] or not robots.allowed_by(rules, link):
            continue
        budget -= 1
        try:
            ld = _ld_job(_get_text(link)[:3_000_000])
        except FetchError as e:
            if not re.search(r"HTTP (404|410)$", str(e)):
                continue          # try again next time
            ld = None             # the job is closed: remember that, don't open it again
        if ld:
            place, remote = _ld_place(ld)
            posted = None
            try:
                posted = datetime.fromisoformat(str(ld.get("datePosted", ""))[:10]).timestamp()
            except ValueError:
                pass
            mine[link] = {"t": html.unescape(str(ld.get("title", "")))[:200], "l": place, "r": remote, "p": posted}
        else:
            mine[link] = {"t": "", "l": "", "r": False, "p": None}   # not a job page: don't open it again
        time.sleep(1)
    with _READS_LOCK:
        try:
            saved = json.loads(JOB_PAGES_FILE.read_text())
        except (OSError, ValueError):
            saved = {}
        saved[p["host"]] = mine
        write_private_file(JOB_PAGES_FILE, json.dumps(saved).encode())
    jobs = []
    for link, v in mine.items():
        if v["t"] and not v["l"] and p.get("country"):
            v = {**v, "l": p["country"]}     # India-only company whose pages leave the city out
        if not v["t"] or not parse.is_india(v["l"]):
            continue
        m = pat.search(link)
        job_id = m.group(1) if m and m.groups() else urllib.parse.urlsplit(link).path
        jobs.append(RawJob(
            source="sitemap", source_id=f"{p['host']}:{job_id}", source_name=f"{p['name']} careers",
            company=p["name"], title=v["t"], url=link, location=v["l"], remote=v["r"], posted_at=v["p"],
            job_type=parse.job_type_from(v["t"]),
        ))
    return jobs


def _sitemap(p: dict) -> list[RawJob]:
    rules = [] if p.get("polite") else robots.rules_for(p["host"])
    if p["style"] == "jsonld":
        jobs = _job_pages(p, _sitemap_links(p), rules)
        _remember_read(p)
        return jobs
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


def _key(p: dict) -> str:
    return p.get("host") or p.get("slug") or p["name"]


def remember_read(p: dict) -> None:
    with _READS_LOCK:
        try:
            saved = json.loads(READS_FILE.read_text())
        except (OSError, ValueError):
            saved = {}
        saved[_key(p)] = time.time()
        write_private_file(READS_FILE, json.dumps(saved).encode())


_remember_read = remember_read


def due(p: dict, hours: int, now: float | None = None) -> bool:
    """Read this source again? (sitemaps and 'polite' sites are read only every few hours)"""
    try:
        last = json.loads(READS_FILE.read_text()).get(_key(p), 0)
    except (OSError, ValueError):
        last = 0
    return (now or time.time()) - last >= hours * 3600 - 300


sitemap_due = due



# ---------- Avature search pages (EA...) ----------

_AV_ROW = re.compile(r'<a class="link[^"]*" href="([^"]+/JobDetail/(?:[^"/]+/)?(\d+))"[^>]*>\s*(.*?)\s*</a>'
                     r'.{0,1500}?class="list-item-location">\s*(.*?)\s*</span>', re.S)


def _avature(p: dict, quick: bool = False) -> list[RawJob]:
    """An Avature careers site's public job search page: title, place and link for each job."""
    base = p["base"].rstrip("/")
    jobs, seen, offset = [], set(), 0
    for _ in range(2 if quick else 25):
        url = f"{base}/SearchJobs/?jobRecordsPerPage=50&jobOffset={offset}"
        if not p.get("polite") and not robots.allowed(url):
            raise FetchError(f"{p['name']}: robots.txt asks bots not to read {url}")
        rows = _AV_ROW.findall(_get_text(url))
        new = 0
        for link, job_id, title, loc in rows:
            if job_id in seen:
                continue
            seen.add(job_id)
            new += 1
            loc, title = html.unescape(re.sub(r"<[^>]+>", " ", loc)).strip(), html.unescape(title).strip()
            if not parse.is_india(loc):
                continue
            jobs.append(RawJob(source="avature", source_id=f"{p['host']}:{job_id}", source_name=f"{p['name']} careers",
                               company=p["name"], title=title[:200], url=html.unescape(link), location=loc,
                               job_type=parse.job_type_from(title)))
        if not new:
            break
        offset += len({r[1] for r in rows})    # the site decides how many it shows per page
    return jobs



# ---------- simple job-card pages (Fidelity India...) ----------

def _cards(p: dict) -> list[RawJob]:
    """A careers page that shows the newest jobs as cards (title, link, place, posted date).
    Only the first page: read every check, so new jobs are caught as they appear."""
    url = p["list"]
    if not p.get("polite") and not robots.allowed(url):
        raise FetchError(f"{p['name']}: robots.txt asks bots not to read {url}")
    page = _get_text(url)
    base = "https://" + urllib.parse.urlsplit(url).netloc
    jobs = []
    chunks = page.split('class="card card-job"')[1:]
    for c in chunks:
        m = re.search(r'href="(/[^"]+/jobs/(\d+)/[^"]*)"[^>]*>(.*?)</a>', c, re.S)
        if not m:
            continue
        link, job_id, title = m.group(1), m.group(2), html.unescape(re.sub(r"<[^>]+>", "", m.group(3))).strip()
        items = [html.unescape(re.sub(r"<[^>]+>|\s+", " ", x)).strip() for x in re.findall(r'list-inline-item">(.*?)</li>', c, re.S)]
        loc = next((x for x in items if parse.is_india(x) or re.search(r",\s*[A-Z]{2}$", x)), p.get("country", ""))
        posted = next((x for x in " ".join(items).split("  ") if x.strip().startswith("Posted")), "")
        when = None
        mm = re.search(r"Posted (\w{3}) (\d{1,2})", posted)
        if mm:
            try:
                d = datetime.strptime(f"{mm.group(1)} {mm.group(2)} {datetime.now().year}", "%b %d %Y")
                when = d.replace(year=d.year - 1).timestamp() if d > datetime.now() else d.timestamp()
            except ValueError:
                pass
        if loc and not parse.is_india(loc) and p.get("country") == "India":
            loc += ", India"
        jobs.append(RawJob(source="cards", source_id=f"{p['host']}:{job_id}", source_name=f"{p['name']} careers",
                           company=p["name"], title=title[:200], url=base + link, location=loc, posted_at=when,
                           job_type=parse.job_type_from(title)))
    return jobs



# ---------- a company's own public JSON job list (LambdaTest...) ----------

def _dig(obj, path: str):
    for part in [x for x in path.split(".") if x]:
        obj = obj.get(part) if isinstance(obj, dict) else None
    return obj


def _jsonlist(p: dict) -> list[RawJob]:
    """A plain public list of jobs as JSON. The entry in portals.json says which field is which:
    items (where the list is), id, title, location (a field, or 'regex:<field>:<pattern>'), description,
    job_type_field, link (a field holding the job's own link), page, body (sent as a POST search, if given)."""
    url = p["url"]
    if not p.get("polite") and not robots.allowed(url):
        raise FetchError(f"{p['name']}: robots.txt asks bots not to read {url}")
    data = _req(url, p.get("body"))   # some lists are asked for with a small search form (POST)
    items = _dig(data, p.get("items", "")) if p.get("items") else data
    jobs = []
    for j in items or []:
        if not isinstance(j, dict):
            continue
        title = str(_dig(j, p["title"]) or "").strip()
        job_id = str(_dig(j, p["id"]) or title)
        loc_rule = p.get("location", "")
        if loc_rule.startswith("regex:"):
            _, field, pattern = loc_rule.split(":", 2)
            m = re.search(pattern, str(_dig(j, field) or ""))
            loc = m.group(1).strip() if m else ""
        else:
            loc = str(_dig(j, loc_rule) or "")
        if p.get("country") == "India" and not parse.is_india(loc):
            loc = f"{loc}, India" if loc else "India"
        desc = parse.html_to_text(str(_dig(j, p["description"]) or "")) if p.get("description") else None
        jobs.append(RawJob(source="jsonlist", source_id=f"{p['host']}:{job_id}", source_name=f"{p['name']} careers",
                           company=p["name"], title=title[:200],
                           url=str(_dig(j, p["link"]) or "") if p.get("link") and str(_dig(j, p["link"]) or "").startswith("https://")
                           else p["page"].replace("{id}", urllib.parse.quote(job_id)),
                           location=loc, description=desc, job_type=parse.job_type_from(title, str(_dig(j, p.get("job_type_field", "")) or "").replace("_", " "))))
    return [j for j in jobs if j.title]
