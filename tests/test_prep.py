"""Phase 4: company info and mock interview. Claude is faked."""
import time

from conftest import csrf_from
from test_phase1 import raw, settings

from jobhunter import ai, collect, prep

INFO = {"about": "Makes payment software.", "size": "about 3,000 people", "india": "Bengaluru",
        "fresher_pay": "about 20 LPA", "reputation": "Good learning, long hours.",
        "hiring_rounds": ["Online test", "2 DSA rounds", "Hiring manager"], "topics": ["Graphs", "SQL joins"],
        "sample_questions": ["Design a rate limiter"], "ask_them": ["What does a first month look like?"],
        "watch_out": ["<b>Bond</b> for service roles"]}


def _job(app):
    from jobhunter.db import get_db
    with app.app_context():
        db = get_db()
        collect.save(db, [raw(1, title="Software Engineer", company="Razorpay")], settings())
        return db.execute("SELECT id FROM jobs").fetchone()[0]


def test_company_info_cached(app, monkeypatch):
    from jobhunter.db import get_db
    calls = []
    monkeypatch.setattr(ai, "ask_json", lambda system, prompt, schema, model: calls.append(prompt) or dict(INFO))
    job_id = _job(app)
    with app.app_context():
        db = get_db()
        job = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        s = settings(company_info_days=30)
        assert prep.company_info(db, job, s) is None
        prep.make_company_info(db, job, s)
        assert prep.company_info(db, job, s)["about"] == "Makes payment software."
        db.execute("UPDATE company_info SET ts = ?", (time.time() - 31 * 86400,))
        assert prep.company_info(db, job, s) is None          # too old: ask again
    assert len(calls) == 1 and "<job>" in calls[0]


def test_prep_page_and_mock(logged_in, app, monkeypatch):
    replies = iter([
        dict(INFO),
        {"feedback": "", "score": None, "next_question": "Find a cycle in a linked list."},
        {"feedback": "Good: Floyd's idea. Missing: why it's O(1) space.", "score": 7,
         "next_question": "Tell me about your Flask project."},
    ])
    prompts = []
    monkeypatch.setattr(ai, "ask_json", lambda system, prompt, schema, model: prompts.append(prompt) or next(replies))
    job_id = _job(app)
    path = f"/job/{job_id}/prep"
    assert "Get company info" in logged_in.get(path).get_data(as_text=True)
    token = csrf_from(logged_in, path)
    logged_in.post(path, data={"csrf": token, "action": "info"})
    html = logged_in.get(path).get_data(as_text=True)
    assert "Makes payment software." in html and "2 DSA rounds" in html and "&lt;b&gt;Bond" in html
    logged_in.post(path, data={"csrf": token, "action": "mock_start"})
    assert "Find a cycle in a linked list." in logged_in.get(path).get_data(as_text=True)
    logged_in.post(path, data={"csrf": token, "action": "mock_answer", "answer": "Two pointers, slow and fast."})
    html = logged_in.get(path).get_data(as_text=True)
    assert "Two pointers, slow and fast." in html and "(7/10)" in html and "Tell me about your Flask project." in html
    assert "Average 7.0 / 10" in html
    assert "Answer: Two pointers, slow and fast." in prompts[-1]
    r = logged_in.post(path, data={"csrf": token, "action": "mock_answer", "answer": ""})
    assert "Write an answer first" in logged_in.get(r.headers["Location"]).get_data(as_text=True)
    logged_in.post(path, data={"csrf": token, "action": "mock_reset"})
    assert "Find a cycle" not in logged_in.get(path).get_data(as_text=True)


def test_prep_claude_error_is_shown(logged_in, app, monkeypatch):
    def broken(*a, **k):
        raise ai.AIError("Plan limit reached.")
    monkeypatch.setattr(ai, "ask_json", broken)
    job_id = _job(app)
    path = f"/job/{job_id}/prep"
    r = logged_in.post(path, data={"csrf": csrf_from(logged_in, path), "action": "info"})
    assert "Plan limit reached." in logged_in.get(r.headers["Location"]).get_data(as_text=True)
