"""The 30-minute quick check: alert only new, fitting jobs from sites we already knew."""
import time

from test_phase1 import raw, settings

from jobhunter import collect, scoring, telegram, worker


def _setup(monkeypatch, batches):
    """Each call to fetch_all returns the next batch of jobs."""
    sent = []
    calls = iter(batches)
    monkeypatch.setattr(collect, "fetch_all", lambda s, log=print, quick=False, **k: next(calls))
    monkeypatch.setattr(scoring, "score_pending", lambda *a, **k: 0)
    monkeypatch.setattr(telegram, "send", lambda text: sent.append(text) or True)
    return sent


def test_quick_check_alerts_only_new_fitting_jobs(app, monkeypatch):
    from jobhunter.db import get_db
    now = time.time()
    s = settings(alerts_per_check=8, alert_max_age_days=2)
    old_post = raw(3, title="Software Engineer")
    old_post.posted_at = now - 10 * 86400
    sent = _setup(monkeypatch, [
        [raw(1)],                                   # first check: site is new, no alert
        [raw(1), raw(2, title="Software Engineer"),  # second check: 2 is new and fits
         raw(4, title="Senior Staff Engineer"),      # too senior, fails rules
         old_post],                                  # new to us but posted 10 days ago
        [raw(1), raw(2, title="Software Engineer")],  # third check: nothing new
    ])
    with app.app_context():
        db = get_db()
        worker.quick(db, s, now=now)
        assert sent == []
        worker.quick(db, s, now=now + 1800)
        assert len(sent) == 1 and "Software Engineer at Acme" in sent[0]
        worker.quick(db, s, now=now + 3600)
        assert len(sent) == 1


def test_quick_check_caps_alerts(app, monkeypatch):
    from jobhunter.db import get_db
    now = time.time()
    s = settings(alerts_per_check=2)
    sent = _setup(monkeypatch, [[raw(1)], [raw(1)] + [raw(i, title="Software Engineer") for i in range(10, 15)]])
    with app.app_context():
        db = get_db()
        worker.quick(db, s, now=now)
        worker.quick(db, s, now=now + 1800)
        assert len(sent) == 3 and sent[-1].startswith("3 more new jobs fit")
        assert db.execute("SELECT COUNT(*) FROM jobs WHERE alerted = 0 AND rule_ok = 1 AND source_id >= '10'").fetchone()[0] == 0


def test_check_page_lists_companies_and_ticks(logged_in):
    from conftest import csrf_from
    client = logged_in
    page = client.get("/check")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "Google" in html and "New on careers sites today" in html and "LinkedIn, last 24 h" not in html and "f_TPR" not in html
    assert "Read by the website" in html
    r = client.post("/check/tick", data={"csrf": csrf_from(client, "/check"), "company": "Google", "done": "1"})
    assert r.status_code == 302
    assert 'aria-pressed="true"' in client.get("/check").get_data(as_text=True)


def test_check_tick_ignores_unknown_company(logged_in, app):
    from conftest import csrf_from
    from jobhunter.db import get_db
    html = logged_in.get("/check").get_data(as_text=True)
    logged_in.post("/check/tick", data={"csrf": csrf_from(logged_in, "/check"), "company": "<script>", "done": "1"})
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM hand_checks").fetchone()[0] == 0


def test_handcheck_sorts_blocked_first():
    from test_phase1 import settings
    from jobhunter import handcheck
    g = handcheck.build(settings(big_companies=["Google", "Walmart Global Tech", "Zepto"], watch_list=[]))
    assert [i["name"] for i in g["first"]] == ["Google"]
    assert [i["name"] for i in g["auto"]] == ["Walmart Global Tech"]
    assert [i["name"] for i in g["later"]] == ["Zepto"]


def test_new_today_lists_fresh_careers_site_jobs(app):
    from test_phase1 import settings
    from jobhunter import handcheck
    from jobhunter.db import get_db
    now = time.time()
    s = settings()
    with app.app_context():
        db = get_db()
        first = raw(1, company="Walmart Global Tech")
        first.posted_at = now - 5 * 86400
        collect.save(db, [first], s, now=now - 5 * 86400)          # site known for days
        fresh = raw(2, company="Walmart Global Tech", title="Software Engineer")
        fresh.posted_at = None                                        # no date: first seen now
        not_dev = raw(3, company="Walmart Global Tech", title="Store Manager")
        not_dev.posted_at = now - 3600
        newsite = raw(4, company="Brand New Co", title="Software Engineer")
        newsite.posted_at = None                                      # new site, no date: not "new"
        collect.save(db, [fresh, not_dev, newsite], s, now=now)
        collect.apply_rules(db, s, log=lambda m: None, now=now)
        groups = handcheck.new_today(db, s, now=now + 60)
        assert [g["name"] for g in groups] == ["Walmart Global Tech"]
        assert [j["title"] for j in groups[0]["dev"]] == ["Software Engineer"]
        assert groups[0]["other"] == 1


def test_today_page_lists_every_passing_job_ranked(app, logged_in):
    import json as _json
    from test_phase1 import settings
    from jobhunter.db import get_db
    s = settings(daily_top_n=2)
    with app.app_context():
        db = get_db()
        collect.save(db, [raw(i, company=f"Co{i}", title="Software Engineer") for i in range(5)], s)
        collect.apply_rules(db, s, log=lambda m: None)
        for i, row in enumerate(db.execute("SELECT id FROM jobs ORDER BY id").fetchall()):
            if i < 4:  # the 5th stays unscored
                db.execute("UPDATE jobs SET ai_score = ?, ai_json = ? WHERE id = ?",
                           (50 + i * 10, _json.dumps({"company_type": "product"}), row["id"]))
        db.commit()
    html = logged_in.get("/").get_data(as_text=True)
    assert "5 jobs passed your rules" in html
    assert html.index("Co3") < html.index("Co2") < html.index("Co1") < html.index("Co0") < html.index("Co4")
    assert "Not scored yet" in html
