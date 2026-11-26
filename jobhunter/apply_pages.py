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
    from . import formfill, outreach, tailor
    return render_template("apply.html", job=job, app=app_row, note=cover.get(db, job_id),
                           tex=bool(tailor.source()), latex=tailor.compiler() is not None,
                           edits=tailor.saved_edits(db, job_id), tailored=tailor.pdf_path(job_id).exists(),
                           contacts=outreach.contacts_for(db, job_id), kinds=outreach.KINDS,
                           linkedin=cover.get(db, job_id, "linkedin"),
                           official_email=outreach.official_apply_email(job),
                           unanswered=unanswered, fill_log=cover.get(db, job_id, "fill_log"),
                           filler=formfill.status(), stage_names=tracker.STAGE_NAMES)


@bp.route("/job/<int:job_id>/cover", methods=["POST"])
def cover_note(job_id):
    db = get_db()
    job = _job_or_none(db, job_id)
    if job is None:
        return redirect(url_for("pages.today"))
    if request.form.get("action") == "linkedin":
        s = settings_store.get_all(db)
        try:
            cover.put(db, job_id, cover.linkedin_note(db, job, s), "linkedin")
            flash("LinkedIn note written. Copy it into LinkedIn's 'Add a note' box.")
        except ai.AIError as e:
            flash(f"Couldn't write the note: {e}", "error")
        return redirect(url_for("pages.apply_job", job_id=job_id) + "#linkedin")
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


# ---------- Prep (Phase 4) ----------

@bp.route("/job/<int:job_id>/prep", methods=["GET", "POST"])
def prep_job(job_id):
    from . import prep
    db = get_db()
    job = _job_or_none(db, job_id)
    if job is None:
        return redirect(url_for("pages.today"))
    s = settings_store.get_all(db)
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "info":
                prep.make_company_info(db, job, s)
            elif action == "mock_start":
                prep.mock_reset(db, job_id)
                prep.mock_step(db, job, s, None)
            elif action == "mock_answer":
                answer = request.form.get("answer", "").strip()
                if not answer:
                    flash("Write an answer first (or 'skip').", "error")
                elif len(answer) > prep.ANSWER_MAX:
                    flash(f"Keep the answer under {prep.ANSWER_MAX} characters.", "error")
                else:
                    prep.mock_step(db, job, s, answer)
            elif action == "mock_reset":
                prep.mock_reset(db, job_id)
        except ai.AIError as e:
            flash(f"Claude couldn't answer: {e}", "error")
        anchor = "#mock" if action and action.startswith("mock") else ""
        return redirect(url_for("pages.prep_job", job_id=job_id) + anchor)
    info = prep.company_info(db, job, s)
    turns = prep.mock_turns(db, job_id)
    scored = [t["score"] for t in turns if t["score"]]
    return render_template("prep.html", job=job, info=info, turns=turns,
                           avg=(sum(scored) / len(scored)) if scored else None)


# ---------- Tailored resume (Phase 6) ----------

@bp.route("/job/<int:job_id>/tailor", methods=["POST"])
def tailor_job(job_id):
    from . import tailor
    db = get_db()
    job = _job_or_none(db, job_id)
    if job is None:
        return redirect(url_for("pages.today"))
    action = request.form.get("action")
    try:
        if action == "suggest":
            edits = tailor.suggest(db, job, settings_store.get_all(db))
            tailor.save_edits(db, job_id, edits)
            flash(f"{len(edits)} edits suggested. Tick the ones you want, then build the PDF."
                  if edits else "Claude found nothing worth changing for this job.")
        elif action == "build":
            edits = tailor.saved_edits(db, job_id)
            picked = [edits[int(i)] for i in request.form.getlist("edit") if i.isdigit() and int(i) < len(edits)]
            tailor.compile_pdf(tailor.apply(tailor.source(), picked), job_id)
            activity.log(db, "apply", f"Tailored resume built for {job['company']} ({len(picked)} edits).")
            flash("Tailored resume built. Emails and the form filler use it for this job.")
        elif action == "remove":
            tailor.pdf_path(job_id).unlink(missing_ok=True)
            flash("Removed. This job uses your main resume again.")
    except (tailor.TailorError, ai.AIError) as e:
        flash(str(e), "error")
    return redirect(url_for("pages.apply_job", job_id=job_id) + "#tailor")


@bp.route("/job/<int:job_id>/resume.pdf")
def tailored_pdf(job_id):
    from flask import send_file
    from . import tailor
    p = tailor.pdf_path(job_id)
    if not p.exists():
        return redirect(url_for("pages.apply_job", job_id=job_id))
    return send_file(p, mimetype="application/pdf", download_name=f"resume-job-{job_id}.pdf")
