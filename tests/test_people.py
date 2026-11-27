"""Contact search: company website addresses and Hunter.io. Network calls are faked."""
import json

import pytest
from conftest import csrf_from
from test_phase1 import raw, settings

from jobhunter import collect, people
from jobhunter.sources.http import FetchError

SITE = """<html><body>Write to careers@acme.in or HR [at] acme.in? hr@acme.in
Sales: sales@acme.in, CEO personal: ravi.k@acme.in, jobs@gmail.com, talent.india@acme.in</body></html>"""
HUNTER = {"data": {"domain": "acme.in", "emails": [
    {"value": "priya@acme.in", "first_name": "Priya", "last_name": "S", "position": "Talent Acquisition Lead",
     "confidence": 95, "verification": {"status": "valid"}},
    {"value": "arun@acme.in", "first_name": "Arun", "last_name": "M", "position": "Engineering Manager", "confidence": 88},
    {"value": "dev@acme.in", "first_name": "Dev", "last_name": "", "position": "Software Engineer", "confidence": 85},
    {"value": "guess@acme.in", "first_name": "G", "position": "Recruiter", "confidence": 40},
    {"value": "not an email", "confidence": 99}]}}


@pytest.fixture
def net(monkeypatch):
    calls = []

    def fetch_bytes(url, timeout=30, max_bytes=0):
        calls.append(url)
        if "acme.in/careers" in url and url.startswith("https://www."):
            return SITE.encode()
        raise FetchError(f"{url}: 404")

    def fetch_json(url, timeout=30):
        calls.append(url)
        if "domain-search" in url:
            return HUNTER
        return {"data": {}}
    monkeypatch.setattr(people, "fetch_bytes", fetch_bytes)
    monkeypatch.setattr(people, "fetch_json", fetch_json)
    monkeypatch.setattr(people, "check_public_url", lambda u: u)
    return calls


def test_website_finds_only_published_job_addresses(net):
    got = {p["email"] for p in people.from_website("acme.in")}
    assert got == {"careers@acme.in", "hr@acme.in", "talent.india@acme.in"}   # not sales@, not a person, not gmail


def test_hunter_results_filtered_and_sorted_into_kinds(net):
    people.KEY_FILE.write_text(json.dumps({"key": "a" * 40}))
    got = {p["email"]: p["kind"] for p in people.from_hunter("acme.in", 80)}
    assert got == {"priya@acme.in": "hr", "arun@acme.in": "manager", "dev@acme.in": "employee"}


def test_no_hunter_key_no_hunter_call(net):
    assert people.from_hunter("acme.in", 80) == []
    assert not any("hunter" in c for c in net)


def test_company_domain():
    job = {"url": "https://job-boards.greenhouse.io/acme/jobs/1", "apply_url": None, "company": "Acme"}
    assert people.company_domain(job, {"acme": {"url": "https://careers.acme.co.in/jobs"}}) == "acme.co.in"
    assert people.company_domain({"url": "https://www.acme.in/careers/1", "apply_url": None, "company": "Acme"}) == "acme.in"
    assert people.company_domain(job, {}) is None          # no key, no careers link


def test_private_addresses_never_fetched(monkeypatch):
    seen = []
    monkeypatch.setattr(people, "fetch_bytes", lambda url, **k: seen.append(url) or b"")
    assert people.from_website("localhost") == [] and seen == []
    assert people.from_website("127.0.0.1.nip.io") == [] and seen == []


def _job(app, url="https://www.acme.in/careers/sde"):
    from jobhunter.db import get_db
    with app.app_context():
        db = get_db()
        j = raw(1, company="Acme", title="Software Engineer")
        j.url = url
        collect.save(db, [j], settings())
        return db.execute("SELECT id FROM jobs").fetchone()[0]


def test_find_people_page_flow(logged_in, app, net):
    people.KEY_FILE.write_text(json.dumps({"key": "a" * 40}))
    job_id = _job(app)
    path = f"/job/{job_id}/apply"
    token = csrf_from(logged_in, path)
    logged_in.post(f"/job/{job_id}/find-people", data={"csrf": token})
    html = logged_in.get(path).get_data(as_text=True)
    assert "careers@acme.in" in html and "Priya S" in html and "Engineering Manager" in html
    n = len(net)
    logged_in.get(path)
    logged_in.post(f"/job/{job_id}/find-people", data={"csrf": token})      # cached: no new lookups
    assert len(net) == n
    from jobhunter.db import get_db
    with app.app_context():
        fid = get_db().execute("SELECT id FROM found_people WHERE email = 'priya@acme.in'").fetchone()[0]
    logged_in.post(f"/job/{job_id}/contacts", data={"csrf": token, "found": str(fid)})
    with app.app_context():
        c = get_db().execute("SELECT * FROM contacts WHERE email = 'priya@acme.in'").fetchone()
    assert c["kind"] == "hr" and "Hunter.io" in c["how"]
    assert "a" * 40 not in logged_in.get("/connections").get_data(as_text=True)


def test_found_people_only_for_that_company(logged_in, app, net):
    from jobhunter.db import get_db
    job_id = _job(app)
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO found_people (company, domain, email, kind, ts) VALUES ('Other', 'o.com', 'x@o.com', 'hr', 1)")
        db.commit()
        other = db.execute("SELECT id FROM found_people").fetchone()[0]
    r = logged_in.post(f"/job/{job_id}/contacts", data={"csrf": csrf_from(logged_in, f"/job/{job_id}/apply"),
                                                         "found": str(other)})
    assert "That suggestion is gone." in logged_in.get(r.headers["Location"]).get_data(as_text=True)


def test_hunter_key_checks(monkeypatch):
    with pytest.raises(people.PeopleError, match="doesn't look like"):
        people.save_hunter_key("nope")

    def refuse(url, timeout=30):
        raise FetchError(url)
    monkeypatch.setattr(people, "fetch_json", refuse)
    with pytest.raises(people.PeopleError, match="said no") as e:
        people.save_hunter_key("b" * 40)
    assert "b" * 40 not in str(e.value) and not people.KEY_FILE.exists()
