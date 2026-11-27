"""Find people to email at a company (the "contact search" in CLAUDE.md rule 5).

Where addresses come from:
1. The company's own website: its careers, contact and about pages, for the
   addresses it publishes for job seekers (careers@, hr@, jobs@, talent@...).
   No key needed.
2. Hunter.io, if you add a free key on the Connections page: names, job titles
   and work emails people use publicly, with how sure Hunter is about each.
   The free plan has 25 company searches a month, so results are saved and reused.

Never from a job post's text (rule 5). Found people are suggestions: you add
the ones you want on the job's page, and every email still needs your approval
until that kind is on auto.

Only the company's name or web address is sent to these services, nothing about you.
"""

import json
import re
import time
import urllib.parse

from . import parse
from .config import SECRETS_DIR, write_private_file
from .sources import robots
from .sources.http import FetchError, check_public_url, fetch_bytes, fetch_json

KEY_FILE = SECRETS_DIR / "hunter.json"
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}")
ROLE_BOX = re.compile(r"^(careers?|jobs?|hr|hrd|recruit\w*|talent\w*|hiring|people|campus|freshers?|resumes?|cv|"
                      r"join\w*|work\w*|apply|placements?)([._-].*)?@", re.I)
PAGES = ("", "careers", "careers/", "jobs", "contact", "contact-us", "contactus", "about", "about-us")
# Hosts that are job boards or ATSs, not the company's own site.
NOT_COMPANY = re.compile(r"greenhouse\.io|lever\.co|ashbyhq|smartrecruiters|workable|jobvite|myworkdayjobs|"
                         r"oraclecloud|linkedin|naukri|indeed|instahyre|cutshort|wellfound|freshteam|avature|"
                         r"successfactors|icims|eightfold|amazon\.jobs|remotive|remoteok|ycombinator|adzuna", re.I)
HR_WORDS = re.compile(r"recruit|talent|hr\b|human resources|people|hiring|acquisition|campus|staffing", re.I)
MANAGER_WORDS = re.compile(r"engineering manager|head of engineering|director.*engineering|vp.*engineering|cto|"
                           r"tech(nical)? lead|founder|co-founder|chief technology|manager", re.I)
ENGINEER_WORDS = re.compile(r"engineer|developer|sde|programmer|architect", re.I)


class PeopleError(Exception):
    pass


# ---------- Hunter key ----------

def hunter_key() -> str | None:
    try:
        return json.loads(KEY_FILE.read_text()).get("key")
    except (OSError, ValueError):
        return None


def save_hunter_key(key: str) -> None:
    key = key.strip()
    if not re.fullmatch(r"[a-f0-9]{30,64}", key):
        raise PeopleError("That doesn't look like a Hunter API key (letters a-f and numbers).")
    try:
        fetch_json(f"https://api.hunter.io/v2/account?api_key={key}")
    except FetchError as e:
        raise PeopleError("Hunter said no to that key.") from e
    write_private_file(KEY_FILE, json.dumps({"key": key}).encode())


# ---------- company web address ----------

def base_domain(host: str) -> str:
    host = host.lower().split(":")[0]
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in ("co", "com", "net", "org", "ac") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def company_domain(job, links: dict | None = None) -> str | None:
    """The company's own web address: from the job link if it's on the company's site,
    else from the careers link we keep for it, else Hunter (if you have a key)."""
    for url in (job["url"], job["apply_url"] or "", (links or {}).get(job["company"].lower(), {}).get("url", "")):
        host = urllib.parse.urlsplit(url or "").netloc
        if host and not NOT_COMPANY.search(host) and "google." not in host:
            return base_domain(host)
    key = hunter_key()
    if key:
        try:
            d = fetch_json("https://api.hunter.io/v2/domain-search?" + urllib.parse.urlencode(
                {"company": job["company"], "limit": 1, "api_key": key}))
            dom = (d.get("data") or {}).get("domain")
            if dom:
                return dom.lower()
        except FetchError:
            pass
    return None


# ---------- finding ----------

def kind_for(position: str) -> str:
    if HR_WORDS.search(position or ""):
        return "hr"
    if MANAGER_WORDS.search(position or ""):
        return "manager"
    if ENGINEER_WORDS.search(position or ""):
        return "employee"
    return "hr"


def from_website(domain: str, polite_ok: bool = True) -> list[dict]:
    """Addresses the company publishes for job seekers on its own pages."""
    found: dict[str, dict] = {}
    for path in PAGES:
        for host in (f"www.{domain}", domain):
            url = f"https://{host}/{path}"
            if not polite_ok and not robots.allowed(url):
                continue
            try:
                check_public_url(url)          # never a local or private address
                page = fetch_bytes(url, timeout=15).decode("utf-8", "ignore")[:2_000_000]
            except FetchError:
                continue
            for e in set(EMAIL.findall(page.replace("[at]", "@").replace("(at)", "@"))):
                e = e.lower().strip(".")
                if (e.endswith("@" + domain) or e.endswith("." + domain)) and ROLE_BOX.match(e) and e not in found:
                    found[e] = {"name": "", "email": e, "position": "Address on the company's website",
                                "kind": "hr", "confidence": 90, "source": f"Company website ({url})"}
            break
    return list(found.values())


def from_hunter(domain: str, min_confidence: int) -> list[dict]:
    key = hunter_key()
    if not key:
        return []
    try:
        d = fetch_json("https://api.hunter.io/v2/domain-search?" + urllib.parse.urlencode(
            {"domain": domain, "limit": 10, "api_key": key}))
    except FetchError as e:
        raise PeopleError(f"Hunter didn't answer: {str(e).split('api_key')[0]}") from e
    out = []
    for p in (d.get("data") or {}).get("emails") or []:
        email, conf = (p.get("value") or "").lower(), int(p.get("confidence") or 0)
        if not EMAIL.fullmatch(email) or conf < min_confidence:
            continue
        name = " ".join(x for x in (p.get("first_name"), p.get("last_name")) if x)
        pos = p.get("position") or p.get("department") or ""
        status = ((p.get("verification") or {}).get("status")) or "not checked"
        out.append({"name": name[:100], "email": email, "position": pos[:120], "kind": kind_for(pos),
                    "confidence": conf, "source": f"Hunter.io ({conf}% sure, email {status})"})
    return out


def cached(conn, domain: str, days: int) -> list | None:
    row = conn.execute("SELECT MAX(ts) FROM found_people WHERE domain = ?", (domain,)).fetchone()
    if not row or not row[0] or time.time() - row[0] > days * 86400:
        return None
    return conn.execute("SELECT * FROM found_people WHERE domain = ? AND email != '' "
                        "ORDER BY (kind = 'hr') DESC, confidence DESC", (domain,)).fetchall()


def find(conn, job, s: dict, refresh: bool = False) -> list:
    """People to email at this job's company. Saved per company and reused (Settings)."""
    from .handcheck import _links
    domain = company_domain(job, _links())
    if not domain:
        raise PeopleError("Couldn't work out the company's web address.")
    if not refresh:
        hit = cached(conn, domain, s["people_cache_days"])
        if hit is not None:
            return hit
    people = from_website(domain, s["read_no_bots_sites"])
    people += [p for p in from_hunter(domain, s["hunter_min_confidence"]) if p["email"] not in {x["email"] for x in people}]
    now = time.time()
    conn.execute("DELETE FROM found_people WHERE domain = ?", (domain,))
    for p in people:
        conn.execute("INSERT INTO found_people (company, domain, name, email, position, kind, confidence, source, ts) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (job["company"], domain, p["name"], p["email"],
                                                            p["position"], p["kind"], p["confidence"], p["source"], now))
    if not people:   # remember that we looked, so we don't ask again every time
        conn.execute("INSERT INTO found_people (company, domain, name, email, position, kind, confidence, source, ts) "
                     "VALUES (?, ?, '', '', '', 'hr', 0, 'nothing found', ?)", (job["company"], domain, now))
    conn.commit()
    return cached(conn, domain, s["people_cache_days"]) or []


def for_job(conn, job) -> list:
    """Saved suggestions for this job's company. Never looks anything up."""
    return conn.execute("SELECT * FROM found_people WHERE company = ? AND email != '' "
                        "ORDER BY (kind = 'hr') DESC, confidence DESC", (job["company"],)).fetchall()
