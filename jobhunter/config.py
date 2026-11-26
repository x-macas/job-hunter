"""Where things live on disk.

Everything personal sits in two folders that Git ignores:
  data/     the database, your resume, your saved answers
  secrets/  login keys (Flask key now; Gmail and Telegram later)

Set JOBHUNTER_HOME to move both folders somewhere else (for example on a
server later). By default they sit inside the project folder.
"""

import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
HOME = Path(os.environ.get("JOBHUNTER_HOME", PROJECT_DIR)).resolve()

DATA_DIR = HOME / "data"
SECRETS_DIR = HOME / "secrets"
DB_PATH = DATA_DIR / "jobhunter.db"
RESUME_PATH = DATA_DIR / "resume.pdf"

# The website only listens on this address. 127.0.0.1 means "this laptop only".
HOST = os.environ.get("JOBHUNTER_HOST", "127.0.0.1")
PORT = int(os.environ.get("JOBHUNTER_PORT", "8000"))


def make_private_dirs() -> None:
    """Create data/ and secrets/ so only your Linux user can open them."""
    for folder in (DATA_DIR, SECRETS_DIR):
        folder.mkdir(parents=True, exist_ok=True)
        os.chmod(folder, 0o700)


def write_private_file(path: Path, content: bytes) -> None:
    """Write a file that only your Linux user can read (permission 600)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(content)
    os.chmod(path, 0o600)
