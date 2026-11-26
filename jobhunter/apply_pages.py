"""Phase 2 pages: Apply (one job), the Tracker board, and your saved Answers."""

from flask import flash, redirect, render_template, request, url_for

from . import activity, ai, answers, cover, settings_store, tracker
from .db import get_db
from .pages import bp


def _job_or_none(db, job_id):
    return db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def _back(default):
    nxt = request.form.get("next", "")
    return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else default)


# ---------- Apply ----------

@bp.route("/job/<int:job_id>/apply")
def apply_job(job_id):
    db = get_db()
    job = _job_or_none(db, job_id)
    if job is None:
        flash("That job is gone.", "error")
        return redirect(url_for("pages.today"))
    app_row = db.execute("SELECT * FROM applications WHERE job_id = ?", (job_id,)).fetchone()
    unanswered = [q for q in cover.get(db, job_id, "unanswered").splitlines() if q.strip()]
    from . import formfill
    return render_template("apply.html", job=job, app=app_row, note=cover.get(db, job_id),
                           unanswered=unanswered, fill_log=cover.get(db, job_id, "fill_log"),
                           filler=formfill.status(), stage_names=tracker.STAGE_NAMES)


@bp.route("/job/<int:job_id>/cover", methods=["POST"])
def cover_note(job_id):
    db = get_db()
    job = _job_or_none(db, job_id)
    if job is None:
        return redirect(url_for("pages.today"))
    if request.form.get("action") == "write":
        s = settings_store.get_all(db)
        try:
            cover.put(db, job_id, cover.write(db, job, s))
            activity.log(db, "apply", f"Cover note written for {job['title']} at {job['company']}.")
            flash("Cover note written. Read it and change anything that doesn't sound like you.")
        except ai.AIError as e:
            flash(f"Couldn't write the note: {e}", "error")
    else:
        text = request.form.get("note", "").strip()
        if len(text) > cover.NOTE_MAX:
            flash(f"Keep the note under {cover.NOTE_MAX} characters.", "error")
        else:
            cover.put(db, job_id, text)
            flash("Cover note saved.")
    return redirect(url_for("pages.apply_job", job_id=job_id) + "#cover")


@bp.route("/job/<int:job_id>/applied", methods=["POST"])
def job_applied(job_id):
    db = get_db()
    job = _job_or_none(db, job_id)
    if job is None:
        return redirect(url_for("pages.today"))
    if request.form.get("undo") == "1":
        tracker.undo_applied(db, job_id)
        activity.log(db, "apply", f"Took {job['title']} at {job['company']} off the Tracker.")
        flash("Taken off the Tracker. It's back on the Today page.")
    elif tracker.mark_applied(db, job_id):
        activity.log(db, "apply", f"Applied: {job['title']} at {job['company']}.")
        s = settings_store.get_all(db)
        n = tracker.applied_today(db)
        goal = s["goal_applications"]
        flash(f"Nice. {n} of {goal} applications today." if n < goal else f"Nice. That's {n} today, goal done.")
    return _back(url_for("pages.tracker"))


@bp.route("/job/<int:job_id>/fill", methods=["POST"])
def fill_form(job_id):
    from . import formfill
    db = get_db()
    job = _job_or_none(db, job_id)
    if job is None:
        return redirect(url_for("pages.today"))
    st = formfill.status()
    if not st["ready"]:
        flash(st["why"], "error")
    elif formfill.busy():
        flash("A form window is already open. Close it first.", "error")
    else:
        formfill.start(job_id)
        activity.log(db, "apply", f"Form filler opened for {job['title']} at {job['company']}.")
        flash("A separate browser window is opening with the form. Check every box, then press Submit yourself. "
              "Reload this page after to see what it couldn't fill.")
    return redirect(url_for("pages.apply_job", job_id=job_id))


# ---------- Tracker ----------

@bp.route("/tracker", endpoint="tracker")
def tracker_page():
    db = get_db()
    s = settings_store.get_all(db)
    return render_template("tracker.html", columns=tracker.board(db, s), stages=tracker.STAGES,
                           followup=s["followup_1_days"])


@bp.route("/tracker/<int:job_id>/move", methods=["POST"])
def tracker_move(job_id):
    db = get_db()
    stage = request.form.get("stage", "")
    if tracker.move(db, job_id, stage):
        job = _job_or_none(db, job_id)
        activity.log(db, "apply", f"{job['company']}: moved to {tracker.STAGE_NAMES[stage]}.")
    return redirect(url_for("pages.tracker") + f"#a-{job_id}")


@bp.route("/tracker/<int:job_id>/note", methods=["POST"])
def tracker_note(job_id):
    db = get_db()
    tracker.set_note(db, job_id, request.form.get("note", ""))
    flash("Note saved.")
    return redirect(url_for("pages.tracker") + f"#a-{job_id}")


# ---------- Answers ----------

@bp.route("/answers", methods=["GET", "POST"])
def answers_page():
    db = get_db()
    answers.seed(db)
    if request.method == "POST":
        raw_id = request.form.get("id", "")
        answer_id = int(raw_id) if raw_id.isdigit() else None
        error = answers.save(db, answer_id, request.form.get("question", ""), request.form.get("answer", ""))
        flash(error or "Answer saved.", "error" if error else "message")
        return _back(url_for("pages.answers_page") + (f"#q-{answer_id}" if answer_id else ""))
    rows = answers.all_answers(db)
    return render_template("answers.html", rows=rows, empty=sum(1 for r in rows if not r["answer"]))


@bp.route("/answers/<int:answer_id>/delete", methods=["POST"])
def answers_delete(answer_id):
    answers.delete(get_db(), answer_id)
    flash("Answer deleted.")
    return redirect(url_for("pages.answers_page"))
