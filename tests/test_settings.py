import json

from conftest import csrf_from

from jobhunter import create_app, settings_store


def form_with_defaults(**changes):
    data = {}
    for s in settings_store.SETTINGS:
        value = changes.get(s.key, s.default)
        if s.kind == "list":
            data[s.key] = "\n".join(value)
        elif s.kind == "bool":
            if value:
                data[s.key] = "on"
        else:
            data[s.key] = str(value)
    return data


def test_settings_save_and_show(logged_in):
    token = csrf_from(logged_in, "/settings")
    data = form_with_defaults(daily_top_n=25, blocked_companies=["Acme Corp"])
    response = logged_in.post("/settings", data={"csrf": token, **data})
    assert response.status_code == 302
    page = logged_in.get("/settings").get_data(as_text=True)
    assert 'value="25"' in page
    assert "Acme Corp" in page
    assert "Changed:" in logged_in.get("/log").get_data(as_text=True)


def test_settings_out_of_range_not_saved(logged_in):
    token = csrf_from(logged_in, "/settings")
    data = form_with_defaults(watch_interval_minutes=5)
    page = logged_in.post("/settings", data={"csrf": token, **data}).get_data(as_text=True)
    assert "Must be at least 15" in page
    assert "Nothing saved" in page


def test_followups_must_be_in_order(logged_in):
    token = csrf_from(logged_in, "/settings")
    data = form_with_defaults(followup_1_days=7, followup_2_days=5)
    page = logged_in.post("/settings", data={"csrf": token, **data}).get_data(as_text=True)
    assert "later than the first" in page


def test_seed_file_loaded_on_first_run(tmp_path):
    (tmp_path / "initial_settings.json").write_text(json.dumps({"blocked_companies": ["Foo Ltd"]}))
    app = create_app({
        "TESTING": True, "SECRET_KEY": "k", "DB_PATH": tmp_path / "t.db",
        "RESUME_PATH": tmp_path / "r.pdf", "SEED_DIR": tmp_path,
    })
    with app.app_context():
        from jobhunter.db import get_db
        assert settings_store.get(get_db(), "blocked_companies") == ["Foo Ltd"]
