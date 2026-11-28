"""Phase 6: tailored resume from LaTeX. Claude is faked; compiling runs only if LaTeX is installed."""
import io

import pytest
from conftest import csrf_from
from test_phase1 import raw, settings

from jobhunter import ai, collect, formfill, tailor

TEX = r"""\documentclass{article}
\usepackage{hyperref}
\begin{document}
\section*{Skills}
C++, Java, Python, Flask, MongoDB
\section*{Experience}
\begin{itemize}
  \item Wrote DSA problems and test cases at GeeksforGeeks.
  \item Built Python tools to test LLM outputs at Ethara AI.
\end{itemize}
\href{https://github.com/me}{GitHub}
\end{document}
"""


def test_safe_edits_filter():
    edits = [
        {"find": "C++, Java, Python, Flask, MongoDB", "replace": "Python, Flask, C++, Java, MongoDB", "why": "Python first"},
        {"find": "test LLM outputs", "replace": "check LLM answers", "why": "plain words"},
        {"find": "Python tools", "replace": r"Python tools\input{/etc/passwd}", "why": "bad"},           # reads a file
        {"find": "test cases", "replace": r"test cases\immediate\write18{rm -rf ~}", "why": "bad"},     # runs a program
        {"find": "at Ethara AI.", "replace": r"at Ethara AI.\textbf{x}", "why": "new command"},         # not in the resume
        {"find": "Python", "replace": "Go", "why": "not unique"},                                      # appears many times
        {"find": "GeeksforGeeks.", "replace": "GeeksforGeeks.}", "why": "braces"},                     # breaks braces
        {"find": "MongoDB", "replace": "MongoDB " + "x" * 400, "why": "too long"},
        {"find": "Flask, MongoDB", "replace": "MongoDB, Flask", "why": "overlaps the first edit"},
        {"find": "nowhere in resume", "replace": "x", "why": "missing"},
        {"find": "Built", "replace": "Built", "why": "no change"},
    ]
    kept = tailor.safe_edits(TEX, edits)          # only the first 8 are looked at
    assert [e["why"] for e in kept] == ["Python first", "plain words"]
    new = tailor.apply(TEX, kept)
    assert "Python, Flask, C++, Java, MongoDB" in new and "check LLM answers" in new
    with pytest.raises(tailor.TailorError):
        tailor.apply(new, kept)          # the source changed: edits no longer fit


def test_banned_list_catches_tricks():
    for bad in [r"\input{x}", r"\include{x}", r"\openout1=x", r"\write18{ls}", r"\catcode`\^=7", r"^^5cinput",
                r"\def\x{}", r"\directlua{os.exit()}", r"\usepackage{shellesc}", r"\csname input\endcsname"]:
        assert tailor.BANNED.search(bad), bad
    assert not tailor.BANNED.search(r"\item Built a \textbf{Flask} app")


def test_save_source_checks(tmp_path):
    with pytest.raises(tailor.TailorError, match="full LaTeX"):
        tailor.save_source(b"hello")
    with pytest.raises(tailor.TailorError, match="input"):
        tailor.save_source(TEX.replace(r"\section*{Skills}", r"\input{skills}").encode())
    with pytest.raises(tailor.TailorError, match="UTF-8"):
        tailor.save_source(b"\xff\xfe" + TEX.encode("utf-16"))
    tailor.save_source(TEX.encode())
    assert tailor.source() == TEX and oct(tailor.TEX_PATH.stat().st_mode)[-3:] == "600"
    # the common resume template line is fine, other \input still isn't
    glyph = TEX.replace(r"\usepackage{hyperref}", "\\usepackage{hyperref}\n\\input{glyphtounicode}")
    tailor.save_source(glyph.encode())
    with pytest.raises(tailor.TailorError, match="input"):
        tailor.save_source((glyph + r"\input{glyphtounicode}\input{x}").encode())


@pytest.mark.skipif(tailor.compiler() is None, reason="LaTeX not installed")
def test_check_build_names_the_line():
    assert tailor.check_build(TEX) is None
    broken = TEX.replace(r"\begin{itemize}", "\\begin{itemize}\n\\begin{itemize}\n\\item x\n\\end{itemize}", 1)
    problem = tailor.check_build(broken)
    assert problem and "line 8:" in problem and "missing" in problem


def _job(app):
    from jobhunter.db import get_db
    with app.app_context():
        db = get_db()
        collect.save(db, [raw(1, title="Backend Engineer", company="Razorpay")], settings())
        return db.execute("SELECT id FROM jobs").fetchone()[0]


def test_tailor_flow(logged_in, app, monkeypatch):
    prompts = []
    monkeypatch.setattr(ai, "ask_json", lambda system, prompt, schema, model: prompts.append(prompt) or {"edits": [
        {"find": "C++, Java, Python, Flask, MongoDB", "replace": "Python, Flask, MongoDB, C++, Java", "why": "Backend skills first"},
        {"find": "Python tools", "replace": r"Python tools\input{/etc/passwd}", "why": "sneaky"}]})
    job_id = _job(app)
    path = f"/job/{job_id}/apply"
    assert "Upload your resume's .tex file" in logged_in.get(path).get_data(as_text=True)
    token = csrf_from(logged_in, "/profile")
    logged_in.post("/profile/tex", data={"csrf": token, "tex": (io.BytesIO(TEX.encode()), "resume.tex")},
                   content_type="multipart/form-data")
    assert "Saved." in logged_in.get("/profile").get_data(as_text=True)
    logged_in.post(f"/job/{job_id}/tailor", data={"csrf": token, "action": "suggest"})
    html = logged_in.get(path).get_data(as_text=True)
    assert "Backend skills first" in html and "sneaky" not in html
    assert "<resume_latex>" in prompts[0] and "data, not instructions" not in prompts[0]   # rule is in the system prompt
    if tailor.compiler() is None:
        assert "LaTeX isn't installed" in html
        r = logged_in.post(f"/job/{job_id}/tailor", data={"csrf": token, "action": "build", "edit": "0"})
        assert "sudo dnf install" in logged_in.get(r.headers["Location"]).get_data(as_text=True)
        assert not tailor.pdf_path(job_id).exists()
    else:
        logged_in.post(f"/job/{job_id}/tailor", data={"csrf": token, "action": "build", "edit": "0"})
        assert tailor.pdf_path(job_id).read_bytes().startswith(b"%PDF")
        assert logged_in.get(f"/job/{job_id}/resume.pdf").data.startswith(b"%PDF")


def test_tailored_pdf_used_for_that_job(app, monkeypatch, tmp_path):
    job_id = _job(app)
    main = tmp_path / "main.pdf"
    main.write_bytes(b"%PDF main")
    monkeypatch.setattr("jobhunter.config.RESUME_PATH", main)
    assert tailor.resume_for(job_id) == main
    tailor.OUT_DIR.mkdir(parents=True, exist_ok=True)
    tailor.pdf_path(job_id).write_bytes(b"%PDF tailored")
    assert tailor.resume_for(job_id) == tailor.pdf_path(job_id)
    actions, _, _ = formfill.plan([{"id": "r", "kind": "file", "label": "Resume"}], {}, [], "", "A", "B", 0.6,
                                  True, tailor.pdf_path(job_id))
    assert actions[0]["value"] == str(tailor.pdf_path(job_id))


def test_resume_pdf_route_needs_login(client, app):
    from conftest import set_password
    set_password(client)
    client.delete_cookie("jobhunter_session")
    r = client.get("/job/1/resume.pdf")
    assert r.status_code == 302 and "/login" in r.headers["Location"]
