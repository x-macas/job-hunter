"""Login, password and protection against fake form posts.

Rules:
- One password, stored only as a hash (a one-way scramble), never as text.
- 5 wrong tries in a row locks the login for 15 minutes.
- The first password can only be set from this laptop itself.
- Every form carries a secret token (CSRF token), so another website
  can't trick your browser into pressing buttons here.
"""

import hmac
import secrets
import time

from flask import (
    Blueprint, abort, current_app, flash, redirect, render_template, request,
    session, url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from . import activity
from .db import get_db

bp = Blueprint("auth", __name__)

MAX_TRIES = 5
LOCK_SECONDS = 15 * 60
MIN_PASSWORD_LENGTH = 10
PUBLIC_ENDPOINTS = {"auth.login", "auth.setup", "static"}


def _auth_row():
    return get_db().execute("SELECT * FROM auth WHERE id = 1").fetchone()


def csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def check_request() -> None:
    """Runs before every request: CSRF check on posts, then login check."""
    if request.method == "POST":
        sent = request.form.get("csrf", "")
        expected = session.get("csrf", "")
        if not expected or not hmac.compare_digest(sent, expected):
            abort(400, "This form expired. Go back, reload the page and try again.")

    if request.endpoint in PUBLIC_ENDPOINTS:
        return None
    if _auth_row() is None:
        return redirect(url_for("auth.setup"))
    if not session.get("logged_in"):
        return redirect(url_for("auth.login", next=request.path))
    return None


def _from_this_laptop() -> bool:
    return request.remote_addr in ("127.0.0.1", "::1")


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    if _auth_row() is not None:
        return redirect(url_for("auth.login"))
    if not _from_this_laptop() and not current_app.config.get("TESTING"):
        abort(403, "Set the first password from the laptop itself.")

    error = None
    if request.method == "POST":
        password = request.form.get("password", "")
        if len(password) < MIN_PASSWORD_LENGTH:
            error = f"Use at least {MIN_PASSWORD_LENGTH} characters."
        elif password != request.form.get("confirm", ""):
            error = "The two passwords don't match."
        else:
            db = get_db()
            db.execute(
                "INSERT INTO auth (id, password_hash, created_at) VALUES (1, ?, ?)",
                (generate_password_hash(password), time.time()),
            )
            db.commit()
            activity.log(db, "security", "Website password set.")
            _start_session()
            return redirect(url_for("pages.today"))
    return render_template("setup.html", error=error, min_length=MIN_PASSWORD_LENGTH)


@bp.route("/login", methods=["GET", "POST"])
def login():
    row = _auth_row()
    if row is None:
        return redirect(url_for("auth.setup"))

    error = None
    now = time.time()
    if row["locked_until"] > now:
        minutes = int((row["locked_until"] - now) // 60) + 1
        error = f"Too many wrong tries. Try again in {minutes} min."
    elif request.method == "POST":
        db = get_db()
        if check_password_hash(row["password_hash"], request.form.get("password", "")):
            db.execute("UPDATE auth SET failed_count = 0, locked_until = 0 WHERE id = 1")
            db.commit()
            activity.log(db, "security", f"Logged in from {request.remote_addr}.")
            _start_session()
            return redirect(safe_next(request.args.get("next")))

        tries = row["failed_count"] + 1
        if tries >= MAX_TRIES:
            db.execute(
                "UPDATE auth SET failed_count = 0, locked_until = ? WHERE id = 1",
                (now + LOCK_SECONDS,),
            )
            activity.log(db, "security",
                         f"Login locked for 15 min after {MAX_TRIES} wrong tries "
                         f"(last from {request.remote_addr}).")
            error = "Too many wrong tries. Locked for 15 minutes."
        else:
            db.execute("UPDATE auth SET failed_count = ? WHERE id = 1", (tries,))
            activity.log(db, "security", f"Wrong password from {request.remote_addr}.")
            error = f"Wrong password. {MAX_TRIES - tries} tries left."
        db.commit()
    return render_template("login.html", error=error)


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("Logged out.")
    return redirect(url_for("auth.login"))


def _start_session() -> None:
    session.clear()  # a fresh session after login stops session-fixation tricks
    session.permanent = True
    session["logged_in"] = True
    csrf_token()


def safe_next(target: str | None) -> str:
    """Only allow going back to a page on this website, never another site."""
    if (
        target
        and target.startswith("/")
        and not target.startswith("//")
        and "\\" not in target
    ):
        return target
    return url_for("pages.today")
