"""The website's pages."""

import time
from datetime import datetime

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from . import activity, profile_store, settings_store
from .auth import safe_next
from .config import write_private_file
from .db import get_db

bp = Blueprint("pages", __name__)

MAX_RESUME_BYTES = 5 * 1024 * 1024


@bp.route("/")
def today():
    return render_template("today.html", now=datetime.now())


@bp.route("/outbox")
def outbox():
    return render_template("outbox.html")


@bp.route("/tracker")
def tracker():
    return render_template("tracker.html")


@bp.route("/stats")
def stats():
    return render_template("stats.html")


@bp.route("/log")
def log():
    return render_template("log.html", rows=activity.recent(get_db()))


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
