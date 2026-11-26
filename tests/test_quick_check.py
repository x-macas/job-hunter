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
