"""The website's pages."""

import json
import re
import time
from datetime import datetime

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from . import activity, profile_store, settings_store
from .auth import safe_next
from .config import write_private_file
from .db import get_db

bp = Blueprint("pages", __name__)

MAX_RESUME_BYTES = 5 * 1024 * 1024


@bp.route("/stats")
def stats():
    from . import backup, stats as stats_mod
    db = get_db()
    s = settings_store.get_all(db)
    return render_template("stats.html", d=stats_mod.page(db, s), last_backup=backup.latest(s),
                           backup_dir=s["backup_dir"], backup_keep=s["backup_keep"])


@bp.route("/backup", methods=["POST"])
def backup_now():
    from . import backup
    db = get_db()
    try:
        path = backup.run(settings_store.get_all(db))
        activity.log(db, "backup", f"Backup saved: {path.name}.")
        flash(f"Backup saved: {path}")
    except (backup.BackupError, OSError) as e:
        flash(f"Backup failed: {e}", "error")
    return redirect(url_for("pages.stats"))


@bp.route("/log")
def log():
    return render_template("log.html", rows=activity.recent(get_db()))


@bp.route("/check")
def check():
    from . import handcheck
    db = get_db()
    s = settings_store.get_all(db)
    groups = handcheck.build(s)
    done = handcheck.done_today(db)
    left = sum(1 for i in groups["first"] if i["name"] not in done)
    new = handcheck.new_today(db, s)
    return render_template("check.html", groups=groups, done=done, left=left, new=new,
                           new_jobs=sum(len(g["dev"]) for g in new), now=time.time())


@bp.route("/check/tick", methods=["POST"])
def check_tick():
    from . import handcheck
    db = get_db()
    company = request.form.get("company", "").strip()[:200]
    names = {i["name"] for g in handcheck.build(settings_store.get_all(db)).values() for i in g}
    if company in names:
        handcheck.mark(db, company, request.form.get("done") == "1")
    return redirect(url_for("pages.check") + "#c-" + handcheck.slug(company))


@bp.route("/check/paste/<company_slug>", methods=["GET", "POST"])
def check_paste(company_slug):
    """For a site we can't read: paste its job list, see only what's new since last time."""
    from . import handcheck
    db = get_db()
    s = settings_store.get_all(db)
    item = handcheck.find(s, company_slug[:200])
    if item is None:
        flash("That company isn't on the Check page.", "error")
        return redirect(url_for("pages.check"))
    if request.method == "POST":
        text = request.form.get("page", "")
        if len(text) > handcheck.PASTE_MAX_CHARS:
            flash("That's too much text. Copy just the job list part of the page.", "error")
        elif not text.strip():
            flash("The box was empty.", "error")
        else:
            handcheck.paste(db, s, item["name"], text)
        return redirect(url_for("pages.check_paste", company_slug=item["slug"]))
    return render_template("check_paste.html", item=item, last=handcheck.last_paste(db, item["name"]))


@bp.route("/settings", methods=["GET", "POST"])
def settings():
    db = get_db()
    errors = {}
    values = settings_store.get_all(db)
    if request.method == "POST":
        new_values, errors = settings_store.parse_form(request.form)
        if errors:
            values = {**values, **new_values}
            flash("Nothing saved. Fix the boxes marked in red.", "error")
        else:
            changed = [k for k, v in new_values.items() if values.get(k) != v]
            for key, value in new_values.items():
                settings_store.put(db, key, value)
            db.commit()
            if changed:
                labels = ", ".join(settings_store.BY_KEY[k].label for k in changed)
                activity.log(db, "settings", f"Changed: {labels}")
            flash("Settings saved.")
            return redirect(url_for("pages.settings"))
    return render_template(
        "settings.html",
        groups=settings_store.GROUPS,
        settings=settings_store.SETTINGS,
        values=values,
        errors=errors,
    )


@bp.route("/profile", methods=["GET", "POST"])
def profile():
    db = get_db()
    errors = {}
    values = profile_store.get_all(db)
    if request.method == "POST":
        new_values, errors = profile_store.parse_form(request.form)
        if errors:
            values = {**values, **new_values}
            flash("Nothing saved. Fix the boxes marked in red.", "error")
        else:
            for key, value in new_values.items():
                profile_store.put(db, key, value)
            db.commit()
            activity.log(db, "profile", "Profile details updated.")
            flash("Profile saved.")
            return redirect(url_for("pages.profile"))
    resume = current_app.config["RESUME_PATH"]
    resume_info = None
    if resume.exists():
        resume_info = datetime.fromtimestamp(resume.stat().st_mtime)
    return render_template(
        "profile.html",
        fields=profile_store.FIELDS,
        values=values,
        errors=errors,
        resume_info=resume_info,
    )


@bp.route("/profile/resume", methods=["POST"])
def upload_resume():
    file = request.files.get("resume")
    if file is None or not file.filename:
        flash("Pick a PDF file first.", "error")
        return redirect(url_for("pages.profile"))
    content = file.read(MAX_RESUME_BYTES + 1)
    if len(content) > MAX_RESUME_BYTES:
        flash("That file is over 5 MB.", "error")
    elif not content.startswith(b"%PDF-"):
        flash("That isn't a PDF file.", "error")
    else:
        # Saved under a fixed name, so the uploaded file name is never used as a path.
        write_private_file(current_app.config["RESUME_PATH"], content)
        db = get_db()
        profile_store.put(db, "resume_uploaded_at", time.time())
        db.commit()
        activity.log(db, "profile", "Resume uploaded.")
        flash("Resume saved.")
    return redirect(url_for("pages.profile"))


@bp.route("/stop", methods=["POST"])
def toggle_stop():
    """The emergency stop: pauses all sending and background work."""
    db = get_db()
    paused = not settings_store.get(db, settings_store.PAUSED_KEY)
    settings_store.put(db, settings_store.PAUSED_KEY, paused)
    db.commit()
    if paused:
        activity.log(db, "security", "EMERGENCY STOP: all sending and background work paused.")
        flash("Everything is stopped. Nothing will be sent until you press Resume.", "error")
    else:
        activity.log(db, "security", "Resumed after emergency stop.")
        flash("Resumed.")
    return redirect(safe_next(request.form.get("next")))


# ---------------- Phase 1: jobs ----------------

SKIP_REASONS = [
    "Not this company", "Wrong tech stack", "Too senior", "Pay too low",
    "Location", "Not a dev role", "Already applied", "Other",
]


def _today_cards(db, s):
    """Every open job that passed your rules (and Claude's checks), best first.
    Jobs Claude hasn't scored yet come last."""
    from . import ranking
    rows = db.execute("SELECT * FROM jobs WHERE rule_ok = 1 AND status = 'new'").fetchall()
    cards, unscored, dropped, now = [], [], [], time.time()
    for r in rows:
        ai = json.loads(r["ai_json"] or "{}")
        if r["ai_score"] is None:
            unscored.append({"job": r, "ai": ai, "tags": ["Not scored yet"], "pay": _pay_label(r), "final": None})
            continue
        final, tags = ranking.final_score(r, ai, s, now)
        if final is None:
            dropped.append({"job": r, "why": (tags or ["dropped"])[0]})
            continue
        cards.append({"job": r, "ai": ai, "tags": tags or [], "pay": _pay_label(r), "final": final})
    cards.sort(key=lambda c: -c["final"])
    unscored.sort(key=lambda c: -(c["job"]["posted_at"] or c["job"]["first_seen"]))
    dropped.sort(key=lambda d: -(d["job"]["ai_score"] or 0))
    return cards + unscored, dropped


def _pay_label(job) -> str:
    low, high = job["salary_min_lpa"], job["salary_max_lpa"]
    if high is None:
        return "Pay not listed"
    if job["job_type"] == "intern":
        return job["salary_text"] or "Stipend listed"
    return f"₹{low:g}–{high:g} LPA" if low != high else f"₹{high:g} LPA"


@bp.route("/", endpoint="today")
def today_jobs():
    db = get_db()
    s = settings_store.get_all(db)
    cards, dropped = _today_cards(db, s)
    run = db.execute("SELECT * FROM runs WHERE name = 'daily'").fetchone()
    counts = db.execute(
        "SELECT COUNT(*) AS total, SUM(rule_ok = 1 AND status = 'new') AS kept FROM jobs").fetchone()
    from . import tracker
    return render_template("today.html", now=datetime.now(), cards=cards, dropped=dropped, run=run,
                           counts=counts, reasons=SKIP_REASONS, applied=tracker.applied_today(db),
                           goal=s["goal_applications"])


@bp.route("/job/<int:job_id>/skip", methods=["POST"])
def skip_job(job_id):
    db = get_db()
    reason = request.form.get("reason", "")
    if reason not in SKIP_REASONS:
        flash("Pick a reason.", "error")
        return redirect(url_for("pages.today"))
    job = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if job is None:
        flash("That job is gone.", "error")
        return redirect(url_for("pages.today"))
    db.execute("UPDATE jobs SET status = 'skipped' WHERE id = ?", (job_id,))
    db.execute("INSERT INTO skips (job_id, reason, ts) VALUES (?, ?, ?)", (job_id, reason, time.time()))
    if reason == "Not this company":
        hidden = settings_store.get(db, "hidden_companies")
        if job["company"] not in hidden:
            settings_store.put(db, "hidden_companies", hidden + [job["company"]])
    db.commit()
    activity.log(db, "job", f"Skipped {job['title']} at {job['company']}: {reason}.")
    flash(f"Skipped {job['company']}.")
    return redirect(url_for("pages.today"))


@bp.route("/add", methods=["POST"])
def add_job():
    from . import linkadd
    db = get_db()
    if settings_store.get(db, settings_store.PAUSED_KEY):
        flash("Everything is stopped. Press Resume first.", "error")
        return redirect(url_for("pages.today"))
    s = settings_store.get_all(db)
    link = request.form.get("link", "").strip()
    text = request.form.get("text", "").strip()
    try:
        if link:
            job = linkadd.from_url(link, s["ai_model"])
        elif len(text) >= 80:
            job = linkadd.from_text(text[:30000], s["ai_model"])
        else:
            raise linkadd.AddError("Paste a job link, or the full job text.")
        job_id = linkadd.add(db, job, s)
    except linkadd.AddError as e:
        flash(str(e), "error")
        return redirect(url_for("pages.today"))
    row = db.execute("SELECT title, company FROM jobs WHERE id = ?", (job_id,)).fetchone()
    activity.log(db, "job", f"Added by you: {row['title']} at {row['company']}.")
    flash(f"Added {row['title']} at {row['company']}. It's at the top of the list.")
    return redirect(url_for("pages.today"))


@bp.route("/run", methods=["POST"])
def run_now():
    import subprocess
    import sys
    from .config import DATA_DIR, PROJECT_DIR
    db = get_db()
    if settings_store.get(db, settings_store.PAUSED_KEY):
        flash("Everything is stopped. Press Resume first.", "error")
        return redirect(url_for("pages.today"))
    log_file = open(DATA_DIR / "worker.log", "a")
    subprocess.Popen([sys.executable, "-B", "-m", "jobhunter.worker", "daily"], cwd=PROJECT_DIR,
                     stdout=log_file, stderr=subprocess.STDOUT, start_new_session=True)
    activity.log(db, "worker", "Daily run started by you.")
    flash("Started. Finding and scoring jobs takes a few minutes. Reload this page later.")
    return redirect(url_for("pages.today"))


# ---------------- Connections (Telegram, Adzuna) ----------------

@bp.route("/connections", methods=["GET", "POST"])
def connections():
    from . import telegram
    from .config import SECRETS_DIR
    db = get_db()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "telegram_token":
            token = request.form.get("token", "").strip()
            if not re.fullmatch(r"\d{6,12}:[A-Za-z0-9_-]{30,50}", token):
                flash("That doesn't look like a bot token. It looks like 123456789:ABC...", "error")
            else:
                telegram.set_token(token)
                activity.log(db, "security", "Telegram bot token saved. Waiting for pairing code.")
                flash("Token saved. Now send the code below to your bot.")
        elif action == "telegram_test":
            try:
                ok = telegram.send("Test from job-hunter. Alerts will come here.")
                flash("Test message sent." if ok else "Telegram isn't connected yet.", "message" if ok else "error")
            except telegram.TelegramError as e:
                flash(str(e), "error")
        elif action == "telegram_remove":
            telegram.FILE.unlink(missing_ok=True)
            activity.log(db, "security", "Telegram disconnected.")
            flash("Telegram disconnected.")
        elif action == "gmail_connect":
            from . import gmail
            address = request.form.get("email", "").strip().lower()
            if not re.fullmatch(r"[a-z0-9._%+-]+@gmail\.com", address):
                flash("Use your full Gmail address, like name@gmail.com.", "error")
            else:
                try:
                    gmail.connect(address, request.form.get("app_password", ""))
                    activity.log(db, "security", f"Gmail connected ({address}).")
                    flash("Gmail connected. Job-alert emails are read on the next check.")
                except gmail.GmailError as e:
                    flash(str(e), "error")
        elif action == "gmail_remove":
            from . import gmail
            gmail.disconnect()
            activity.log(db, "security", "Gmail disconnected.")
            flash("Gmail disconnected. The app password is deleted from this laptop. "
                  "You can also delete it in your Google account.")
        elif action == "adzuna":
            app_id, key = request.form.get("app_id", "").strip(), request.form.get("app_key", "").strip()
            if not (re.fullmatch(r"[A-Za-z0-9]{4,40}", app_id) and re.fullmatch(r"[A-Za-z0-9]{10,80}", key)):
                flash("Those don't look like Adzuna keys.", "error")
            else:
                write_private_file(SECRETS_DIR / "adzuna.json",
                                   json.dumps({"app_id": app_id, "app_key": key}).encode())
                activity.log(db, "security", "Adzuna keys saved.")
                flash("Adzuna keys saved. The next daily run will use them.")
        return redirect(url_for("pages.connections"))
    from . import gmail
    tg = telegram.load()
    return render_template("connections.html", tg=tg, gmail=gmail.load().get("email"),
                           adzuna=(SECRETS_DIR / "adzuna.json").exists())


from . import apply_pages, outbox_pages  # noqa: E402,F401  (Phase 2 and 3 pages)
