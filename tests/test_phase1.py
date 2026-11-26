import json
import time

import pytest
from conftest import csrf_from

from jobhunter import collect, parse, ranking, rules, settings_store
from jobhunter.sources import RawJob

DEFAULTS = {s.key: s.default for s in settings_store.SETTINGS}
NOW = time.time()


def settings(**changes):
    s = dict(DEFAULTS)
    s.update(blocked_companies=["Ethara AI"], skills=["Python"])
    s.update(changes)
    return s


def job(**k):
    base = dict(id=1, title="Software Engineer", company="Acme", location="Bengaluru, India", remote=0,
                posted_at=NOW - 3600, job_type="full_time", min_years=None, description="",
                salary_max_lpa=None, salary_min_lpa=None, ai_score=70, added_by="auto", rule_reason="")
    base.update(k)
    return base


# ---------- parsing ----------

@pytest.mark.parametrize("text, expected", [
    ("We need 0-1 years of experience", 0),
    ("3+ years of backend experience", 3),
    ("Minimum 2 years in Java", 2),
    ("1 to 3 yrs", 1),
    ("Freshers welcome", None),
    ("We have 2000 years of history and 5+ years experience", 5),
])
def test_min_years(text, expected):
    assert parse.min_years(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("12-18 LPA", (12, 18)),
    ("CTC: 10 LPA", (10, 10)),
    ("₹12,00,000 - ₹15,00,000", (12, 15)),
    ("$100k - $150k per year", (88, 132)),
    ("$40 per hour", (None, None)),
    ("", (None, None)),
])
def test_salary(text, expected):
    assert parse.salary_lpa(text, 88) == expected


@pytest.mark.parametrize("loc, ok", [
    ("Bengaluru, Karnataka", True),
    ("Remote", True),
    ("Remote - India", True),
    ("Remote (Worldwide)", True),
    ("Remote in New York, New Jersey", False),
    ("San Francisco, CA", False),
    ("Remote - US", False),
])
def test_location(loc, ok):
    assert parse.location_ok(loc, False, ["India", "Remote"]) is ok


def test_html_to_text_strips_scripts():
    text = parse.html_to_text("<p>Hello</p><script>alert(1)</script><ul><li>One</li></ul>")
    assert "alert" not in text and "Hello" in text and "- One" in text


# ---------- rules ----------

@pytest.mark.parametrize("changes, ok, reason_part", [
    ({}, True, ""),
    ({"company": "Ethara AI"}, False, "blocked"),
    ({"title": "Senior Software Engineer"}, False, "hidden word"),
    ({"title": "Software Engineer II"}, False, "level"),
    ({"title": "SDE 2"}, False, "level"),
    ({"title": "SDE-1"}, True, ""),
    ({"title": "Marketing Manager"}, False, "role"),
    ({"title": "Frontend Developer"}, False, "role not in your list"),
    ({"location": "London, UK"}, False, "location"),
    ({"posted_at": NOW - 10 * 86400}, False, "too old"),
    ({"min_years": 3}, False, "asks 3"),
    ({"job_type": "intern"}, False, "non-big"),
    ({"job_type": "intern", "company": "Google"}, True, ""),
    ({"salary_max_lpa": 6}, False, "below 10"),
    ({"description": "You must sign a service agreement of 2 years"}, False, "bond"),
])
def test_rules(changes, ok, reason_part):
    result, reason = rules.evaluate(job(**changes), settings(), NOW)
    assert result is ok, reason
    assert reason_part in reason


def test_company_name_matching():
    assert rules.name_in("Google India Pvt Ltd", ["Google"])
    assert not rules.name_in("Googly Games", ["Google India"])
    assert not rules.name_in("Ab", ["Abc Corp"])


# ---------- ranking ----------

def test_big_company_boost_and_tags():
    s = settings()
    score, tags = ranking.final_score(job(company="Stripe"), {"company_type": "big_tech"}, s, NOW)
    assert score == 70 + 15 + ranking.FRESH_BOOST
    assert "Big company" in tags and "NEW" in tags


def test_startup_unknown_pay_ranked_lower():
    score, tags = ranking.final_score(job(posted_at=None), {"company_type": "startup"}, settings(), NOW)
    assert score == 70 - ranking.UNKNOWN_PAY_PENALTY and "Pay unknown" in tags


def test_startup_low_pay_dropped():
    score, _ = ranking.final_score(job(salary_max_lpa=11), {"company_type": "startup"}, settings(), NOW)
    assert score is None


def test_ai_drops_non_dev_and_bond_and_senior():
    for ai in ({"is_dev_role": False}, {"has_bond": True}, {"needs_years": 3}):
        assert ranking.final_score(job(), ai, settings(), NOW)[0] is None


# ---------- saving, rules and picks together ----------

def raw(i, company="Acme", title="Backend Developer", **k):
    return RawJob(source="test", source_id=str(i), source_name="Test", company=company, title=title,
                  url=f"https://example.com/{i}", location="Remote", remote=True,
                  description="Python backend. 0-1 years.", posted_at=NOW - 3600, job_type="full_time", **k)


def test_pipeline_keeps_big_company_spots(app):
    from jobhunter.db import get_db
    with app.app_context():
        db = get_db()
        s = settings(daily_top_n=4, big_company_spots=2)
        jobs = [raw(i, company=f"Startup{i}") for i in range(6)] + [raw(10, company="Stripe"), raw(11, company="Google")]
        collect.save(db, jobs, s)
        collect.apply_rules(db, s, log=lambda m: None)
        for row in db.execute("SELECT id, company FROM jobs"):
            score = 40 if row["company"] in ("Stripe", "Google") else 90
            db.execute("UPDATE jobs SET ai_score = ?, ai_json = ? WHERE id = ?",
                       (score, json.dumps({"company_type": "product"}), row["id"]))
        db.commit()
        assert ranking.pick_today(db, s) == 4
        picked = {r[0] for r in db.execute("SELECT j.company FROM picks p JOIN jobs j ON j.id = p.job_id")}
        assert {"Stripe", "Google"} <= picked


def test_bad_links_never_saved(app):
    from jobhunter.db import get_db
    with app.app_context():
        db = get_db()
        bad = raw(1)
        bad.url = "javascript:alert(1)"
        good = raw(2, apply_url="javascript:alert(2)")
        assert len(collect.save(db, [bad, good], settings())) == 1
        assert db.execute("SELECT apply_url FROM jobs").fetchone()[0] is None


# ---------- web pages ----------

def test_today_page_shows_cards_and_skip_learns(app, logged_in):
    from jobhunter.db import get_db
    with app.app_context():
        db = get_db()
        s = settings()
        collect.save(db, [raw(1, company="Paylane", title="Backend Engineer")], s)
        collect.apply_rules(db, s, log=lambda m: None)
        db.execute("UPDATE jobs SET ai_score = 88, ai_json = ?", (json.dumps(
            {"why": "Python backend, fresher friendly", "missing": ["Docker"], "company_type": "product"}),))
        db.commit()
        ranking.pick_today(db, s)
    page = logged_in.get("/").get_data(as_text=True)
    assert "Backend Engineer · Paylane" in page and "Docker" in page

    token = csrf_from(logged_in, "/")
    logged_in.post("/job/1/skip", data={"csrf": token, "reason": "Not this company"})
    assert "Backend Engineer · Paylane" not in logged_in.get("/").get_data(as_text=True)
    with app.app_context():
        db = get_db()
        assert db.execute("SELECT status FROM jobs WHERE id = 1").fetchone()[0] == "skipped"
        assert "Paylane" in settings_store.get(db, "hidden_companies")
        assert db.execute("SELECT reason FROM skips").fetchone()[0] == "Not this company"


def test_skip_needs_known_reason(logged_in):
    token = csrf_from(logged_in, "/")
    logged_in.post("/job/1/skip", data={"csrf": token, "reason": "<script>"})
    assert "Pick a reason" in logged_in.get("/").get_data(as_text=True)


def test_add_link_refuses_private_addresses(logged_in):
    token = csrf_from(logged_in, "/")
    logged_in.post("/add", data={"csrf": token, "link": "http://127.0.0.1:8000/settings"})
    assert "private address" in logged_in.get("/").get_data(as_text=True)


def test_ai_model_must_be_known(logged_in):
    from test_settings import form_with_defaults
    token = csrf_from(logged_in, "/settings")
    data = form_with_defaults(ai_model="--dangerously-skip-permissions")
    page = logged_in.post("/settings", data={"csrf": token, **data}).get_data(as_text=True)
    assert "Choose one of" in page


# ---------- Telegram bot only listens to you ----------

def test_telegram_pairing_and_strangers_ignored(app, tmp_path, monkeypatch):
    from jobhunter import telegram, telegram_bot
    from jobhunter.db import get_db
    monkeypatch.setattr(telegram, "FILE", tmp_path / "telegram.json")
    telegram.store({"token": "1:x", "chat_id": None, "pair_code": "123456", "offset": 0})
    with app.app_context():
        db = get_db()
        assert telegram_bot.handle(db, 999, "000000") is None          # wrong code: silent
        assert "Connected" in telegram_bot.handle(db, 42, "123456")     # right code: paired
        assert telegram.load()["chat_id"] == 42
        assert telegram_bot.handle(db, 999, "/stop") is None            # stranger: ignored
        assert settings_store.get(db, settings_store.PAUSED_KEY) is False
        assert "Stopped" in telegram_bot.handle(db, 42, "/stop")        # you: works
        assert settings_store.get(db, settings_store.PAUSED_KEY) is True


def test_telegram_code_cancelled_after_10_wrong_tries(app, tmp_path, monkeypatch):
    from jobhunter import telegram, telegram_bot
    from jobhunter.db import get_db
    monkeypatch.setattr(telegram, "FILE", tmp_path / "telegram.json")
    telegram.store({"token": "1:x", "chat_id": None, "pair_code": "123456", "offset": 0})
    with app.app_context():
        db = get_db()
        for _ in range(10):
            telegram_bot.handle(db, 999, "111111")
        assert telegram_bot.handle(db, 42, "123456") is None
