"""The Check page.

At the top: every job posted on the company careers sites the website reads,
in the last 24 hours, with a link to the job on the company's own site.

Below that, companies whose sites we can't or shouldn't read (they block bots, their robots.txt
says no, or we haven't found a readable feed yet) are checked by hand, so they come
first with a tick box for today and a link to their careers site (newest first
where the site allows it in the link).
"""

import json
import re
import time
import urllib.parse
from datetime import date
from pathlib import Path

from . import parse, rules
from .sources import boards, portals

LINKS_FILE = Path(__file__).parent / "sources" / "careers_links.json"
WHY = {
    "blocks": "Blocks automated reading",
    "no_bots": "Asks bots not to read its site",
    "not_found": "Not readable yet",
}


def _links() -> dict:
    return {k.lower(): v for k, v in json.loads(LINKS_FILE.read_text()).items()}


def source_url(src: dict, facets: dict | None = None) -> str:
    """The careers page for a source the website reads, filtered to India and
    newest first where the site's links allow it."""
    kind = src.get("type") or src.get("ats")
    slug = src.get("slug", "")
    if kind == "workday":
        # Workday lists newest first; its links take the same India filter the website uses.
        url = f"https://{src['host']}/{src['site']}"
        facet = (facets or {}).get(f"{src['tenant']}/{src['site']}")
        if facet:
            url += "?" + urllib.parse.urlencode([(facet[0], i) for i in facet[1]])
        return url
    if kind == "oraclehcm":
        return (f"https://{src['host']}/hcmUI/CandidateExperience/en/sites/{src['site']}/requisitions?"
                + urllib.parse.urlencode({"location": "India", "sortBy": "POSTING_DATES_DESC"}))
    if kind == "amazon":
        return "https://www.amazon.jobs/en/search?base_query=software&country=IND&sort=recent"
    if kind == "atlassian":
        return "https://www.atlassian.com/company/careers/all-jobs"
    if kind == "sitemap":
        return src["page"]
    if kind == "avature":
        return src["base"] + "/SearchJobs/"
    if kind in ("cards", "pagelist"):
        return src["list"]
    if kind == "jsonlist":
        return src["page"].replace("{id}", "")
    if kind == "eightfold":
        return f"https://{src['host']}/careers?location={src.get('country', 'India')}"
    if kind == "jibe":
        return f"https://{src['host']}/careers-home/jobs?location={src.get('country', 'India')}"
    if kind == "srsite":
        return f"{src['base']}?country={src.get('country', 'India')}"
    return {
        "greenhouse": (f"https://job-boards.eu.greenhouse.io/{slug[:-3]}" if slug.endswith("@eu")
                       else f"https://job-boards.greenhouse.io/{slug}"),
        "lever": f"https://jobs.lever.co/{slug}",
        "ashby": f"https://jobs.ashbyhq.com/{slug}",
        "smartrecruiters": f"https://jobs.smartrecruiters.com/{slug}",
        "workable": f"https://apply.workable.com/{slug}/",
        "jobvite": f"https://jobs.jobvite.com/{slug}/jobs",
        "freshteam": f"https://{slug if '.' in slug else slug + '.freshteam.com'}/jobs",
        "mynexthire": f"https://{slug}.mynexthire.com/employer/jobs?src=careers",
        "recruiterbox": src.get("page", ""),
        "rippling": f"https://ats.rippling.com/{slug}/jobs",
        "kula": f"https://careers.kula.ai/{slug}",
        "pinpoint": f"https://{slug}.pinpointhq.com/",
        "gem": f"https://jobs.gem.com/{slug}",
        "recruitee": f"https://{slug}.recruitee.com/",
        "jazzhr": f"https://{slug}.applytojob.com/apply",
        "zohorecruit": f"https://{slug}/jobs/{src.get('page', 'Careers')}",
    }.get(kind, "")


def slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", name)


def _same(src: dict, name: str) -> bool:
    names = [src["name"]] + src.get("also", [])
    return rules.name_in(name, names) or any(rules.name_in(n, [name]) for n in names)


def _search_url(company: str) -> str:
    return "https://www.google.com/search?" + urllib.parse.urlencode({"q": f"{company} careers India software jobs"})


def build(s: dict) -> dict:
    """Returns {'first': [...], 'later': [...], 'auto': [...]}.
    first = blocked / no-bots companies, later = not readable yet, auto = read by the website."""
    sources = [x for x in boards.default_boards() + boards.boards_from_setting(s["extra_boards"])
               + portals.default_portals() if s["read_no_bots_sites"] or not x.get("polite")]
    links = _links()
    facets = portals.saved_facets()
    out = {"first": [], "later": [], "auto": []}
    for name in dict.fromkeys(s["big_companies"] + s["watch_list"]):
        if not name.strip():
            continue
        srcs = [x for x in sources if _same(x, name)]
        item = {"name": name, "slug": slug(name), "watch": name in s["watch_list"]}
        if srcs:
            item["url"] = source_url(srcs[0], facets)
            out["auto"].append(item)
            continue
        known = links.get(name.lower(), {})
        why = known.get("why", "not_found")
        item.update(url=known.get("url") or _search_url(name), why=WHY.get(why, WHY["not_found"]),
                    guessed=not known.get("url"))
        out["first" if why in ("blocks", "no_bots") or item["watch"] else "later"].append(item)
    for group in out.values():
        group.sort(key=lambda i: (not i["watch"], i["name"].lower()))
    return out


def done_today(conn) -> set[str]:
    day = date.today().isoformat()
    return {r[0] for r in conn.execute("SELECT company FROM hand_checks WHERE day = ?", (day,))}


def mark(conn, company: str, done: bool) -> None:
    day = date.today().isoformat()
    if done:
        conn.execute("INSERT OR IGNORE INTO hand_checks (day, company, ts) VALUES (?, ?, ?)",
                     (day, company, time.time()))
    else:
        conn.execute("DELETE FROM hand_checks WHERE day = ? AND company = ?", (day, company))
    conn.commit()


def new_today(conn, s: dict, now: float | None = None, hours: int = 24) -> list[dict]:
    """Jobs posted on company careers sites in the last 24 hours, in your locations,
    grouped by company. A job counts as new if the site says it was posted in that time,
    or (when the site gives no date) the website first saw it then, on a site it
    already knew, so a site added today doesn't show its whole list as new."""
    now = now or time.time()
    since = now - hours * 3600
    rows = conn.execute(
        """SELECT j.*, (SELECT MIN(k.first_seen) FROM jobs k
                        WHERE k.company = j.company AND k.source = j.source) AS site_first
           FROM jobs j
           WHERE j.status != 'skipped' AND j.added_by != 'link'
             AND (j.posted_at >= ? OR (j.posted_at IS NULL AND j.first_seen >= ?))
             AND j.source NOT IN ('remotive', 'remoteok', 'hn', 'adzuna', 'email')""", (since, since)).fetchall()
    groups: dict[str, dict] = {}
    for r in rows:
        if r["posted_at"] is None and r["first_seen"] <= r["site_first"] + 3600:
            continue
        if not parse.location_ok(r["location"] or "", bool(r["remote"]), s["locations"]):
            continue
        g = groups.setdefault(r["company"], {"name": r["company"], "slug": slug(r["company"]),
                                             "dev": [], "other": 0, "fits": 0, "url": None})
        if rules.is_role(r["title"], s):
            g["dev"].append(r)
            g["fits"] += bool(r["rule_ok"])
        else:
            g["other"] += 1
    sources = boards.default_boards() + boards.boards_from_setting(s["extra_boards"]) + portals.default_portals()
    facets = portals.saved_facets()
    for g in groups.values():
        src = next((x for x in sources if _same(x, g["name"])), None)
        g["url"] = source_url(src, facets) if src else None
        g["dev"].sort(key=lambda r: (not r["rule_ok"], -(r["posted_at"] or r["first_seen"])))
    return sorted((g for g in groups.values() if g["dev"]),
                  key=lambda g: (-g["fits"], -len(g["dev"]), g["name"].lower()))


# ---------- "Paste the page" for sites we can't read ----------

PASTE_MAX_CHARS = 1_000_000
_COUNT = re.compile(r"\s*\(\d[\d,]*\)\s*$")   # "Software Engineering (120)" in a filter menu


def page_titles(text: str, s: dict) -> list[tuple[str, str]]:
    """(title, the line after it) for every line of pasted page text that looks like a job title."""
    lines = [" ".join(x.split()) for x in text[:PASTE_MAX_CHARS].splitlines()]
    lines = [_COUNT.sub("", x) for x in lines if x.strip()]
    out = []
    for i, line in enumerate(lines):
        if 4 <= len(line) <= 120 and rules.is_role(line, s):
            out.append((line, lines[i + 1][:100] if i + 1 < len(lines) else ""))
    return out


def paste(conn, s: dict, company: str, text: str, now: float | None = None) -> None:
    """Save this paste and work out which job titles are new since the last one.
    Titles are counted, so a second 'Software Engineer' job shows up as new too."""
    now = now or time.time()
    found = page_titles(text, s)
    counts: dict[str, int] = {}
    for title, _ in found:
        counts[title] = counts.get(title, 0) + 1
    row = conn.execute("SELECT * FROM pasted_pages WHERE company = ?", (company,)).fetchone()
    new = []
    if row:
        before = dict(json.loads(row["titles"]))
        for title, near in found:
            if before.get(title, 0) > 0:
                before[title] -= 1
                continue
            ok, reason = rules.title_check(title, s)
            new.append({"title": title, "near": near, "fits": ok, "reason": reason})
    conn.execute(
        "INSERT INTO pasted_pages (company, titles, ts, prev_ts, last_new) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(company) DO UPDATE SET titles = excluded.titles, ts = excluded.ts, "
        "prev_ts = excluded.prev_ts, last_new = excluded.last_new",
        (company, json.dumps(counts), now, row["ts"] if row else None, json.dumps(new)))
    conn.commit()
    if row is not None or found:
        mark(conn, company, True)


def last_paste(conn, company: str) -> dict | None:
    row = conn.execute("SELECT * FROM pasted_pages WHERE company = ?", (company,)).fetchone()
    if not row:
        return None
    titles = json.loads(row["titles"])
    return {"ts": row["ts"], "prev_ts": row["prev_ts"], "total": sum(titles.values()),
            "new": json.loads(row["last_new"])}


def find(s: dict, company_slug: str) -> dict | None:
    """The Check page entry for this slug, from the companies checked by hand."""
    g = build(s)
    return next((i for i in g["first"] + g["later"] if i["slug"] == company_slug), None)
