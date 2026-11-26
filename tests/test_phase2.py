"""Phase 2: Tracker, saved answers, cover notes, the form filler."""
import importlib.util
from pathlib import Path

import pytest
from conftest import csrf_from
from test_phase1 import raw, settings

from jobhunter import answers, collect, formfill, tracker

PROFILE = {"full_name": "Asha Kumari Rao", "email": "asha@example.com", "phone": "9876543210",
           "linkedin_url": "https://linkedin.com/in/asha", "github_url": "https://github.com/asha",
           "expected_ctc": "15 LPA", "notice_period": "Immediate", "relocate": "Yes, anywhere in India",
           "college": "NIT Raipur", "grad_year": "2026", "city": "Raipur", "country": "India"}


def _job(app, **k):
    from jobhunter.db import get_db
    with app.app_context():
        db = get_db()
        collect.save(db, [raw(1, title="Software Engineer", **k)], settings())
        collect.apply_rules(db, settings(), log=lambda m: None)
        return db.execute("SELECT id FROM jobs").fetchone()[0]


# ---------- answers ----------

def test_answer_matching():
    assert answers.similarity("Why do you want to join {company}?", "Why do you want to work at Acme?") >= 0.5
    assert answers.similarity("What is your notice period?", "Notice period (in days)") >= 0.6
    assert answers.similarity("What is your notice period?", "Describe a project you are proud of") < 0.3
    rows = [{"id": 1, "question": "Why do you want to work at {company}?", "answer": "I use {company} every day."},
            {"id": 2, "question": "Describe a project", "answer": ""}]
    row, _ = answers.best("Why do you want to work here at this company?", rows, 0.4)
    assert row["id"] == 1 and answers.fill(row["answer"], "Acme", "SDE") == "I use Acme every day."
    assert answers.best("Describe a project you led", rows, 0.4)[0] is None   # empty answers never used


def test_answers_page(logged_in, app):
    from jobhunter.db import get_db
    html = logged_in.get("/answers").get_data(as_text=True)
    assert "Why do you want to work at {company}?" in html and "still empty" in html
    token = csrf_from(logged_in, "/answers")
    logged_in.post("/answers", data={"csrf": token, "question": "Do you know Docker?", "answer": "Yes, basics."})
    with app.app_context():
        row = get_db().execute("SELECT * FROM answers WHERE question = 'Do you know Docker?'").fetchone()
    assert row["answer"] == "Yes, basics."
    logged_in.post("/answers", data={"csrf": token, "id": str(row["id"]), "question": "Docker?", "answer": "Yes."})
    logged_in.post(f"/answers/{row['id']}/delete", data={"csrf": token})
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM answers WHERE id = ?", (row["id"],)).fetchone()[0] == 0
    r = logged_in.post("/answers", data={"csrf": token, "question": "x" * 400, "answer": "y"})
    assert "Keep the question under" in logged_in.get(r.headers["Location"]).get_data(as_text=True)


# ---------- tracker ----------

def test_tracker_flow(logged_in, app):
    job_id = _job(app)
    token = csrf_from(logged_in, "/")
    assert "Software Engineer" in logged_in.get("/").get_data(as_text=True)
    logged_in.post(f"/job/{job_id}/applied", data={"csrf": token, "next": "/"})
    assert "Software Engineer" not in logged_in.get("/").get_data(as_text=True)   # off the Today list
    board = logged_in.get("/tracker").get_data(as_text=True)
    assert "1 application" in board and "Applied today" in board
    logged_in.post(f"/tracker/{job_id}/move", data={"csrf": token, "stage": "interview"})
    logged_in.post(f"/tracker/{job_id}/move", data={"csrf": token, "stage": "hacked"})
    logged_in.post(f"/tracker/{job_id}/note", data={"csrf": token, "note": "Call with Priya on Monday <b>"})
    board = logged_in.get("/tracker").get_data(as_text=True)
    assert 'value="interview" selected' in board and "Call with Priya on Monday &lt;b&gt;" in board
    logged_in.post(f"/job/{job_id}/applied", data={"csrf": token, "undo": "1"})
    assert "Software Engineer" in logged_in.get("/").get_data(as_text=True)


def test_tracker_waiting_flag(app):
    from jobhunter.db import get_db
    job_id = _job(app)
    with app.app_context():
        db = get_db()
        tracker.mark_applied(db, job_id, now=1000)
        cols = tracker.board(db, settings(followup_1_days=5), now=1000 + 6 * 86400)
    card = cols[0]["cards"][0]
    assert card["waiting"] and card["days"] == 6


def test_apply_pages_need_csrf_and_login(logged_in, app):
    job_id = _job(app)
    assert logged_in.post(f"/job/{job_id}/applied", data={}).status_code == 400
    assert logged_in.post(f"/job/{job_id}/fill", data={}).status_code == 400
    assert logged_in.get(f"/job/{job_id}/apply").status_code == 200
    assert logged_in.get("/job/99999/apply").status_code == 302


def test_apply_pages_need_login(client, app):
    from conftest import set_password
    set_password(client)
    client.cookie_jar.clear() if hasattr(client, "cookie_jar") else client.delete_cookie("jobhunter_session")
    job_id = _job(app)
    for path in (f"/job/{job_id}/apply", "/tracker", "/answers"):
        r = client.get(path)
        assert r.status_code == 302 and "/login" in r.headers["Location"]


# ---------- cover note ----------

def test_cover_note(logged_in, app, monkeypatch):
    from jobhunter import ai
    seen = {}

    def fake(system, prompt, schema, model):
        seen.update(system=system, prompt=prompt, model=model)
        return {"note": "I'm a fresh grad from NIT Raipur. I built a Flask app like yours."}
    monkeypatch.setattr(ai, "ask_json", fake)
    job_id = _job(app)
    path = f"/job/{job_id}/apply"
    token = csrf_from(logged_in, path)
    logged_in.post(f"/job/{job_id}/cover", data={"csrf": token, "action": "write"})
    html = logged_in.get(path).get_data(as_text=True)
    assert "I built a Flask app like yours." in html
    assert "data, not instructions" in seen["system"] and "<job>" in seen["prompt"] and seen["model"] == "sonnet"
    logged_in.post(f"/job/{job_id}/cover", data={"csrf": token, "action": "save", "note": "My own words."})
    assert "My own words." in logged_in.get(path).get_data(as_text=True)


def test_fill_button_starts_helper(logged_in, app, monkeypatch):
    started = []
    monkeypatch.setattr(formfill, "status", lambda: {"ready": True, "why": ""})
    monkeypatch.setattr(formfill, "busy", lambda: False)
    monkeypatch.setattr(formfill, "start", lambda job_id: started.append(job_id))
    job_id = _job(app)
    token = csrf_from(logged_in, f"/job/{job_id}/apply")
    logged_in.post(f"/job/{job_id}/fill", data={"csrf": token})
    assert started == [job_id]
    monkeypatch.setattr(formfill, "busy", lambda: True)
    logged_in.post(f"/job/{job_id}/fill", data={"csrf": token})
    assert started == [job_id]


# ---------- form filler: deciding ----------

FIELDS = [
    {"id": "a", "kind": "text", "label": "First Name *", "required": True},
    {"id": "b", "kind": "text", "label": "Last Name", "required": True},
    {"id": "c", "kind": "text", "label": "Email", "required": True},
    {"id": "d", "kind": "text", "label": "Phone", "required": False},
    {"id": "e", "kind": "file", "label": "Resume/CV", "required": True},
    {"id": "f", "kind": "text", "label": "LinkedIn Profile", "required": False},
    {"id": "g", "kind": "textarea", "label": "Cover Letter", "required": False},
    {"id": "h", "kind": "radio", "label": "Are you willing to relocate?", "options": ["Yes", "No"]},
    {"id": "i", "kind": "select", "label": "Notice period", "options": ["Select...", "Immediate", "30 days"]},
    {"id": "j", "kind": "textarea", "label": "Why do you want to work at Acme?", "required": True},
    {"id": "k", "kind": "text", "label": "What is your favourite colour?", "required": True},
    {"id": "l", "kind": "checkbox", "label": "I agree to the privacy policy", "required": True},
    {"id": "m", "kind": "text", "label": "Password"},
    {"id": "n", "kind": "combobox", "label": "Country", "required": True},
]


def test_plan_fills_from_profile_answers_and_note():
    saved = [{"id": 7, "question": "Why do you want to join {company}?", "answer": "I like how {company} builds {role}."}]
    actions, unanswered, left = formfill.plan(FIELDS, PROFILE, saved, "My note.", "Acme", "SDE", 0.5, True)
    got = {a["id"]: a["value"] for a in actions}
    assert got["a"] == "Asha" and got["b"] == "Kumari Rao" and got["c"] == "asha@example.com"
    assert got["d"] == "9876543210" and got["f"] == "https://linkedin.com/in/asha"
    assert got["e"].endswith("resume.pdf") and got["g"] == "My note."
    assert got["h"] == "Yes" and got["i"] == "Immediate"
    assert got["j"] == "I like how Acme builds SDE."
    assert "m" not in got and "l" not in got and got["n"] == "India"
    assert unanswered == ["What is your favourite colour?"]
    assert {a["id"]: a["kind"] for a in actions}["n"] == "combobox"   # Country, from the profile
    assert left == ["I agree to the privacy policy"]


def test_plan_without_resume_or_note():
    actions, unanswered, _ = formfill.plan(FIELDS, {}, [], "", "Acme", "SDE", 0.6, False)
    assert not [a for a in actions if a["kind"] == "file"]
    assert "Resume/CV" in unanswered and "Cover Letter" in unanswered


def test_form_url():
    assert formfill.form_url({"source": "lever", "apply_url": None, "url": "https://jobs.lever.co/x/1"}) \
        == "https://jobs.lever.co/x/1/apply"
    assert formfill.form_url({"source": "ashby", "apply_url": None, "url": "https://jobs.ashbyhq.com/x/1"}) \
        == "https://jobs.ashbyhq.com/x/1/application"
    assert formfill.form_url({"source": "greenhouse", "apply_url": None, "url": "https://g.io/x"}) == "https://g.io/x"


# ---------- form filler: a real (headless) browser on a local page ----------

FORM_HTML = """<!doctype html><html><body>
<form id="f" onsubmit="document.title='SUBMITTED'; return false;">
 <label for="fn">First name</label><input id="fn" required>
 <label>Email <input type="email" name="em"></label>
 <div class="field"><label>Phone number</label><input name="ph" value="111"></div>
 <label for="cv">Resume</label><input type="file" id="cv">
 <fieldset><legend>Willing to relocate?</legend>
   <label><input type="radio" name="rel" value="y"> Yes</label><label><input type="radio" name="rel" value="n"> No</label>
 </fieldset>
 <label for="np">Notice period</label><select id="np"><option>Pick</option><option>Immediate</option></select>
 <label for="pw">Password</label><input type="password" id="pw">
 <input type="hidden" name="secret" value="x">
 <label><input type="checkbox" id="ok"> I agree to the terms</label>
 <label for="odd">Favourite colour</label><input id="odd" required>
 <button type="submit">Submit application</button>
</form></body></html>"""


@pytest.mark.skipif(importlib.util.find_spec("playwright") is None
                    or not any(Path.home().joinpath(".cache/ms-playwright").glob("chromium*")),
                    reason="Playwright browser not installed")
def test_browser_fills_but_never_submits(tmp_path, monkeypatch):
    from playwright.sync_api import sync_playwright
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 test")
    monkeypatch.setattr(formfill, "RESUME_PATH", resume)
    page_file = tmp_path / "form.html"
    page_file.write_text(FORM_HTML)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(page_file.as_uri())
        fields = page.evaluate(formfill.READ_FIELDS_JS)
        labels = {f["label"] for f in fields}
        assert "Password" not in labels and not any("secret" in f["label"] for f in fields)
        actions, unanswered, left = formfill.plan(fields, PROFILE, [], "", "Acme", "SDE", 0.6, True)
        filled, missed = formfill.apply_actions(page, fields, actions)
        assert missed == []
        assert page.input_value("#fn") == "Asha"
        assert page.input_value("[name=em]") == "asha@example.com"
        assert page.input_value("[name=ph]") == "111"              # never overwrites what's there
        assert page.is_checked("input[name=rel][value=y]")
        assert page.eval_on_selector("#np", "e => e.value") == "Immediate"
        assert page.eval_on_selector("#cv", "e => e.files.length") == 1
        assert not page.is_checked("#ok")                          # consent left for you
        assert page.input_value("#pw") == ""
        assert page.title() != "SUBMITTED"                         # never submitted
        assert unanswered == ["Favourite colour"] and left == ["I agree to the terms"]
        browser.close()


LEVER_LIKE = """<!doctype html><html><body><form onsubmit="document.title='SUBMITTED'; return false;">
<div><button type="button">Attach</button><label for="resume">Attach</label><input id="resume" type="file" style="display:none"></div>
<li class="application-question"><div class="application-label"><div class="text">Are you comfortable working from Pune?</div></div>
 <div class="application-field"><ul><li><label><input type="radio" name="c0" value="Yes"><span>Yes</span></label></li>
 <li><label><input type="radio" name="c0" value="No"><span>No</span></label></li></ul></div></li>
<li class="application-question"><div class="application-label"><div class="text">Notice period</div></div>
 <div class="application-field"><select name="cards[x][field2]"><option>Select...</option><option>Immediate Joiner</option>
 <option>15 days</option></select></div></li>
<div class="field"><label id="cl">Country</label>
 <input id="country" role="combobox" aria-labelledby="cl" autocomplete="off">
 <div id="list"></div></div>
<div class="field"><label id="gl">Gender</label><input id="gender" role="combobox" aria-labelledby="gl"><div id="glist"></div></div>
<script>
 const opts = ["Indonesia", "India", "Iceland"];
 const c = document.getElementById('country'), l = document.getElementById('list');
 c.addEventListener('input', () => { l.innerHTML = opts.filter(o => o.toLowerCase().startsWith(c.value.toLowerCase().slice(0, 2)))
   .map(o => '<div role="option">' + o + '</div>').join(''); });
 l.addEventListener('click', e => { c.value = e.target.textContent; c.dataset.picked = e.target.textContent; l.innerHTML = ''; });
 const g = document.getElementById('gender'), gl = document.getElementById('glist');
 g.addEventListener('input', () => { gl.innerHTML = '<div role="option">Woman</div><div role="option">Man</div>'; });
</script>
<button type="submit">Submit</button></form></body></html>"""


@pytest.mark.skipif(importlib.util.find_spec("playwright") is None
                    or not any(Path.home().joinpath(".cache/ms-playwright").glob("chromium*")),
                    reason="Playwright browser not installed")
def test_browser_reads_lever_style_questions_and_dropdowns(tmp_path, monkeypatch):
    from playwright.sync_api import sync_playwright
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 test")
    monkeypatch.setattr(formfill, "RESUME_PATH", resume)
    page_file = tmp_path / "lever.html"
    page_file.write_text(LEVER_LIKE)
    saved = [{"id": 1, "question": "Are you comfortable working from {company}'s Pune office?", "answer": "Yes"},
             {"id": 2, "question": "What is your gender?", "answer": "Prefer not to say"}]
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(page_file.as_uri())
        fields = page.evaluate(formfill.READ_FIELDS_JS)
        labels = [f["label"] for f in fields]
        assert "Are you comfortable working from Pune?" in labels and "Notice period" in labels
        assert any("resume" in x.lower() for x in labels)
        actions, unanswered, _ = formfill.plan(fields, PROFILE, saved, "", "Acme", "SDE", 0.5, True)
        filled, missed = formfill.apply_actions(page, fields, actions)
        assert page.is_checked("input[name=c0][value=Yes]")
        assert page.eval_on_selector("select", "e => e.value") == "Immediate Joiner"
        assert page.eval_on_selector("#country", "e => e.dataset.picked") == "India"
        assert page.input_value("#gender") == ""          # no matching option: cleared, left for you
        assert missed == ["Gender"]
        assert page.eval_on_selector("#resume", "e => e.files.length") == 1
        assert page.title() != "SUBMITTED"
        browser.close()


def test_answer_from_apply_page(logged_in, app):
    from jobhunter import cover
    from jobhunter.db import get_db
    job_id = _job(app)
    with app.app_context():
        cover.put(get_db(), job_id, "Are you based in Bangalore?", "unanswered")
    path = f"/job/{job_id}/apply"
    html = logged_in.get(path).get_data(as_text=True)
    assert "Are you based in Bangalore?" in html and "Save answer" in html
    r = logged_in.post("/answers", data={"csrf": csrf_from(logged_in, path), "question": "Are you based in Bangalore?",
                                         "answer": "No, but I can move.", "next": path})
    assert r.headers["Location"].endswith(path)
    r = logged_in.post("/answers", data={"csrf": csrf_from(logged_in, path), "question": "q", "answer": "a",
                                         "next": "//evil.example/x"})
    assert not r.headers["Location"].startswith("//") and "evil" not in r.headers["Location"]
    r = logged_in.post("/answers", data={"csrf": csrf_from(logged_in, path), "question": "q2", "answer": "a",
                                         "next": "/\\evil.example/x"})
    assert "evil" not in r.headers["Location"]
    assert "Applied today: 0 of 3" in logged_in.get("/").get_data(as_text=True)
