import re

import pytest

from jobhunter import create_app


@pytest.fixture(autouse=True)
def no_real_accounts(tmp_path, monkeypatch):
    """Tests never touch your real Gmail, Telegram or browser profile."""
    from jobhunter import formfill, gmail, people, tailor, telegram
    monkeypatch.setattr(people, "KEY_FILE", tmp_path / "hunter.json")
    monkeypatch.setattr(gmail, "FILE", tmp_path / "gmail.json")
    monkeypatch.setattr(tailor, "TEX_PATH", tmp_path / "resume.tex")
    monkeypatch.setattr(tailor, "OUT_DIR", tmp_path / "resumes")
    monkeypatch.setattr(telegram, "FILE", tmp_path / "telegram.json")
    monkeypatch.setattr(formfill, "PROFILE_DIR", tmp_path / "browser-profile")
    from jobhunter.sources import portals
    monkeypatch.setattr(portals, "JOB_PAGES_FILE", tmp_path / "job_pages.json")
    monkeypatch.setattr(portals, "READS_FILE", tmp_path / "sitemap_reads.json")


@pytest.fixture
def app(tmp_path):
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "test-key",
        "DB_PATH": tmp_path / "test.db",
        "RESUME_PATH": tmp_path / "resume.pdf",
        "SEED_DIR": tmp_path,
    })


@pytest.fixture
def client(app):
    return app.test_client()


def csrf_from(client, path):
    html = client.get(path).get_data(as_text=True)
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, f"no CSRF token on {path}"
    return match.group(1)


def set_password(client, password="correct-horse-1"):
    token = csrf_from(client, "/setup")
    return client.post("/setup", data={"csrf": token, "password": password, "confirm": password})


@pytest.fixture
def logged_in(client):
    set_password(client)
    return client
