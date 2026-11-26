import re

import pytest

from jobhunter import create_app


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
