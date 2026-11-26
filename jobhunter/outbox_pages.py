"""Phase 3 pages: people for a job, email drafts, the Outbox."""

import time

from flask import flash, redirect, render_template, request, url_for

from . import activity, ai, gmail, outreach, settings_store
from .db import get_db
from .pages import bp


def _job(db, job_id):
    return db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


@bp.route("/job/<int:job_id>/contacts", methods=["POST"])
def add_contact(job_id):
    db = get_db()
    job = _job(db, job_id)
    if job is None:
        return redirect(url_for("pages.today"))
    if request.form.get("official") == "1":
        address = outreach.official_apply_email(job)
        error = outreach.add_contact(db, job_id, "", address or "", "hr", "Official apply email") if address \
            else "This job has no official apply email."
    else:
        error = outreach.add_contact(db, job_id, request.form.get("name", ""), request.form.get("email", ""),
                                     request.form.get("kind", ""), request.form.get("how", ""))
    flash(error or "Added.", "error" if error else "message")
    return redirect(url_for("pages.apply_job", job_id=job_id) + "#people")


@bp.route("/contacts/<int:contact_id>/draft", methods=["POST"])
def draft_email(contact_id):
    db = get_db()
    contact = db.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    if contact is None:
        return redirect(url_for("pages.outbox"))
    job = _job(db, contact["job_id"])
    try:
        email_id = outreach.write_draft(db, job, contact, settings_store.get_all(db))
    except ai.AIError as e:
        flash(f"Couldn't write the email: {e}", "error")
        return redirect(url_for("pages.apply_job", job_id=job["id"]) + "#people")
    flash("Draft written. Read it, change anything, then approve it.")
    return redirect(url_for("pages.outbox") + f"#e-{email_id}")


@bp.route("/emails/<int:email_id>", methods=["POST"])
def email_action(email_id):
    db = get_db()
    s = settings_store.get_all(db)
    action = request.form.get("action")
    if action in ("save", "send"):
        outreach.edit(db, email_id, request.form.get("subject", ""), request.form.get("body", ""))
    if action == "save":
        flash("Saved. Changed drafts need your approval again.")
    elif action == "reject":
        outreach.reject(db, email_id)
        activity.log(db, "email", f"Draft #{email_id} rejected.")
        flash("Rejected. That kind of email needs your approvals again before it sends on its own.")
    elif action == "send":
        try:
            outreach.send(db, email_id, s, approved_by_you=True)
            flash("Sent.")
        except outreach.SendBlocked as e:
            flash(str(e), "error")
        except gmail.GmailError as e:
            flash(f"Not sent: {e}", "error")
    return redirect(url_for("pages.outbox") + f"#e-{email_id}")


@bp.route("/outbox", endpoint="outbox")
def outbox_page():
    db = get_db()
    s = settings_store.get_all(db)
    kinds = [{"key": k, "name": n, "streak": outreach.streak(db, k), "auto": outreach.is_auto(db, k, s)}
             for k, n in outreach.KINDS.items()]
    return render_template("outbox.html", box=outreach.outbox(db), kinds=kinds, need=s["approvals_per_kind"],
                           sent_today=outreach.sent_today(db), cap=s["email_daily_cap"], names=outreach.KINDS,
                           wait=s["auto_send_wait_minutes"], connected=gmail.connected(), now=time.time())
