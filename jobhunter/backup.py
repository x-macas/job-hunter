"""Phase 5: daily backups on this laptop only (never Google Drive, never a USB stick).

Each backup is one .tar.gz in the backup folder (Settings), readable only by you:
the database (copied safely while the website runs), your resume (PDF and LaTeX) and the small
data files. Old ones are deleted so only the newest N stay (Settings).
Secrets (passwords, tokens) are NOT in the backup.

To restore: stop the website, unpack a backup into the project's data/ folder, start it again.
"""

import os
import re
import sqlite3
import tarfile
import tempfile
import time
from pathlib import Path

from .config import DATA_DIR, DB_PATH, RESUME_PATH

NAME = re.compile(r"job-hunter-\d{8}-\d{6}\.tar\.gz")
EXTRA_FILES = ("resume.tex", "workday_facets.json", "sitemap_reads.json", "initial_settings.json")
# Folders that are a USB stick, another disk, or synced to the cloud. Backups never go there.
NOT_HERE = re.compile(r"^/(media|run/media|mnt)/|google|g-?drive|onedrive|dropbox|icloud|mega(sync)?\b", re.I)


class BackupError(Exception):
    pass


def folder(s: dict) -> Path:
    path = Path(os.path.expanduser(s["backup_dir"])).resolve()
    if NOT_HERE.search(str(path)):
        raise BackupError("Backups stay on this laptop: pick a folder that isn't a USB stick or a cloud folder.")
    if path == DATA_DIR.resolve() or DATA_DIR.resolve() in path.parents:
        raise BackupError("Pick a backup folder outside the project's data/ folder.")
    return path


def run(s: dict, now: float | None = None) -> Path:
    dest = folder(s)
    dest.mkdir(parents=True, exist_ok=True)
    os.chmod(dest, 0o700)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now or time.time()))
    target = dest / f"job-hunter-{stamp}.tar.gz"
    with tempfile.TemporaryDirectory(dir=dest) as tmp:
        copy = Path(tmp) / "jobhunter.db"
        src = sqlite3.connect(DB_PATH)
        out = sqlite3.connect(copy)
        try:
            src.backup(out)        # a consistent copy even while the website writes
        finally:
            out.close()
            src.close()
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f, tarfile.open(fileobj=f, mode="w:gz") as tar:
            tar.add(copy, arcname="jobhunter.db")
            if RESUME_PATH.exists():
                tar.add(RESUME_PATH, arcname="resume.pdf")
            for name in EXTRA_FILES:
                if (DATA_DIR / name).exists():
                    tar.add(DATA_DIR / name, arcname=name)
    prune(dest, s["backup_keep"])
    return target


def prune(dest: Path, keep: int) -> None:
    files = sorted((p for p in dest.iterdir() if NAME.fullmatch(p.name)), key=lambda p: p.name, reverse=True)
    for old in files[keep:]:
        old.unlink()


def latest(s: dict) -> tuple[Path, float] | None:
    try:
        dest = folder(s)
    except BackupError:
        return None
    if not dest.exists():
        return None
    files = sorted((p for p in dest.iterdir() if NAME.fullmatch(p.name)), key=lambda p: p.name)
    return (files[-1], files[-1].stat().st_mtime) if files else None
