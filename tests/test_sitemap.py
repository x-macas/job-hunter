"""Sitemap reader: robots.txt rules, reading titles and cities from job links, new links = new jobs."""
import time

import pytest

from test_phase1 import settings

from jobhunter import collect, handcheck
from jobhunter.sources import portals, robots
from jobhunter.sources.http import FetchError


def test_robots_longest_rule_wins():
    rules = robots.parse_rules("User-agent: *\nDisallow: /\nAllow: /careers\nAllow: /api/apply\n")
    assert robots.allowed_by(rules, "https://x.com/careers/sitemap_index.xml?domain=x.com")
    assert not robots.allowed_by(rules, "https://x.com/api/jobs")
    assert not robots.allowed_by(rules, "https://x.com/")


def test_robots_search_page_only():
    rules = robots.parse_rules("User-agent: *\nDisallow:/search-jobs/\n")
    assert robots.allowed_by(rules, "https://x.com/sitemap.xml")
    assert robots.allowed_by(rules, "https://x.com/job/noida/ai-engineer/1/2")
    assert not robots.allowed_by(rules, "https://x.com/search-jobs/India")


def test_robots_groups_wildcards_and_failures():
    text = ("User-agent: Googlebot\nAllow: /\n\nUser-agent: *\nDisallow: /*.pdf$\nDisallow: /private\n"
            "\nUser-agent: job-hunter\nUser-agent: other\nDisallow: /jobs\n")
    ours = robots.parse_rules(text)
    assert ours == [(False, "/jobs")]       # our own group beats "*"
    star = robots.parse_rules(text, agent="somebot")
    assert not robots.allowed_by(star, "https://x.com/a/b.pdf")
    assert robots.allowed_by(star, "https://x.com/a/b.pdf?x=1")
    assert not robots.allowed_by(star, "https://x.com/private/x")
    assert robots.allowed_by([], "https://x.com/anything")      # no robots.txt: allowed
    assert not robots.allowed_by(None, "https://x.com/anything")  # site error: not allowed


def test_sitemap_links_to_jobs():
    tb = portals.sitemap_job("https://c.com/job/greater-noida/ai-ml-engineer/34088/103229769312", "talentbrew")
    assert tb == ("103229769312", "AI ML Engineer", "Greater Noida")
    assert portals.sitemap_job("https://c.com/job/cambridge/software-engineer/1/2", "talentbrew") is None
    assert portals.sitemap_job("https://c.com/business/job_level/manager/34088/5", "talentbrew") is None
    rmk = portals.sitemap_job("https://c.com/Nomura/job/Mumbai-Quants-400076/1352661800/", "rmk")
    assert rmk == ("1352661800", "Quants", "Mumbai")
    sc = portals.sitemap_job("https://c.com/job/Bangalore-Senior-C%2B%2B-Developer-KA/1377786457/", "rmk")
    assert sc == ("1377786457", "Senior C++ Developer", "Bangalore")
    nd = portals.sitemap_job("https://c.com/job/New-Delhi-Software-Engineer/9/", "rmk")
    assert nd == ("9", "Software Engineer", "New Delhi")
    assert portals.sitemap_job("https://c.com/job/Seoul-Equity-Research-100768/1441661600/", "rmk") is None


SITE = {"type": "sitemap", "name": "Optum", "host": "c.com", "sitemap": "https://c.com/sitemap.xml",
        "style": "talentbrew", "page": "https://c.com/", "also": ["UnitedHealth Group"]}


def _fake_site(monkeypatch, tmp_path, links, robots_text="User-agent: *\nDisallow:/search-jobs/\n"):
    xml = "<urlset>" + "".join(f"<url><loc>{u}</loc></url>" for u in links) + "</urlset>"
    monkeypatch.setattr(portals, "_get_text", lambda url: xml)
    monkeypatch.setattr(robots, "rules_for", lambda host: robots.parse_rules(robots_text))
    monkeypatch.setattr(portals, "READS_FILE", tmp_path / "reads.json")


def test_sitemap_reader_keeps_india_jobs_on_the_site(monkeypatch, tmp_path):
    _fake_site(monkeypatch, tmp_path, [
        "https://c.com/job/noida/software-engineer/1/11",
        "https://c.com/job/dallas/software-engineer/1/12",
        "https://evil.example/job/noida/software-engineer/1/13",
        "https://c.com/search-jobs/noida/software-engineer/1/14",
        "https://c.com/job/pune/data-engineer/1/15?a=1&amp;b=2",
    ])
    jobs = portals.fetch_portal(SITE)
    assert [(j.source_id, j.title, j.location) for j in jobs] == [
        ("c.com:11", "Software Engineer", "Noida, India"), ("c.com:15", "Data Engineer", "Pune, India")]
    assert jobs[1].url.endswith("?a=1&b=2")
    assert not portals.sitemap_due(SITE, 12)
    assert portals.sitemap_due(SITE, 12, now=time.time() + 12 * 3600)


def test_sitemap_reader_obeys_robots(monkeypatch, tmp_path):
    _fake_site(monkeypatch, tmp_path, ["https://c.com/job/noida/software-engineer/1/11"],
               robots_text="User-agent: *\nDisallow: /\n")
    with pytest.raises(FetchError):
        portals.fetch_portal(SITE)
    assert portals.sitemap_due(SITE, 12)  # nothing read, so still due


def test_new_sitemap_link_is_a_new_job_today(app, monkeypatch, tmp_path):
    from jobhunter.db import get_db
    now = time.time()
    s = settings()
    _fake_site(monkeypatch, tmp_path, ["https://c.com/job/noida/software-engineer/1/11"])
    with app.app_context():
        db = get_db()
        collect.save(db, portals.fetch_portal(SITE), s, now=now - 2 * 86400)   # first read: the baseline
        _fake_site(monkeypatch, tmp_path, ["https://c.com/job/noida/software-engineer/1/11",
                                           "https://c.com/job/bengaluru/backend-engineer/1/22"])
        collect.save(db, portals.fetch_portal(SITE), s, now=now)
        collect.apply_rules(db, s, log=lambda m: None, now=now)
        groups = handcheck.new_today(db, s, now=now + 60)
    assert [g["name"] for g in groups] == ["Optum"]
    assert [j["title"] for j in groups[0]["dev"]] == ["Backend Engineer"]
    assert groups[0]["url"] == "https://careers.unitedhealthgroup.com/"  # the real Optum entry


def test_sitemap_companies_leave_the_hand_check_list():
    g = handcheck.build(settings(big_companies=["Optum", "UnitedHealth Group", "Nomura", "Google"], watch_list=[]))
    assert {i["name"] for i in g["auto"]} == {"Optum", "UnitedHealth Group", "Nomura"}
    assert [i["name"] for i in g["first"]] == ["Google"]


def test_sitemaps_read_only_when_due(monkeypatch):
    seen = []
    monkeypatch.setattr(portals, "default_portals", lambda: [SITE, {"type": "amazon", "name": "Amazon"}])
    monkeypatch.setattr(portals, "fetch_portal", lambda p, quick=False: seen.append(p["name"]) or [])
    monkeypatch.setattr(collect, "all_boards", lambda s: [])
    monkeypatch.setattr(portals, "due", lambda p, hours: False)
    collect.fetch_all(settings(), log=lambda m: None, quick=True)
    assert seen == ["Amazon"]


PAGE_1 = """Search jobs\nSoftware Engineering (120)\nSoftware Engineer\nBengaluru, India · Posted 2 days ago
Backend Developer\nHyderabad\nSales Manager\nMumbai\n"""
PAGE_2 = PAGE_1.replace("(120)", "(124)") + """Software Engineer\nNoida · Posted today\nSenior Staff Engineer\nPune
Software Engineer II\nBengaluru\n"""


def test_paste_shows_only_new_titles(app):
    from jobhunter.db import get_db
    s = settings(hide_level_2_plus=True)
    with app.app_context():
        db = get_db()
        handcheck.paste(db, s, "Google", PAGE_1, now=1000)
        first = handcheck.last_paste(db, "Google")
        assert first["prev_ts"] is None and first["total"] == 2 and first["new"] == []
        handcheck.paste(db, s, "Google", PAGE_2, now=2000)
        last = handcheck.last_paste(db, "Google")
        assert [(j["title"], j["fits"]) for j in last["new"]] == [
            ("Software Engineer", True), ("Software Engineer II", False)]
        assert last["new"][0]["near"] == "Noida · Posted today"
        assert last["prev_ts"] == 1000
        assert "Google" in handcheck.done_today(db)


def test_paste_page_flow(logged_in, app):
    from conftest import csrf_from
    from jobhunter.db import get_db
    assert "Paste page, see what" in logged_in.get("/check").get_data(as_text=True)
    path = "/check/paste/Google"
    token = csrf_from(logged_in, path)
    logged_in.post(path, data={"csrf": token, "page": PAGE_1})
    assert "First paste saved" in logged_in.get(path).get_data(as_text=True)
    r = logged_in.post(path, data={"csrf": token, "page": PAGE_2 + "<script>alert(1)</script> Software Engineer"})
    assert r.status_code == 302
    html = logged_in.get(path).get_data(as_text=True)
    assert "3 new since" in html and "Noida · Posted today" in html and "&lt;script&gt;" in html
    assert "<script>alert(1)" not in html
    assert logged_in.post(path, data={"page": PAGE_1}).status_code in (400, 403)   # no CSRF token
    assert logged_in.get("/check/paste/Not-A-Company").status_code == 302
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM pasted_pages").fetchone()[0] == 1


FT_PAGE = """<a href="/jobs/8RrzHwmKvc5h/software-engineer-backend" class="heading" data-portal-title="x"
 data-portal-location="Mumbai, India" data-portal-job-type="2" data-portal-remote-location=false>
 <div class="row"><div class="job-list-info"><div class="job-title">Software Engineer - Backend</div></div></div></a>
<a href="/jobs/HVivFopqkiC1/voice-ai" class="heading" data-portal-location="" data-portal-remote-location=true>
 <div class="job-title">Voice AI Engineer &amp; Agent</div></a>"""


def test_freshteam_board(monkeypatch):
    from jobhunter.sources import boards
    monkeypatch.setattr(boards, "fetch_bytes", lambda url: FT_PAGE.encode())
    monkeypatch.setattr(robots, "rules_for", lambda host: robots.parse_rules("User-agent: *\nAllow: /jobs\nDisallow: /\n"))
    jobs = boards.fetch_board({"ats": "freshteam", "slug": "haptik", "name": "Haptik"})
    assert [(j.source_id, j.title, j.location, j.remote) for j in jobs] == [
        ("haptik:8RrzHwmKvc5h", "Software Engineer - Backend", "Mumbai, India", False),
        ("haptik:HVivFopqkiC1", "Voice AI Engineer & Agent", "", True)]
    assert jobs[0].url == "https://haptik.freshteam.com/jobs/8RrzHwmKvc5h/software-engineer-backend"
    monkeypatch.setattr(robots, "rules_for", lambda host: robots.parse_rules("User-agent: *\nDisallow: /\n"))
    with pytest.raises(FetchError):
        boards.fetch_board({"ats": "freshteam", "slug": "haptik", "name": "Haptik"})


def test_sitemap_more_link_styles():
    jid = portals.sitemap_job("https://jobs.renesas.com/job/aws-network-engineer-in-bengaluru-ka-india-jid-3274", "jid")
    assert jid == ("3274", "AWS Network Engineer", "Bengaluru")
    assert portals.sitemap_job("https://jobs.renesas.com/job/radio-engineer-in-kallithea-greece-jid-7543", "jid") is None
    team = portals.sitemap_job("https://www.optiver.com/join-us/jobs/technology/mumbai/data-center-engineer/", "team")
    assert team == ("technology/mumbai/data-center-engineer", "Data Center Engineer", "Mumbai")
    assert portals.sitemap_job("https://www.optiver.com/join-us/jobs/technology/london/software-engineer/", "team") is None


def test_sitemap_avature_style():
    a = portals.sitemap_job("https://bloomberg.avature.net/careers/JobDetail/Senior-Software-Engineer-BPaaS-Pune/45852", "avature")
    assert a == ("45852", "Senior Software Engineer BPaaS", "Pune")
    assert portals.sitemap_job("https://bloomberg.avature.net/careers/JobDetail/Terminal-Sales-Account-Manager/46347", "avature") is None
    nd = portals.sitemap_job("https://x.avature.net/careers/JobDetail/Software-Engineer-New-Delhi/7", "avature")
    assert nd == ("7", "Software Engineer", "New Delhi")


def test_polite_sites_follow_the_setting(monkeypatch, tmp_path):
    seen = []
    polite = dict(SITE, name="Flipkart", host="f.com", polite=True)
    monkeypatch.setattr(portals, "default_portals", lambda: [polite])
    monkeypatch.setattr(portals, "fetch_portal", lambda p, quick=False: seen.append(p["name"]) or [])
    monkeypatch.setattr(portals, "READS_FILE", tmp_path / "reads.json")
    monkeypatch.setattr(collect, "all_boards", lambda s: [])
    collect.fetch_all(settings(read_no_bots_sites=False), log=lambda m: None, quick=True)
    assert seen == []
    collect.fetch_all(settings(read_no_bots_sites=True), log=lambda m: None, quick=True)
    assert seen == ["Flipkart"]
    collect.fetch_all(settings(read_no_bots_sites=True), log=lambda m: None, quick=True)
    assert seen == ["Flipkart"]                      # read once, then waits its hours


def test_polite_sitemap_skips_robots(monkeypatch, tmp_path):
    _fake_site(monkeypatch, tmp_path, ["https://c.com/job/noida/software-engineer/1/11"],
               robots_text="User-agent: *\nDisallow: /\n")
    assert [j.title for j in portals.fetch_portal(dict(SITE, polite=True))] == ["Software Engineer"]


AV_PAGE = """<h3><a class="link link_result" href="https://jobs.x.com/en_US/careers/JobDetail/Software-Engineer-I/214211">
 Software Engineer I </a></h3><div><span class="list-item-location">Hyderabad, India</span></div>
<h3><a class="link link_result" href="https://jobs.x.com/en_US/careers/JobDetail/Game-Designer/1">Game Designer</a></h3>
<span class="list-item-location">Austin, Texas</span>"""


def test_avature_search_reader(monkeypatch):
    pages = iter([AV_PAGE, AV_PAGE])
    monkeypatch.setattr(portals, "_get_text", lambda url: next(pages))
    monkeypatch.setattr(robots, "rules_for", lambda host: [])
    jobs = portals.fetch_portal({"type": "avature", "name": "EA", "host": "jobs.x.com", "base": "https://jobs.x.com/en_US/careers"})
    assert [(j.title, j.location, j.source_id) for j in jobs] == [("Software Engineer I", "Hyderabad, India", "jobs.x.com:214211")]


CARDS = """<div class="card card-job" data-id="11"><h2 class="card-title"><a class="stretched-link" href="/in/jobs/11/sde/">SDE &amp; Backend</a></h2>
<ul><li class="list-inline-item"><span>Technology</span> <span>Posted Nov 26</span></li><li class="list-inline-item"> Chennai, TN </li></ul></div>
<div class="card card-job" data-id="12"><h2 class="card-title"><a href="/in/jobs/12/x/">Analyst</a></h2><li class="list-inline-item">Bangalore, KA</li></div>"""


def test_cards_reader(monkeypatch):
    monkeypatch.setattr(portals, "_get_text", lambda url: CARDS)
    monkeypatch.setattr(robots, "rules_for", lambda host: [])
    jobs = portals.fetch_portal({"type": "cards", "name": "Fid", "host": "j.com", "list": "https://j.com/in/jobs/", "country": "India"})
    assert [(j.title, j.location, j.url) for j in jobs] == [("SDE & Backend", "Chennai, TN", "https://j.com/in/jobs/11/sde/"),
                                                            ("Analyst", "Bangalore, KA", "https://j.com/in/jobs/12/x/")]
    assert jobs[0].posted_at and not jobs[1].posted_at


def test_mynexthire_and_recruiterbox(monkeypatch):
    from jobhunter.sources import boards
    monkeypatch.setattr(robots, "rules_for", lambda host: [])
    monkeypatch.setattr(boards, "_post_json", lambda url, body: {"reqDetailsBOList": [
        {"reqId": 7, "reqTitle": "SDE 1", "expMin": 0, "expMax": 1, "locationList": [{"office": "Bangalore"}],
         "jdDisplay": "Build APIs.", "approvedOn": "2026-11-20T05:23:41.966+0000", "employmentType": "full_time"}]})
    j = boards.fetch_board({"ats": "mynexthire", "slug": "acme", "name": "Acme"})[0]
    assert (j.title, j.location, j.source_id) == ("SDE 1", "Bangalore", "acme:7")
    assert j.description.startswith("Experience: 0-1 years") and j.posted_at
    monkeypatch.setattr(boards, "fetch_json", lambda url: [
        {"id": 9, "hash_id": "ab", "title": "Backend Engineer", "allows_remote": False, "position_type": "Full-time",
         "location": {"city": "Bengaluru", "state": "Karnataka", "country": "India"}, "description": "<p>Go</p>"}])
    r = boards.fetch_board({"ats": "recruiterbox", "slug": "1", "name": "Ex", "page": "https://ex.com/careers/"})[0]
    assert (r.title, r.location, r.url, r.description) == ("Backend Engineer", "Bengaluru, Karnataka, India",
                                                           "https://ex.com/careers/#ab", "Go")


def test_jsonlist_reader(monkeypatch):
    monkeypatch.setattr(portals, "_req", lambda url, body=None: {"data": {"jobs": [
        {"id": "A1", "title": "SDET 1", "excerpt": "QA · 📍 Noida 🕐 Full-Time", "jobType": "full_time", "description": "<p>Tests</p>"},
        {"id": "A2", "title": "Intern", "excerpt": "Remote", "jobType": "intern"}]}})
    monkeypatch.setattr(robots, "rules_for", lambda host: [])
    jobs = portals.fetch_portal({"type": "jsonlist", "name": "LT", "host": "x.com", "url": "https://x.com/jobs", "items": "data.jobs",
                                 "id": "id", "title": "title", "location": "regex:excerpt:📍\\s*([^🕐·]+)",
                                 "description": "description", "job_type_field": "jobType", "page": "https://lt.com/careers#{id}",
                                 "country": "India"})
    assert [(j.title, j.location, j.job_type, j.url) for j in jobs] == [
        ("SDET 1", "Noida", "full_time", "https://lt.com/careers#A1"), ("Intern", "India", "intern", "https://lt.com/careers#A2")]


def test_pagelist_reader(monkeypatch):
    page = ('<li class="role" data-loc="Mumbai"><a href="jd.php?id=1">Quant Developer</a></li>'
            '<li class="role" data-loc="Gift City"><a href="jd.php?id=2">Trader &amp; Analyst</a></li>'
            '<li class="role" data-loc="X"><a href="https://evil.com/jd.php?id=3">Elsewhere</a></li>'
            '<li class="role" data-loc="Mumbai"><a href="jd.php?id=1">Quant Developer</a></li>')
    monkeypatch.setattr(portals, "_get_text", lambda url, **k: page)
    monkeypatch.setattr(robots, "rules_for", lambda host: [])
    jobs = portals.fetch_portal({"type": "pagelist", "name": "IR", "host": "www.ir.in", "list": "https://www.ir.in/careers.php",
                                 "item": r'<li class="role" data-loc="(?P<place>[^"]*)"><a href="(?P<link>[^"]*?(?:id=(?P<id>\d+))?)">(?P<title>[^<]+)</a>',
                                 "country": "India"})
    assert [(j.title, j.location, j.url) for j in jobs] == [
        ("Quant Developer", "Mumbai", "https://www.ir.in/jd.php?id=1"),
        ("Trader & Analyst", "Gift City, India", "https://www.ir.in/jd.php?id=2")]
    monkeypatch.setattr(robots, "rules_for", lambda host: robots.parse_rules("User-agent: *\nDisallow: /careers\n"))
    with pytest.raises(FetchError):
        portals.fetch_portal({"type": "pagelist", "name": "IR", "host": "www.ir.in", "list": "https://www.ir.in/careers.php", "item": "x"})


def test_pagelist_several_pages(monkeypatch):
    pages = {"https://c.j.com/a": '<a href="jd?i=1">SDE ( 11 )</a><a href="jd?i=2">QA ( 12 )</a>',
             "https://c.j.com/b": '<a href="jd?i=1">SDE ( 11 )</a><a href="jd?i=3">Data ( 13 )</a>'}
    monkeypatch.setattr(portals, "_get_text", lambda url, **k: pages[url])
    monkeypatch.setattr(robots, "rules_for", lambda host: [])
    jobs = portals.fetch_portal({"type": "pagelist", "name": "J", "host": "c.j.com", "list": list(pages),
                                 "item": r'<a href="(?P<link>[^"]+)">(?P<title>[^<(]+?)\s*\(\s*(?P<id>\d+)\s*\)</a>'})
    assert [(j.title, j.url) for j in jobs] == [("SDE", "https://c.j.com/jd?i=1"), ("QA", "https://c.j.com/jd?i=2"),
                                                ("Data", "https://c.j.com/jd?i=3")]


def test_jsonlist_title_in_link(monkeypatch):
    monkeypatch.setattr(portals, "_req", lambda url, body=None: {"allJobs": [
        {"job_id": "a1", "job_title": "Sr. Devops Engineer / SRE", "location_city": ["Gurgaon", "Bangalore"]}]})
    monkeypatch.setattr(robots, "rules_for", lambda host: [])
    j = portals.fetch_portal({"type": "jsonlist", "name": "M", "host": "c.m.com", "url": "https://c.m.com/api/jobs", "items": "allJobs",
                              "id": "job_id", "title": "job_title", "location": "location_city",
                              "page": "https://c.m.com/opportunity/{id}/{title_slug}", "country": "India"})[0]
    assert j.location == "Gurgaon / Bangalore"
    assert j.url == "https://c.m.com/opportunity/a1/sr.-devops-engineer---sre"


def _ld_page(title, city, country="India"):
    return (f'<html><script type="application/ld+json">{{"@context":"https://schema.org","@type":"JobPosting",'
            f'"title":"{title}","datePosted":"2026-11-20","jobLocation":{{"@type":"Place","address":'
            f'{{"addressLocality":"{city}","addressCountry":"{country}"}}}}}}</script></html>')


def test_job_pages_style_opens_each_new_page_once(monkeypatch):
    site = {"type": "sitemap", "name": "TD", "host": "c.td.com", "sitemap": "https://c.td.com/sitemap.xml",
            "style": "jsonld", "job_link": r"/jobs/(\d+)/", "pages_per_run": 2, "title_tail": 2}
    xml = "<urlset>" + "".join(f"<url><loc>https://c.td.com/{u}</loc></url>" for u in
                               ["", "jobs/1/sde", "jobs/2/sre", "jobs/3/qa", "private/jobs/4/x", "jobs/5/x"]) + "</urlset>"
    pages = {"https://c.td.com/jobs/1/sde": _ld_page("Software Engineer", "Hyderabad"),
             "https://c.td.com/jobs/2/sre": _ld_page("SRE", "San Diego", "United States"),
             "https://c.td.com/jobs/3/qa": _ld_page("QA &amp; Test - IT - Pune, India", "Pune"), "https://c.td.com/jobs/5/x": None}
    opened = []

    def get(url):
        if url.endswith("sitemap.xml"):
            return xml
        opened.append(url)
        if pages[url] is None:
            raise FetchError(f"{url}: HTTP 404")
        return pages[url]
    monkeypatch.setattr(portals, "_get_text", get)
    monkeypatch.setattr(robots, "rules_for", lambda host: robots.parse_rules("User-agent: *\nDisallow: /private/\n"))
    monkeypatch.setattr(portals.time, "sleep", lambda s: None)
    jobs = portals.fetch_portal(site)
    assert opened == ["https://c.td.com/jobs/1/sde", "https://c.td.com/jobs/2/sre"]     # only 2 per read
    assert [(j.title, j.location, j.source_id) for j in jobs] == [("Software Engineer", "Hyderabad, India", "c.td.com:1")]
    jobs = portals.fetch_portal(site)
    assert opened[2:] == ["https://c.td.com/jobs/3/qa", "https://c.td.com/jobs/5/x"]    # never the blocked one, never twice
    assert [j.title for j in jobs] == ["Software Engineer", "QA & Test"] and jobs[1].posted_at
    portals.fetch_portal(site)
    assert len(opened) == 4


def test_rippling_board(monkeypatch):
    from jobhunter.sources import boards
    calls = []

    def fake(url):
        calls.append(url)
        if url.endswith("/jobs"):
            return [{"uuid": "u1", "name": "Software Engineer", "url": "https://ats.rippling.com/acme/jobs/u1",
                     "workLocation": {"label": "Bangalore, India"}},
                    {"uuid": "u2", "name": "SRE", "url": "https://ats.rippling.com/acme/jobs/u2", "workLocation": None}]
        return {"description": {"company": "<p>About us</p>", "role": "<p>Build <b>APIs</b></p>"}}
    monkeypatch.setattr(boards, "fetch_json", fake)
    jobs = boards.fetch_board({"ats": "rippling", "slug": "acme", "name": "Acme"})
    assert [(j.title, j.location, j.source_id) for j in jobs] == [("Software Engineer", "Bangalore, India", "acme:u1"),
                                                                  ("SRE", "", "acme:u2")]
    assert boards.rippling_description("acme", "u1") == "Build APIs"
    assert handcheck.source_url({"ats": "rippling", "slug": "acme", "name": "Acme"}) == "https://ats.rippling.com/acme/jobs"


KULA = """<div class="chakra-card css-1"><span class="css-3snkxd">Engineering</span>
<p class="chakra-text css-a">Software Development Engineer 1</p><svg></svg><p class="chakra-text css-b">Bengaluru, Karnataka, India</p>
<p class="chakra-text css-c">Full time<!-- --> • On-Site</p><a href="/acme/5565-software-development-engineer-1">Apply Now</a></div>
<div class="chakra-card css-1"><p class="chakra-text css-a">Intern &amp; Trainee</p><p class="chakra-text css-b">Remote, India</p>
<p class="chakra-text css-c">Internship • Remote</p><a href="/acme/77-intern">Apply</a></div>
<div class="chakra-card css-1"><p class="chakra-text">Made with Kula</p><a href="https://kula.ai">x</a></div>"""


def test_kula_board(monkeypatch):
    from jobhunter.sources import boards
    monkeypatch.setattr(boards, "fetch_bytes", lambda url: KULA.encode())
    monkeypatch.setattr(robots, "rules_for", lambda host: robots.parse_rules("User-agent: *\nDisallow: /api/\n"))
    jobs = boards.fetch_board({"ats": "kula", "slug": "acme", "name": "Acme"})
    assert [(j.title, j.location, j.url, j.remote) for j in jobs] == [
        ("Software Development Engineer 1", "Bengaluru, Karnataka, India", "https://careers.kula.ai/acme/5565-software-development-engineer-1", False),
        ("Intern & Trainee", "Remote, India", "https://careers.kula.ai/acme/77-intern", True)]
    assert jobs[1].job_type == "intern"
    with pytest.raises(FetchError):
        boards.fetch_board({"ats": "kula", "slug": "../api", "name": "Bad"})


def test_zohorecruit_board(monkeypatch):
    import html as h
    import json as js

    from jobhunter.sources import boards
    data = [{"id": "1", "Posting_Title": "Software Engineer", "City": "Bengaluru", "Country": "India", "Publish": True,
             "Job_Type": "Full time"},
            {"id": "2", "Posting_Title": "Hidden", "City": "Pune", "Publish": False},
            {"id": "3", "Job_Opening_Name": "SDE Intern", "Publish": "True", "Remote_Job": True}]
    page = f'<input type="hidden" value="{h.escape(js.dumps(data))}" id="jobs">'
    monkeypatch.setattr(boards, "fetch_bytes", lambda url: page.encode())
    monkeypatch.setattr(boards, "check_public_url", lambda url: url)
    monkeypatch.setattr(robots, "rules_for", lambda host: robots.parse_rules("User-agent: *\nAllow: /jobs\nDisallow: /\n"))
    jobs = boards.fetch_board({"ats": "zohorecruit", "slug": "acme.zohorecruit.in", "name": "Acme", "country": "India"})
    assert [(j.title, j.location, j.url) for j in jobs] == [
        ("Software Engineer", "Bengaluru, India", "https://acme.zohorecruit.in/jobs/Careers/1/Software-Engineer"),
        ("SDE Intern", "India (Remote)", "https://acme.zohorecruit.in/jobs/Careers/3/SDE-Intern")]
    with pytest.raises(FetchError):
        boards.fetch_board({"ats": "zohorecruit", "slug": "a/../b", "name": "Bad"})


def test_greenhouse_eu_boards(monkeypatch):
    from jobhunter.sources import boards
    calls = []
    monkeypatch.setattr(boards, "fetch_json", lambda url: calls.append(url) or {"jobs": [], "content": "<p>Hi</p>"})
    boards.fetch_board({"ats": "greenhouse", "slug": "qb@eu", "name": "QB"})
    boards.greenhouse_description("qb@eu", "7")
    boards.fetch_board({"ats": "greenhouse", "slug": "acme", "name": "Acme"})
    assert calls == ["https://boards.eu.greenhouse.io/v1/boards/qb/jobs", "https://boards.eu.greenhouse.io/v1/boards/qb/jobs/7",
                     "https://boards-api.greenhouse.io/v1/boards/acme/jobs"]
    assert handcheck.source_url({"ats": "greenhouse", "slug": "qb@eu", "name": "QB"}) == "https://job-boards.eu.greenhouse.io/qb"


def test_job_pages_from_a_list_page(monkeypatch):
    site = {"type": "sitemap", "name": "EC", "host": "c.ec.in", "list_page": "https://c.ec.in/", "sitemap": "https://c.ec.in/",
            "style": "jsonld", "job_link": r"/jobs/([a-z0-9-]+)/$", "country": "India"}
    home = '<a href="/jobs/sde/">SDE</a> <a href="https://c.ec.in/jobs/sde/">again</a> <a href="https://evil.com/jobs/x/">x</a>'
    page = '<script type="application/ld+json">{"@type":"JobPosting","title":"SDE"}</script>'
    monkeypatch.setattr(portals, "_get_text", lambda url: home if url == "https://c.ec.in/" else page)
    monkeypatch.setattr(robots, "rules_for", lambda host: [])
    monkeypatch.setattr(portals.time, "sleep", lambda s: None)
    assert [(j.title, j.location, j.url) for j in portals.fetch_portal(site)] == [("SDE", "India", "https://c.ec.in/jobs/sde/")]


def test_pinpoint_and_gem_boards(monkeypatch):
    from jobhunter.sources import boards
    monkeypatch.setattr(robots, "rules_for", lambda host: [])

    def fake(url):
        if "pinpointhq" in url:
            return {"data": [{"id": "1", "title": "AI SDE 2", "url": "https://h.pinpointhq.com/en/postings/x",
                              "location": {"city": "Bengaluru", "name": "India"}, "workplace_type": "onsite",
                              "description": "<p>Build</p>", "employment_type_text": "Full Time"}]}
        return [{"id": "g1", "title": "Forward Deployed Engineer", "absolute_url": "https://jobs.gem.com/p/g1",
                 "location": {"name": "Bangalore, India"}, "location_type": "hybrid", "content_plain": "Ship it",
                 "first_published_at": "2026-11-20T00:00:00Z", "employment_type": "full_time"}]
    monkeypatch.setattr(boards, "fetch_json", fake)
    p = boards.fetch_board({"ats": "pinpoint", "slug": "h", "name": "H"})[0]
    assert (p.title, p.location, p.url, p.description) == ("AI SDE 2", "Bengaluru, India", "https://h.pinpointhq.com/en/postings/x", "Build")
    g = boards.fetch_board({"ats": "gem", "slug": "p", "name": "P"})[0]
    assert (g.title, g.location, g.url, g.description) == ("Forward Deployed Engineer", "Bangalore, India", "https://jobs.gem.com/p/g1", "Ship it")
    assert g.posted_at
    with pytest.raises(FetchError):
        boards.fetch_board({"ats": "gem", "slug": "../x", "name": "Bad"})


def test_recruitee_board(monkeypatch):
    from jobhunter.sources import boards
    monkeypatch.setattr(robots, "rules_for", lambda host: [])
    seen = []

    def fake(url):
        seen.append(url)
        return {"offers": [
            {"id": 7, "title": "Backend Engineer", "city": "bengaluru", "country": "India", "remote": False,
             "careers_url": "https://jobs.z.com/o/be", "status": "published", "description": "<p>APIs</p>",
             "requirements": "<p>Go</p>", "published_at": "2026-11-20 10:00:00 UTC", "employment_type_code": "fulltime_permanent"},
            {"id": 8, "title": "Old", "status": "closed"}]}
    monkeypatch.setattr(boards, "fetch_json", fake)
    jobs = boards.fetch_board({"ats": "recruitee", "slug": "z", "name": "Z"})
    assert seen == ["https://z.recruitee.com/api/offers/"]
    assert len(jobs) == 1
    j = jobs[0]
    assert (j.title, j.location, j.url, j.description) == ("Backend Engineer", "Bengaluru, India", "https://jobs.z.com/o/be", "APIs\n\nGo")
    assert j.posted_at
    with pytest.raises(FetchError):
        boards.fetch_board({"ats": "recruitee", "slug": "a.b", "name": "Bad"})


def test_keka_board(monkeypatch):
    from jobhunter.sources import boards
    monkeypatch.setattr(robots, "rules_for", lambda host: robots.parse_rules("User-agent: *\nDisallow: /\nAllow: /careers\n"))
    seen = []

    def fake(url):
        seen.append(url)
        return [{"id": 95, "title": "Backend &amp; Infra Engineer", "description": "<p>Go</p>", "experience": "0 - 1",
                 "jobLocations": [{"city": "Bengaluru", "countryName": "India"}, {"name": "Pune", "countryName": "India"}],
                 "jobType": 2, "publishedOn": "2026-11-11T06:27:24.503Z"}]
    monkeypatch.setattr(boards, "fetch_json", fake)
    jobs = boards.fetch_board({"ats": "keka", "slug": "t", "name": "T"})
    assert seen == ["https://t.keka.com/careers/api/jobs/default/active"]
    j = jobs[0]
    assert (j.title, j.location, j.url) == ("Backend & Infra Engineer", "Bengaluru, India; Pune, India",
                                            "https://t.keka.com/careers/jobdetails/95")
    assert j.description == "Experience: 0 - 1 years\n\nGo" and j.posted_at


def test_pyjamahr_board(monkeypatch):
    from jobhunter.sources import boards
    monkeypatch.setattr(robots, "rules_for", lambda host: [])
    seen = []

    def fake(url):
        seen.append(url)
        if url.endswith("page=1&is_careers_page=true"):
            return {"next": "p2", "results": [
                {"id": 1, "slug": "sde-1", "title": "SDE 1", "location": "Bengaluru, Karnataka, India",
                 "workplace_type": "ON_SITE", "min_experience": 0.0, "max_experience": 1.0},
                {"id": 2, "slug": "../x", "title": "Odd link", "location": "Pune"}]}
        return {"next": None, "results": [{"id": 3, "slug": "qa", "title": "QA", "location": "India", "workplace_type": "REMOTE"}]}
    monkeypatch.setattr(boards, "fetch_json", fake)
    jobs = boards.fetch_board({"ats": "pyjamahr", "slug": "k", "uuid": "8B92017E1E", "name": "K"})
    assert len(seen) == 2 and "company_uuid=8B92017E1E" in seen[0]
    assert [(j.title, j.url, j.description) for j in jobs] == [
        ("SDE 1", "https://jobs.pyjamahr.com/k/sde-1", "Experience: 0 - 1 years"),
        ("QA", "https://jobs.pyjamahr.com/k/qa", None)]
    assert jobs[1].location == "India (Remote)"
    with pytest.raises(FetchError):
        boards.fetch_board({"ats": "pyjamahr", "slug": "k", "uuid": "a&b=c", "name": "Bad"})


def test_jazzhr_board(monkeypatch):
    from jobhunter.sources import boards
    monkeypatch.setattr(robots, "rules_for", lambda host: [])
    item = ('<li class="list-group-item"><h3 class=\'list-group-item-heading\'><a href="https://{h}.applytojob.com/apply/{i}/x">'
            ' {t} </a></h3><ul class=\'list-inline list-group-item-text\'><li><i class=\'fa fa-map-marker\'></i>{p}</li></ul></li>')
    page = ("<ul class='list-group'>" + item.format(h="jar", i="Ab1", t="SDE &amp; Intern", p="Bengaluru, Karnataka, India")
            + item.format(h="other", i="Zz9", t="Not ours", p="Pune") + item.format(h="jar", i="Ab1", t="Dup", p="X") + "</ul></div>")
    monkeypatch.setattr(boards, "fetch_bytes", lambda url, **k: page.encode())
    jobs = boards.fetch_board({"ats": "jazzhr", "slug": "jar", "name": "Jar"})
    assert [(j.title, j.location, j.url) for j in jobs] == [
        ("SDE & Intern", "Bengaluru, Karnataka, India", "https://jar.applytojob.com/apply/Ab1/x")]


def test_job_pages_labelled_fields(monkeypatch):
    site = {"type": "sitemap", "name": "AV", "host": "c.av.com", "sitemap": "https://c.av.com/s.xml", "style": "jsonld",
            "job_link": r"/JobDetail/[^/]+/(\d+)$", "place_fields": ["City", "Country/Region"]}
    xml = "<urlset><url><loc>https://c.av.com/JobDetail/SDE/1</loc></url><url><loc>https://c.av.com/JobDetail/PM/2</loc></url>" \
          "<url><loc>https://c.av.com/JobDetail/Gone/3</loc></url><url><loc>https://c.av.com/JobDetail/Ops/4</loc></url></urlset>"
    field = '<div class="article__content__view__field__label" > {} </div> <div class="article__content__view__field__value"> {} </div>'
    pages = {"https://c.av.com/JobDetail/SDE/1": '<script type="application/ld+json">{"@type":"JobPosting","title":"SDE"}</script>'
             + field.format("City", "Bengaluru") + field.format("Country/Region", "India"),
             "https://c.av.com/JobDetail/PM/2": '<meta property="og:title" content="PM" />' + field.format("City", "Pune"),
             "https://c.av.com/JobDetail/Gone/3": None,
             "https://c.av.com/JobDetail/Ops/4": "<html>no title here</html>"}

    def get(url):
        if url.endswith("s.xml"):
            return xml
        if pages[url] is None:
            raise FetchError(f"{url}: HTTP 404")
        return pages[url]
    monkeypatch.setattr(portals, "_get_text", get)
    monkeypatch.setattr(robots, "rules_for", lambda host: [])
    monkeypatch.setattr(portals.time, "sleep", lambda s: None)
    jobs = portals.fetch_portal(site)
    assert [(j.title, j.location) for j in jobs] == [("SDE", "Bengaluru, India"), ("PM", "Pune")]


def test_sap_style_careers_site(monkeypatch):
    rows = ('{\\"id\\":\\"744000163994949\\",\\"title\\":\\"Senior Developer\\",\\"url\\":\\"/en/jobs/744000163994949/senior-developer/\\",'
            '\\"score\\":1.9,\\"teams\\":\\"IT\\",\\"locations\\":\\"Bangalore, India\\",\\"displayDate\\":\\"2026-11-27T09:26:16.27Z\\"}')
    pages = {1: rows, 2: rows, 3: ""}
    asked = []

    def get(url):
        asked.append(url)
        return pages[int(url.rsplit("=", 1)[1])]
    monkeypatch.setattr(portals, "_get_text", get)
    monkeypatch.setattr(robots, "rules_for", lambda host: robots.parse_rules("User-agent: *\nDisallow: /*?q=\n"))
    jobs = portals.fetch_portal({"type": "srsite", "name": "SAP", "host": "jobs.sap.com", "base": "https://jobs.sap.com/en/jobs/"})
    assert [(j.title, j.location, j.url) for j in jobs] == [
        ("Senior Developer", "Bangalore, India", "https://jobs.sap.com/en/jobs/744000163994949/senior-developer/")]
    assert jobs[0].posted_at and asked[0] == "https://jobs.sap.com/en/jobs/?country=India&page=1" and len(asked) == 2


def test_job_pages_honor_crawl_delay(monkeypatch):
    waits = []
    site = {"type": "sitemap", "name": "K", "host": "j.k.com", "sitemap": "https://j.k.com/s.xml", "style": "jsonld",
            "job_link": r"/jobs/(\d+)$", "delay": 5, "max_pages": 2}
    xml = "<urlset>" + "".join(f"<url><loc>https://j.k.com/jobs/{i}</loc></url>" for i in range(5)) + "</urlset>"
    monkeypatch.setattr(portals, "_get_text", lambda url: xml if url.endswith("s.xml") else _ld_page("SDE", "Pune"))
    monkeypatch.setattr(robots, "rules_for", lambda host: [])
    monkeypatch.setattr(portals.time, "sleep", waits.append)
    assert len(portals.fetch_portal(site)) == 2 and waits == [5.0, 5.0]


def test_jibe_careers_site(monkeypatch):
    asked = []

    def req(url, body=None):
        asked.append(url)
        return {"jobs": [{"data": {"req_id": "92517", "slug": "92517", "title": "SoC Engineer", "full_location": "Hyderabad, India",
                                   "posted_date": "2026-11-27T06:50:00+0000", "description": "<p>Chips</p>",
                                   "employment_type": "FULL_TIME"}}]}
    monkeypatch.setattr(portals, "_req", req)
    monkeypatch.setattr(robots, "rules_for", lambda host: [])
    j = portals.fetch_portal({"type": "jibe", "name": "AMD", "host": "careers.amd.com"})[0]
    assert (j.title, j.location, j.url, j.description) == ("SoC Engineer", "Hyderabad, India",
                                                          "https://careers.amd.com/careers-home/jobs/92517?lang=en-us", "Chips")
    assert j.posted_at and "location=India" in asked[0] and len(asked) == 1


def test_eightfold_careers_site(monkeypatch):
    asked, waits = [], []
    page = {"data": {"positions": [{"id": 5631, "name": "Software Engineer", "locations": ["India,  Karnataka,Bangalore"],
                                    "postedTs": 1795772274, "positionUrl": "/careers/job/5631", "workLocationOption": "onsite"}]
                     + [{"id": 100 + i, "name": f"Job {i}", "locations": ["Pune,MH,India"]} for i in range(9)]}}
    monkeypatch.setattr(portals, "_req", lambda url, body=None: asked.append(url) or (page if len(asked) == 1 else {"data": {"positions": []}}))
    monkeypatch.setattr(robots, "rules_for", lambda host: robots.parse_rules("User-agent: *\nDisallow: /\nAllow: /api/pcsx\n"))
    monkeypatch.setattr(portals.time, "sleep", waits.append)
    jobs = portals.fetch_portal({"type": "eightfold", "name": "E", "host": "jobs.e.com", "domain": "e.com", "delay": 5})
    assert (jobs[0].title, jobs[0].location, jobs[0].url) == ("Software Engineer", "India, Karnataka, Bangalore", "https://jobs.e.com/careers/job/5631")
    assert len(jobs) == 10 and len(asked) == 2 and waits == [5.0] and "location=India" in asked[0]
