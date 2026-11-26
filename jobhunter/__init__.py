"""job-hunter: finds jobs, helps you apply and keeps track of everything."""

import secrets
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask

from . import auth, config, pages, settings_store
from .db import close_db, get_db, init_db


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(
        DB_PATH=config.DB_PATH,
        RESUME_PATH=config.RESUME_PATH,
        SEED_DIR=config.DATA_DIR,
        MAX_CONTENT_LENGTH=6 * 1024 * 1024,  # bigger uploads are refused
        SESSION_COOKIE_HTTPONLY=True,          # page scripts can't read the login cookie
        SESSION_COOKIE_SAMESITE="Strict",      # other sites can't use your login
        SESSION_COOKIE_NAME="jobhunter_session",
        PERMANENT_SESSION_LIFETIME=timedelta(days=3),
    )
    if test_config:
        app.config.update(test_config)
    else:
        config.make_private_dirs()
        app.config["SECRET_KEY"] = _load_secret_key(config.SECRETS_DIR / "flask_secret.key")

    init_db(app.config["DB_PATH"], app.config.get("SEED_DIR"))

    app.teardown_appcontext(close_db)
    app.before_request(auth.check_request)
    app.after_request(_security_headers)
    app.register_blueprint(auth.bp)
    app.register_blueprint(pages.bp)

    @app.context_processor
    def template_helpers():
        paused = settings_store.get(get_db(), settings_store.PAUSED_KEY)
        return {"csrf_token": auth.csrf_token, "paused": paused}

    @app.template_filter("when")
    def format_time(ts) -> str:
        if isinstance(ts, (int, float)):
            ts = datetime.fromtimestamp(ts)
        return ts.strftime("%d %b %Y, %I:%M %p")

    return app


def _load_secret_key(path: Path) -> bytes:
    """The key that signs login cookies. Made once, kept in secrets/."""
    if not path.exists():
        config.write_private_file(path, secrets.token_bytes(32))
    return path.read_bytes()


def _security_headers(response):
    # Only load scripts, styles and images from this website itself.
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; object-src 'none'; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response
