"""The database: one SQLite file at data/jobhunter.db."""

import json
import os
import sqlite3
import time
from pathlib import Path

from flask import current_app, g

SCHEMA = Path(__file__).with_name("schema.sql")


def connect(path) -> sqlite3.Connection:
    """Open the database. Used by the website and, later, by the worker."""
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")  # website and worker can both use it
    return conn


def get_db() -> sqlite3.Connection:
    """One connection per web request, closed when the request ends."""
    if "db" not in g:
        g.db = connect(current_app.config["DB_PATH"])
    return g.db


def close_db(_error=None) -> None:
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db(path, seed_dir=None) -> None:
    """Create the tables if they don't exist yet.

    On the very first run, if data/initial_settings.json or
    data/initial_profile.json exist, their values are loaded once. These files
    hold your personal choices, so they live in data/ and never go to GitHub.
    """
    conn = connect(path)
    try:
        first_run = not conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'settings'"
        ).fetchone()
        conn.executescript(SCHEMA.read_text())
        if first_run and seed_dir is not None:
            _load_seed(conn, Path(seed_dir) / "initial_settings.json", "settings")
            _load_seed(conn, Path(seed_dir) / "initial_profile.json", "profile")
        conn.commit()
    finally:
        conn.close()
    # Only your Linux user may read the database. SQLite gives its helper
    # files (-wal, -shm) the same permission as the main file.
    os.chmod(path, 0o600)


def _load_seed(conn, file: Path, table: str) -> None:
    if not file.exists():
        return
    values = json.loads(file.read_text())
    now = time.time()
    for key, value in values.items():
        conn.execute(
            f"INSERT OR REPLACE INTO {table} (key, value, updated_at) VALUES (?, ?, ?)",
            (key, json.dumps(value), now),
        )
