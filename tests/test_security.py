from conftest import csrf_from, set_password


def test_first_visit_goes_to_setup(client):
    response = client.get("/")
    assert response.status_code == 302
    assert "/setup" in response.headers["Location"]


def test_short_password_refused(client):
    token = csrf_from(client, "/setup")
    response = client.post("/setup", data={"csrf": token, "password": "short", "confirm": "short"})
    assert "at least" in response.get_data(as_text=True)


def test_setup_only_once(client):
    set_password(client)
    client.post("/logout", data={"csrf": csrf_from(client, "/")})
    response = client.get("/setup")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_pages_need_login(client):
    set_password(client)
    client.post("/logout", data={"csrf": csrf_from(client, "/")})
    for path in ["/", "/outbox", "/tracker", "/stats", "/profile", "/settings", "/log"]:
        response = client.get(path)
        assert response.status_code == 302, path
        assert "/login" in response.headers["Location"], path


def test_database_only_readable_by_owner(app):
    assert oct(app.config["DB_PATH"].stat().st_mode & 0o777) == "0o600"


def test_password_not_stored_as_text(app, client):
    set_password(client, "correct-horse-1")
    db = app.config["DB_PATH"].read_bytes()
    assert b"correct-horse-1" not in db


def login(client, password):
    token = csrf_from(client, "/login")
    return client.post("/login", data={"csrf": token, "password": password})


def test_lockout_after_five_wrong_tries(client):
    set_password(client, "correct-horse-1")
    client.post("/logout", data={"csrf": csrf_from(client, "/")})
    for _ in range(5):
        login(client, "wrong-password")
    # Even the right password is refused while locked.
    response = login(client, "correct-horse-1")
    assert "Too many wrong tries" in response.get_data(as_text=True)
    assert client.get("/").status_code == 302


def test_right_password_logs_in(client):
    set_password(client, "correct-horse-1")
    client.post("/logout", data={"csrf": csrf_from(client, "/")})
    response = login(client, "correct-horse-1")
    assert response.status_code == 302
    assert client.get("/").status_code == 200


def test_post_without_csrf_rejected(logged_in):
    response = logged_in.post("/stop", data={})
    assert response.status_code == 400


def test_login_redirect_stays_on_site(client):
    set_password(client, "correct-horse-1")
    client.post("/logout", data={"csrf": csrf_from(client, "/")})
    for bad in ["https://evil.example", "//evil.example", "/\\evil.example"]:
        token = csrf_from(client, "/login")
        response = client.post(f"/login?next={bad}",
                               data={"csrf": token, "password": "correct-horse-1"})
        assert response.headers["Location"] == "/", bad
        client.post("/logout", data={"csrf": csrf_from(client, "/")})


def test_security_headers(logged_in):
    response = logged_in.get("/")
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]
    assert response.headers["X-Frame-Options"] == "DENY"


def test_emergency_stop_toggles(logged_in):
    token = csrf_from(logged_in, "/")
    logged_in.post("/stop", data={"csrf": token, "next": "/"})
    assert "Everything is stopped" in logged_in.get("/").get_data(as_text=True)
    logged_in.post("/stop", data={"csrf": token, "next": "/"})
    assert "Everything is stopped" not in logged_in.get("/").get_data(as_text=True)
    log = logged_in.get("/log").get_data(as_text=True)
    assert "EMERGENCY STOP" in log


def test_resume_must_be_pdf(app, logged_in):
    import io
    token = csrf_from(logged_in, "/profile")
    logged_in.post("/profile/resume", data={
        "csrf": token, "resume": (io.BytesIO(b"not a pdf"), "resume.pdf"),
    }, content_type="multipart/form-data")
    assert not app.config["RESUME_PATH"].exists()

    logged_in.post("/profile/resume", data={
        "csrf": token, "resume": (io.BytesIO(b"%PDF-1.7 fake"), "../../evil.pdf"),
    }, content_type="multipart/form-data")
    assert app.config["RESUME_PATH"].exists()
    assert oct(app.config["RESUME_PATH"].stat().st_mode & 0o777) == "0o600"


def test_worker_lock_file_is_private(tmp_path, monkeypatch):
    from jobhunter import worker
    lock = tmp_path / "worker.lock"
    lock.write_text("")
    lock.chmod(0o644)                     # an old run left it readable
    monkeypatch.setattr(worker, "DATA_DIR", tmp_path)
    monkeypatch.setattr(worker, "DB_PATH", tmp_path / "w.db")
    monkeypatch.setattr(worker, "make_private_dirs", lambda: None)
    assert worker.main(["worker", "status"]) == 0
    assert oct(lock.stat().st_mode & 0o777) == "0o600"
