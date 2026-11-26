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
