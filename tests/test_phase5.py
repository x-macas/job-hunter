"""Phase 5: stats, streak, evening nudge, backups."""
import json
import tarfile
import time
from datetime import date, timedelta

import pytest
from test_phase1 import raw, settings

from jobhunter import backup, collect, stats, telegram, tracker, worker


def _jobs(db, n):
    collect.save(db, [raw(i, title="Software Engineer", company=f"Co{i}") for i in range(n)], settings())
    return [r[0] for r in db.execute("SELECT id FROM jobs ORDER BY id")]


def _midday(d: date) -> float:
    return time.mktime((d.year, d.month, d.day, 12, 0, 0, 0, 0, -1))


def test_streak_and_per_day(app):
    from jobhunter.db import get_db
    today = date.today()
    with app.app_context():
        db = get_db()
        ids = _jobs(db, 7)
        # goal 2: met 3 days ago, 2 days ago and yesterday; today only 1 so far
        days = [3, 3, 2, 2, 1, 1, 0]
        for job_id, ago in zip(ids, days):
            tracker.mark_applied(db, job_id, now=_midday(today - timedelta(days=ago)))
        assert stats.streak(db, 2) == 3
        tracker.mark_applied(db, _jobs(db, 8)[-1], now=_midday(today))
        assert stats.streak(db, 2) == 4                   # today met too
        assert stats.streak(db, 0) == 0
        series = stats.per_day(db, "SELECT applied_at FROM applications WHERE applied_at >= ?", 5)
        assert [v for _, v in series] == [0, 2, 2, 2, 2]


def test_funnel_counts_later_stages_as_earlier_too(app):
    from jobhunter.db import get_db
    with app.app_context():
        db = get_db()
        ids = _jobs(db, 4)
        for job_id, stage in zip(ids, ["applied", "emailed", "interview", "rejected"]):
            tracker.mark_applied(db, job_id)
            tracker.move(db, job_id, stage)
        assert stats.funnel(db) == [("Applied", 4), ("Emailed", 2), ("Replied", 1), ("Interview", 1), ("Offer", 0)]


def test_skill_gaps(app):
    from jobhunter.db import get_db
    with app.app_context():
        db = get_db()
        ids = _jobs(db, 3)
        collect.apply_rules(db, settings(), log=lambda m: None)
        for job_id, miss in zip(ids, [["Kubernetes", "Go"], ["kubernetes"], ["Go", "AWS"]]):
            db.execute("UPDATE jobs SET ai_json = ? WHERE id = ?", (json.dumps({"missing": miss}), job_id))
        db.commit()
        assert stats.skill_gaps(db)[:2] == [("Kubernetes", 2), ("Go", 2)]


def test_chart_geometry():
    series = [(date(2026, 11, 1) + timedelta(days=i), v) for i, v in enumerate([0, 3, 7, 1])]
    c = stats.columns(series)
    assert [b["label"] for b in c["bars"]] == [None, None, 7, None]       # only the peak is labelled
    assert c["bars"][0]["path"] == "" and all(b["path"] for b in c["bars"][1:])
    assert [g["v"] for g in c["grid"]] == [0, 2, 4, 6, 8]                  # clean gridlines
    assert stats._nice_max(13) == 20 and stats._nice_max(1) == 4 and stats._nice_max(0) == 4
    h = stats.hbars([("Applied", 4), ("Offer", 0)])
    assert h["rows"][1]["path"] == "" and h["rows"][0]["v"] == 4


def test_stats_page(logged_in, app):
    from jobhunter.db import get_db
    with app.app_context():
        db = get_db()
        ids = _jobs(db, 2)
        tracker.mark_applied(db, ids[0])
        db.execute("INSERT INTO skips (job_id, reason, ts) VALUES (?, '<b>Too senior</b>', ?)", (ids[1], time.time()))
        db.commit()
    html = logged_in.get("/stats").get_data(as_text=True)
    assert "Applications per day" in html and "<svg" in html and "Applied today" in html
    assert "&lt;b&gt;Too senior" in html and "style=" not in html.split("<main")[1]   # no inline styles (CSP)


@pytest.fixture
def backup_env(tmp_path, monkeypatch, app):
    data = tmp_path / "data"
    data.mkdir()
    (data / "resume.pdf").write_bytes(b"%PDF resume")
    (data / "workday_facets.json").write_text("{}")
    (data / "resume.tex").write_text(r"\documentclass{article}")
    monkeypatch.setattr(backup, "DB_PATH", app.config["DB_PATH"])
    monkeypatch.setattr(backup, "RESUME_PATH", data / "resume.pdf")
    monkeypatch.setattr(backup, "DATA_DIR", data)
    return tmp_path / "backups"


def test_backup_and_prune(backup_env):
    s = settings(backup_dir=str(backup_env), backup_keep=2)
    made = [backup.run(s, now=time.time() + i) for i in range(3)]
    left = sorted(p.name for p in backup_env.iterdir())
    assert left == sorted(p.name for p in made[1:])
    assert oct(backup_env.stat().st_mode)[-3:] == "700" and oct(made[-1].stat().st_mode)[-3:] == "600"
    with tarfile.open(made[-1]) as tar:
        assert set(tar.getnames()) == {"jobhunter.db", "resume.pdf", "resume.tex", "workday_facets.json"}
    assert backup.latest(s)[0] == made[-1]


@pytest.mark.parametrize("bad", ["/media/usb/backups", "/run/media/me/STICK", "~/Google Drive/jh", "~/Dropbox/jh"])
def test_backup_refuses_usb_and_cloud(bad):
    with pytest.raises(backup.BackupError):
        backup.folder(settings(backup_dir=bad))


def test_backup_button(logged_in, backup_env, monkeypatch):
    from conftest import csrf_from
    from jobhunter import settings_store
    monkeypatch.setattr(settings_store, "get_all", lambda conn, _orig=settings_store.get_all:
                        _orig(conn) | {"backup_dir": str(backup_env)})
    r = logged_in.post("/backup", data={"csrf": csrf_from(logged_in, "/stats")})
    assert "Backup saved" in logged_in.get(r.headers["Location"]).get_data(as_text=True)
    assert len(list(backup_env.iterdir())) == 1


def test_evening_nudge(app, monkeypatch):
    from jobhunter.db import get_db
    sent = []
    monkeypatch.setattr(telegram, "send", lambda text: sent.append(text) or True)
    s = settings(nudge_hour=19, goal_applications=3, goal_emails=5, hand_check_reminder=False)
    with app.app_context():
        db = get_db()
        _jobs(db, 2)
        collect.apply_rules(db, settings(), log=lambda m: None)
        assert worker.evening_nudge(db, s, hour=18) is None
        text = worker.evening_nudge(db, s, hour=19)
        assert worker.evening_nudge(db, s, hour=20) is None                    # once a day
    assert sent == [text] and "0 of 3 applications, 0 of 5 emails" in text and "2 jobs are waiting" in text


def test_daily_backup_once(app, backup_env, monkeypatch):
    from jobhunter.db import get_db
    s = settings(backup_dir=str(backup_env))
    with app.app_context():
        db = get_db()
        worker.daily_backup(db, s)
        worker.daily_backup(db, s)
    assert len(list(backup_env.iterdir())) == 1
