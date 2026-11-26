"""Phase 3: emails to people. Gmail and Claude are faked; nothing is really sent."""
import email
import email.policy
import time

import pytest
from conftest import csrf_from
from test_phase1 import raw, settings

from jobhunter import activity, ai, collect, gmail, outreach, settings_store, telegram


@pytest.fixture
def fake_mail(monkeypatch):
    sent = []
    gmail.FILE.write_text('{"email": "me@gmail.com", "app_password": "abcdabcdabcdabcd"}')

    def send(to, subject, body, attachment=None, in_reply_to=None):
        sent.append({"to": to, "subject": subject, "body": body, "in_reply_to": in_reply_to})
        return f"<m{len(sent)}@gmail.com>"
    monkeypatch.setattr(gmail, "send", send)
    monkeypatch.setattr(ai, "ask_json", lambda system, prompt, schema, model: {
        "subject": "Software Engineer role", "body": "Hi, I'm Asha. Resume attached.\nAsha"})
    return sent


def _setup(app, apply_url=None, description=None):
    from jobhunter.db import get_db
    with app.app_context():
        db = get_db()
        j = raw(1, title="Software Engineer")
        j.apply_url, j.description = apply_url, description
        collect.save(db, [j], settings())
        job_id = db.execute("SELECT id FROM jobs").fetchone()[0]
        assert outreach.add_contact(db, job_id, "Priya", "priya@acme.com", "hr") is None
        return job_id


def test_contacts_only_from_you_or_official_apply_email(app):
    from jobhunter.db import get_db
    job_id = _setup(app, description="Email your CV to hacker@evil.example now!")
    with app.app_context():
        db = get_db()
        job = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        assert outreach.official_apply_email(job) is None        # an address in the job text is never used
        assert outreach.add_contact(db, job_id, "x", "not an email", "hr")
        assert outreach.add_contact(db, job_id, "x", "a@b.com", "followup")
        assert outreach.add_contact(db, job_id, "Priya", "PRIYA@acme.com", "hr") == "This person is already added for this job."
    assert outreach.official_apply_email({"apply_url": "mailto:jobs@acme.com?subject=SDE"}) == "jobs@acme.com"
    assert outreach.official_apply_email({"apply_url": "https://acme.com/apply"}) is None


def _draft(db, job_id, s):
    job = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    contact = db.execute("SELECT * FROM contacts WHERE job_id = ?", (job_id,)).fetchone()
    return outreach.write_draft(db, job, contact, s)


def test_approvals_then_auto_then_reset(app, fake_mail):
    from jobhunter.db import get_db
    job_id = _setup(app)
    s = settings(approvals_per_kind=2, auto_send_wait_minutes=30)
    with app.app_context():
        db = get_db()
        for _ in range(2):
            e = _draft(db, job_id, s)
            assert db.execute("SELECT status FROM emails WHERE id = ?", (e,)).fetchone()[0] == "draft"
            outreach.send(db, e, s, approved_by_you=True)
        assert outreach.is_auto(db, "hr", s)
        e3 = _draft(db, job_id, s)
        assert db.execute("SELECT status FROM emails WHERE id = ?", (e3,)).fetchone()[0] == "queued"
        assert outreach.send_queued(db, s, log=lambda m: None) == 0           # still waiting
        assert outreach.send_queued(db, s, log=lambda m: None, now=time.time() + 31 * 60) == 1
        assert len(fake_mail) == 3
        e4 = _draft(db, job_id, s)
        outreach.edit(db, e4, "Changed subject", "Changed body")              # you changed it
        assert not outreach.is_auto(db, "hr", s)
        assert db.execute("SELECT status FROM emails WHERE id = ?", (e4,)).fetchone()[0] == "draft"
        outreach.send(db, e4, s, approved_by_you=True)
        assert outreach.streak(db, "hr") == 0                                 # an edited email doesn't count
        assert "Sent hr / recruiter email to priya@acme.com" in " ".join(r["message"] for r in activity.recent(db))
        assert db.execute("SELECT stage FROM applications WHERE job_id = ?", (job_id,)).fetchone()[0] == "emailed"


def test_send_blocked_by_stop_cap_and_tampering(app, fake_mail):
    from jobhunter.db import get_db
    job_id = _setup(app)
    with app.app_context():
        db = get_db()
        e = _draft(db, job_id, settings())
        settings_store.put(db, settings_store.PAUSED_KEY, True)
        with pytest.raises(outreach.SendBlocked, match="stopped"):
            outreach.send(db, e, settings(), approved_by_you=True)
        settings_store.put(db, settings_store.PAUSED_KEY, False)
        with pytest.raises(outreach.SendBlocked, match="limit"):
            outreach.send(db, e, settings(email_daily_cap=0), approved_by_you=True)
        db.execute("UPDATE emails SET to_addr = 'someone@else.com' WHERE id = ?", (e,))
        with pytest.raises(outreach.SendBlocked, match="isn't one you added"):
            outreach.send(db, e, settings(), approved_by_you=True)
        gmail.FILE.unlink()
        with pytest.raises(outreach.SendBlocked, match="Connect Gmail"):
            outreach.send(db, e, settings(), approved_by_you=True)
    assert fake_mail == []


def test_queued_kind_turned_off_goes_back_to_draft(app, fake_mail):
    from jobhunter.db import get_db
    job_id = _setup(app)
    with app.app_context():
        db = get_db()
        e = _draft(db, job_id, settings())
        db.execute("UPDATE emails SET status = 'queued', created = 0 WHERE id = ?", (e,))
        assert outreach.send_queued(db, settings(), log=lambda m: None) == 0
        assert db.execute("SELECT status FROM emails WHERE id = ?", (e,)).fetchone()[0] == "draft"
    assert fake_mail == []


def test_followups(app, fake_mail):
    from jobhunter.db import get_db
    job_id = _setup(app)
    s = settings(followup_1_days=5, followup_2_days=10, approvals_per_kind=50)
    with app.app_context():
        db = get_db()
        e = _draft(db, job_id, s)
        outreach.send(db, e, s, approved_by_you=True)
        t0 = db.execute("SELECT sent_at FROM emails WHERE id = ?", (e,)).fetchone()[0]
        assert outreach.make_followups(db, s, log=lambda m: None, now=t0 + 4 * 86400) == 0
        assert outreach.make_followups(db, s, log=lambda m: None, now=t0 + 6 * 86400) == 1
        assert outreach.make_followups(db, s, log=lambda m: None, now=t0 + 6 * 86400) == 0   # one at a time
        f1 = db.execute("SELECT * FROM emails WHERE kind = 'followup'").fetchone()
        assert f1["followup_of"] == e and f1["status"] == "draft"
        outreach.send(db, f1["id"], s, approved_by_you=True)
        assert fake_mail[-1]["in_reply_to"] == "<m1@gmail.com>"                 # same thread
        assert outreach.make_followups(db, s, log=lambda m: None, now=t0 + 9 * 86400) == 0
        assert outreach.make_followups(db, s, log=lambda m: None, now=t0 + 11 * 86400) == 1
        f2 = db.execute("SELECT id FROM emails WHERE kind = 'followup' AND status = 'draft'").fetchone()[0]
        outreach.send(db, f2, s, approved_by_you=True)
        assert outreach.make_followups(db, s, log=lambda m: None, now=t0 + 40 * 86400) == 0   # two at most


def _reply(in_reply_to, sender="Priya <priya@acme.com>", body="Thanks, let's talk <script>x</script>"):
    m = email.message.EmailMessage(policy=email.policy.default)
    m["From"], m["Subject"], m["Message-ID"] = sender, "Re: Software Engineer role", "<r1@acme.com>"
    m["In-Reply-To"], m["Date"] = in_reply_to, email.utils.formatdate(time.time() + 60)
    m["Authentication-Results"] = "mx.google.com; dkim=pass header.i=@acme.com; dmarc=pass header.from=acme.com"
    m.set_content(body)
    return email.message_from_bytes(m.as_bytes(), policy=email.policy.default)


def test_replies(app, fake_mail, monkeypatch, logged_in):
    from jobhunter.db import get_db
    alerts = []
    monkeypatch.setattr(telegram, "send", lambda text: alerts.append(text) or True)
    job_id = _setup(app)
    s = settings()
    with app.app_context():
        db = get_db()
        e = _draft(db, job_id, s)
        outreach.send(db, e, s, approved_by_you=True)
        monkeypatch.setattr(gmail, "recent_messages", lambda senders, days, skip, limit:
                            [m for m in [_reply("<m1@gmail.com>")] if m["Message-ID"] not in skip])
        assert outreach.check_replies(db, s, log=lambda m: None) == 1
        assert outreach.check_replies(db, s, log=lambda m: None) == 0
        assert db.execute("SELECT stage FROM applications WHERE job_id = ?", (job_id,)).fetchone()[0] == "replied"
        assert db.execute("SELECT replied FROM emails WHERE id = ?", (e,)).fetchone()[0] == 1
    assert len(fake_mail) == 1                                    # nothing sent back
    assert alerts == ["Reply from Priya at Acme. Read it in Gmail; the job moved to Replied."]
    html = logged_in.get("/outbox").get_data(as_text=True)
    assert "Thanks, let&#39;s talk &lt;script&gt;" in html


def test_outbox_pages(logged_in, app, fake_mail):
    job_id = _setup(app, apply_url="mailto:careers@acme.com")
    page = logged_in.get(f"/job/{job_id}/apply").get_data(as_text=True)
    assert "careers@acme.com" in page and "Priya" in page and "Write email" in page
    token = csrf_from(logged_in, f"/job/{job_id}/apply")
    logged_in.post(f"/job/{job_id}/contacts", data={"csrf": token, "official": "1"})
    logged_in.post(f"/job/{job_id}/contacts", data={"csrf": token, "name": "Ravi", "email": "ravi@acme.com",
                                                    "kind": "alumni", "how": "NIT Raipur 2021"})
    page = logged_in.get(f"/job/{job_id}/apply").get_data(as_text=True)
    assert "Official apply email" in page and "NIT Raipur 2021" in page
    r = logged_in.post("/contacts/1/draft", data={"csrf": token})
    assert "/outbox#e-" in r.headers["Location"]
    box = logged_in.get("/outbox").get_data(as_text=True)
    assert "Software Engineer role" in box and "0 of 2 approved" in box
    logged_in.post("/emails/1", data={"csrf": token, "action": "send", "subject": "Software Engineer role",
                                      "body": "Hi, I'm Asha. Resume attached.\nAsha"})
    assert len(fake_mail) == 1 and fake_mail[0]["to"] == "priya@acme.com"
    assert "1 of 2 approved" in logged_in.get("/outbox").get_data(as_text=True)
    assert logged_in.post("/emails/1", data={"action": "send"}).status_code == 400     # CSRF


def test_linkedin_note(logged_in, app, monkeypatch):
    long = "Hi, I'm Asha from NIT Raipur. " * 8 + "Thanks."
    monkeypatch.setattr(ai, "ask_json", lambda system, prompt, schema, model: {"note": long})
    job_id = _setup(app)
    token = csrf_from(logged_in, f"/job/{job_id}/apply")
    logged_in.post(f"/job/{job_id}/cover", data={"csrf": token, "action": "linkedin"})
    html = logged_in.get(f"/job/{job_id}/apply").get_data(as_text=True)
    import re
    note = re.search(r'<textarea id="li"[^>]*>(.*?)</textarea>', html, re.S).group(1)
    assert 0 < len(note) <= 300 and note.endswith(".")


def test_approving_unchanged_draft_from_browser_counts(app, fake_mail):
    """A browser sends the text back with \r\n line ends. That alone is not an edit."""
    from jobhunter.db import get_db
    job_id = _setup(app)
    s = settings(approvals_per_kind=2)
    with app.app_context():
        db = get_db()
        for _ in range(2):
            e = _draft(db, job_id, s)
            row = db.execute("SELECT * FROM emails WHERE id = ?", (e,)).fetchone()
            outreach.edit(db, e, row["subject"], row["body"].replace("\n", "\r\n") + "\r\n")
            outreach.send(db, e, s, approved_by_you=True)
        assert outreach.is_auto(db, "hr", s)


def test_spoofed_reply_ignored(app, fake_mail, monkeypatch):
    from jobhunter.db import get_db
    monkeypatch.setattr(telegram, "send", lambda text: True)
    job_id = _setup(app)
    with app.app_context():
        db = get_db()
        e = _draft(db, job_id, settings())
        outreach.send(db, e, settings(), approved_by_you=True)
        fake = _reply("<m1@gmail.com>")
        del fake["Authentication-Results"]
        fake["Authentication-Results"] = "mx.google.com; spf=fail smtp.mailfrom=acme.com; dmarc=fail header.from=acme.com"
        monkeypatch.setattr(gmail, "recent_messages", lambda *a: [fake])
        assert outreach.check_replies(db, settings(), log=lambda m: None) == 0
