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


def source_url(src: dict) -> str:
    """The careers page for a source the website reads."""
    kind = src.get("type") or src.get("ats")
    slug = src.get("slug", "")
    if kind == "workday":
        return f"https://{src['host']}/{src['site']}"
    if kind == "oraclehcm":
        return f"https://{src['host']}/hcmUI/CandidateExperience/en/sites/{src['site']}/requisitions"
    if kind == "amazon":
        return "https://www.amazon.jobs/en/search?base_query=software&country=IND&sort=recent"
    if kind == "atlassian":
        return "https://www.atlassian.com/company/careers/all-jobs"
    return {
        "greenhouse": f"https://job-boards.greenhouse.io/{slug}",
        "lever": f"https://jobs.lever.co/{slug}",
        "ashby": f"https://jobs.ashbyhq.com/{slug}",
        "smartrecruiters": f"https://jobs.smartrecruiters.com/{slug}",
        "workable": f"https://apply.workable.com/{slug}/",
        "jobvite": f"https://jobs.jobvite.com/{slug}/jobs",
    }.get(kind, "")


def slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", name)


def _search_url(company: str) -> str:
    return "https://www.google.com/search?" + urllib.parse.urlencode({"q": f"{company} careers India software jobs"})


def build(s: dict) -> dict:
    """Returns {'first': [...], 'later': [...], 'auto': [...]}.
    first = blocked / no-bots companies, later = not readable yet, auto = read by the website."""
    sources = boards.default_boards() + boards.boards_from_setting(s["extra_boards"]) + portals.default_portals()
    links = _links()
    out = {"first": [], "later": [], "auto": []}
    for name in dict.fromkeys(s["big_companies"] + s["watch_list"]):
        if not name.strip():
            continue
        srcs = [x for x in sources if rules.name_in(x["name"], [name]) or rules.name_in(name, [x["name"]])]
        item = {"name": name, "slug": slug(name), "watch": name in s["watch_list"]}
        if srcs:
            item["url"] = source_url(srcs[0])
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
             AND j.source NOT IN ('remotive', 'remoteok', 'hn', 'adzuna')""", (since, since)).fetchall()
    groups: dict[str, dict] = {}
    for r in rows:
        if r["posted_at"] is None and r["first_seen"] <= r["site_first"] + 3600:
            continue
        if not parse.location_ok(r["location"] or "", bool(r["remote"]), s["locations"]):
            continue
        g = groups.setdefault(r["company"], {"name": r["company"], "slug": slug(r["company"]),
                                             "dev": [], "other": 0, "fits": 0})
        if rules.is_role(r["title"], s):
            g["dev"].append(r)
            g["fits"] += bool(r["rule_ok"])
        else:
            g["other"] += 1
    for g in groups.values():
        g["dev"].sort(key=lambda r: (not r["rule_ok"], -(r["posted_at"] or r["first_seen"])))
    return sorted((g for g in groups.values() if g["dev"]),
                  key=lambda g: (-g["fits"], -len(g["dev"]), g["name"].lower()))
