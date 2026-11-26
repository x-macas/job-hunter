"""Fill a job's application form in a separate browser. You check it and click Submit.

    python -m jobhunter.formfill <job id>     # what the "Fill the form for me" button runs

Safety (security rule 7):
- A separate, empty browser profile (data/browser-profile): no saved logins, no
  cookies from your normal browser.
- It NEVER clicks Submit or Apply. It types into boxes, picks options, attaches
  your resume, then leaves the window open for you.
- Consent boxes ("I agree to the privacy policy") are left for you to tick.
- Passwords, captchas and hidden boxes are never touched.
- The page is untrusted: we only read the labels of its boxes, nothing on the
  page is run as an instruction.

How a box is filled, in order:
1. Your Profile (name, email, phone, links, college, CTC, notice period...).
2. Your saved Answers, when the question is close enough (Settings).
3. The cover note, for a "cover letter" box.
Anything else is left empty and listed on the Apply page, so you can save an
answer for next time.
"""

import fcntl
import importlib.util
import re
import subprocess
import sys
import time
from pathlib import Path

from . import answers as answer_bank
from .config import DATA_DIR, PROJECT_DIR, RESUME_PATH

PROFILE_DIR = DATA_DIR / "browser-profile"
LOCK = DATA_DIR / "formfill.lock"
LOG = DATA_DIR / "formfill.log"
WAIT_FOR_YOU = 45 * 60  # seconds the window stays open for you before the helper quits
LABEL_MAX = 300

# (pattern on the box's label, where the value comes from). First match wins.
PROFILE_RULES = [
    (r"\bfirst\s*name|\bgiven\s*name", "first_name"),
    (r"\blast\s*name|\bsurname|\bfamily\s*name", "last_name"),
    (r"^\s*(full\s*)?name\s*\*?\s*$|\byour\s+name\b|\bfull\s*name|\blegal\s*name", "full_name"),
    (r"e-?mail", "email"),
    (r"phone|mobile|contact\s*number|whatsapp", "phone"),
    (r"linked\s*in", "linkedin_url"),
    (r"git\s*hub", "github_url"),
    (r"portfolio|personal\s*(web)?site|^\s*website", "portfolio_url"),
    (r"expected\s*(ctc|salary|compensation|pay)|salary\s*expectation|desired\s*(salary|pay)", "expected_ctc"),
    (r"current\s*(ctc|salary|compensation|pay)", "current_ctc"),
    (r"notice\s*period|when\s+can\s+you\s+(start|join)|earliest\s+(start|joining)", "notice_period"),
    (r"relocat", "relocate"),
    (r"graduation\s*(year|date)|year\s*of\s*(passing|graduation)|passing\s*year", "grad_year"),
    (r"\bc?gpa\b|\bcgpa\b", "cgpa"),
    (r"college|university|school\s*name|institut", "college"),
    (r"degree|qualification|field\s*of\s*study|major|branch", "degree"),
    (r"current\s*(company|employer|organi[sz]ation)", "current_company"),
    (r"country(?!.*(citizen|passport|nationality))", "country"),
    (r"current\s*(city|location)|^\s*(city|location)\s*\*?\s*$|where\s+do\s+you\s+live|^\s*location\b", "city"),
]
NEVER = re.compile(r"password|captcha|verification\s*code|otp\b|signature", re.I)
CONSENT = re.compile(r"\bi\s+(agree|accept|consent|confirm|acknowledge|certify)|privacy\s*policy|terms|consent", re.I)
RESUME = re.compile(r"resume|\bcv\b|curriculum", re.I)
COVER = re.compile(r"cover\s*letter|cover\s*note|motivation", re.I)


def status() -> dict:
    if importlib.util.find_spec("playwright") is None:
        return {"ready": False, "why": "The form filler needs Playwright. Run: .venv/bin/pip install -r requirements.txt"}
    if not any(Path.home().joinpath(".cache/ms-playwright").glob("chromium-*")):
        return {"ready": False, "why": "The form filler needs its browser. Run: .venv/bin/python -m playwright install chromium"}
    return {"ready": True, "why": ""}


def busy() -> bool:
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK, "a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(f, fcntl.LOCK_UN)
    return False


def start(job_id: int) -> None:
    log = open(LOG, "a")
    subprocess.Popen([sys.executable, "-B", "-m", "jobhunter.formfill", str(int(job_id))], cwd=PROJECT_DIR,
                     stdout=log, stderr=subprocess.STDOUT, start_new_session=True)


# ---------- deciding what goes in each box (no browser needed) ----------

def profile_values(prof: dict) -> dict:
    v = {k: (prof.get(k) or "").strip() for k in
         ("full_name", "email", "phone", "linkedin_url", "github_url", "portfolio_url", "expected_ctc",
          "current_ctc", "notice_period", "relocate", "grad_year", "cgpa", "college", "degree", "city",
          "current_company", "country")}
    parts = v["full_name"].split()
    v["first_name"] = parts[0] if parts else ""
    v["last_name"] = " ".join(parts[1:]) if len(parts) > 1 else ""
    return v


def _pick_option(answer: str, options: list[str]) -> str | None:
    """The option that matches an answer: 'Yes, anywhere' picks 'Yes'."""
    a = answer.strip().lower()
    if not a or not options:
        return None
    for o in options:
        if o.strip().lower() == a:
            return o
    first = re.match(r"(yes|no)\b", a)
    if first:
        for o in options:
            if re.match(rf"{first.group(1)}\b", o.strip().lower()):
                return o
    best, score = None, 0.0
    for o in options:
        sc = answer_bank.similarity(answer, o)
        if sc > score:
            best, score = o, sc
    return best if score >= 0.6 else None


def plan(fields: list[dict], prof: dict, saved, cover_note: str, company: str, role: str,
         min_score: float, have_resume: bool, resume_path=None) -> tuple[list[dict], list[str], list[str]]:
    """fields: [{id, label, kind, options, required}] read from the page.
    Returns (actions, unanswered questions, boxes left for you on purpose)."""
    pv = profile_values(prof)
    actions, unanswered, left = [], [], []
    for f in fields:
        label = " ".join((f.get("label") or "").split())[:LABEL_MAX]
        kind = f.get("kind", "text")
        if not label or NEVER.search(label):
            continue
        if kind == "file":
            if RESUME.search(label) and have_resume:
                actions.append({"id": f["id"], "kind": "file", "value": str(resume_path or RESUME_PATH),
                                "from": "resume", "label": label})
            elif f.get("required"):
                unanswered.append(label)
            continue
        if kind == "checkbox" and CONSENT.search(label):
            left.append(label)
            continue
        value, source = None, None
        if kind == "textarea" and COVER.search(label):
            value, source = (cover_note or None), "cover note"
        if value is None:
            for pattern, key in PROFILE_RULES:
                if re.search(pattern, label, re.I):
                    value, source = (pv.get(key) or None), "profile"
                    break
        if value is None:
            row, _score = answer_bank.best(label, saved, min_score)
            if row is not None:
                value, source = answer_bank.fill(row["answer"], company, role), f"answer #{row['id']}"
        if value is None:
            if f.get("required") or kind in ("textarea", "select", "radio"):
                unanswered.append(label)
            continue
        if kind == "combobox":
            actions.append({"id": f["id"], "kind": kind, "value": value, "from": source, "label": label})
            continue
        if kind in ("select", "radio"):
            option = _pick_option(value, f.get("options") or [])
            if option is None:
                unanswered.append(label)
                continue
            value = option
        elif kind == "checkbox":
            if not re.match(r"\s*yes\b", value, re.I):
                continue
            value = "check"
        actions.append({"id": f["id"], "kind": kind, "value": value, "from": source, "label": label})
    return actions, list(dict.fromkeys(unanswered)), list(dict.fromkeys(left))


# ---------- the browser part ----------

# Runs inside the page: list the visible boxes with their labels. Read only, plus a
# data-jh attribute on each box so we can find it again.
READ_FIELDS_JS = r"""
() => {
  const out = [];
  const seen = new Set();
  const text = el => (el ? (el.innerText || el.textContent || '') : '').replace(/\s+/g, ' ').trim();
  const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length) &&
                        getComputedStyle(el).visibility !== 'hidden';
  const WEAK = /^(attach|upload|browse|choose( a)? file|select|select\.\.\.|type your response|start typing\.*)?\s*[*✱]?$/i;
  // The question text for a box: its own label first, else the nearest container's label.
  // Climb only while the container holds no other box, so we never borrow another question's label.
  const others = (p, el) => [...p.querySelectorAll('input:not([type=hidden]), select, textarea')]
    .some(x => x !== el && !(el.type === 'radio' && x.type === 'radio' && x.name === el.name));
  const near = el => {
    let p = el.parentElement;
    for (let i = 0; i < 7 && p && !others(p, el); i++, p = p.parentElement) {
      for (const l of p.querySelectorAll('legend, label, .application-label, [class*="label"], [class*="question-title"]')) {
        if (l.contains(el) || l.querySelector('input, select, textarea')) continue;
        const tgt = l.htmlFor && document.getElementById(l.htmlFor);
        if (tgt && tgt.matches('input[type=radio], input[type=checkbox]')) continue;   // an option's own label
        const t = text(l);
        if (t && !WEAK.test(t)) return t;
      }
    }
    return '';
  };
  const labelOf = el => {
    let t = '';
    if (el.labels && el.labels.length) t = text(el.labels[0]);
    if (!t && el.getAttribute('aria-label')) t = el.getAttribute('aria-label');
    const by = el.getAttribute('aria-labelledby');
    if (!t && by) t = by.split(/\s+/).map(id => text(document.getElementById(id))).join(' ').trim();
    if (!t || WEAK.test(t)) t = near(el) || t;
    if (!t || WEAK.test(t)) t = (t + ' ' + (el.id || el.getAttribute('name') || '').replace(/[_-]+/g, ' ')).trim();
    if (!t) t = el.getAttribute('placeholder') || '';
    return t;
  };
  let n = 0;
  for (const el of document.querySelectorAll('input, textarea, select')) {
    const type = (el.getAttribute('type') || el.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'image', 'reset', 'password'].includes(type)) continue;
    if (type !== 'file' && !visible(el)) continue;
    if (el.disabled || el.readOnly) continue;
    if (type === 'radio') {
      const key = 'r:' + el.name;
      if (seen.has(key)) continue;
      seen.add(key);
      const group = [...document.querySelectorAll('input[type=radio]')].filter(r => r.name === el.name);
      const set = el.closest('fieldset');
      const q = (set ? text(set.querySelector('legend')) : '') || near(el);
      const id = 'jh' + (n++);
      group.forEach((r, i) => r.setAttribute('data-jh', id + '-' + i));
      out.push({id, kind: 'radio', label: q, required: group.some(r => r.required),
                options: group.map(r => text(r.closest('label')) || (r.labels && r.labels.length ? text(r.labels[0]) : r.value))});
      continue;
    }
    const id = 'jh' + (n++);
    el.setAttribute('data-jh', id);
    const combo = el.getAttribute('role') === 'combobox' || el.getAttribute('aria-autocomplete') === 'list';
    const kind = el.tagName === 'SELECT' ? 'select' : el.tagName === 'TEXTAREA' ? 'textarea' :
                 (type === 'checkbox' ? 'checkbox' : type === 'file' ? 'file' : combo ? 'combobox' : 'text');
    let label = labelOf(el);
    if (kind === 'file') label = (label + ' ' + (el.id || '') + ' ' + (el.getAttribute('name') || '')).replace(/[_-]+/g, ' ').trim();
    out.push({id, kind, label, required: el.required || el.getAttribute('aria-required') === 'true',
              options: el.tagName === 'SELECT' ? [...el.options].map(o => text(o)).filter(Boolean) : []});
  }
  return out;
}
"""


def _combobox(frame, box, value: str) -> bool:
    """A custom drop-down: type the answer, then click an option only if its text really matches.
    If nothing matches, the box is cleared and left for you."""
    box.click(timeout=3000)
    box.fill(value[:60], timeout=3000)
    frame.wait_for_timeout(600)
    options = frame.locator('[role="option"]:visible')
    texts = [t.strip() for t in options.all_inner_texts()][:50]
    pick = _pick_option(value, texts)
    if pick is None:
        box.fill("", timeout=2000)
        box.press("Escape")
        return False
    options.nth(texts.index(pick)).click(timeout=3000)
    return True


def apply_actions(frame, fields: list[dict], actions: list[dict]) -> tuple[int, list[str]]:
    """Returns (boxes filled, questions that couldn't be filled after all)."""
    by_id = {f["id"]: f for f in fields}
    done, missed = 0, []
    for a in actions:
        try:
            if a["kind"] == "combobox":
                if _combobox(frame, frame.locator(f'[data-jh="{a["id"]}"]'), a["value"]):
                    done += 1
                else:
                    missed.append(a["label"])
                continue
            if a["kind"] == "radio":
                idx = by_id[a["id"]]["options"].index(a["value"])
                frame.locator(f'[data-jh="{a["id"]}-{idx}"]').check(timeout=3000)
            else:
                box = frame.locator(f'[data-jh="{a["id"]}"]')
                if a["kind"] == "file":
                    box.set_input_files(a["value"], timeout=5000)
                elif a["kind"] == "select":
                    box.select_option(label=a["value"], timeout=3000)
                elif a["kind"] == "checkbox":
                    box.check(timeout=3000)
                elif not box.input_value(timeout=2000).strip():   # never overwrite what's there
                    box.fill(a["value"], timeout=3000)
            done += 1
        except Exception:  # one odd box must not stop the rest
            if a.get("label"):
                missed.append(a["label"])
            continue
    return done, missed


def form_url(job) -> str:
    """Go straight to the form where the job board has a separate form page."""
    url = job["apply_url"] if (job["apply_url"] or "").startswith("https://") else job["url"]
    if job["source"] == "lever" and not url.rstrip("/").endswith("/apply"):
        return url.rstrip("/") + "/apply"
    if job["source"] == "ashby" and not url.rstrip("/").endswith("/application"):
        return url.rstrip("/") + "/application"
    return url


def run(job_id: int) -> str:
    from playwright.sync_api import sync_playwright

    from . import cover, profile_store, settings_store, tailor
    from .config import DB_PATH, make_private_dirs
    from .db import connect
    make_private_dirs()
    conn = connect(DB_PATH)
    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if job is None:
        return "No such job."
    url = form_url(job)
    if not url.startswith("https://"):
        return "The job link isn't a normal https link, so the form wasn't opened."
    s = settings_store.get_all(conn)
    prof = profile_store.get_all(conn)
    saved = answer_bank.all_answers(conn)
    note = cover.get(conn, job_id)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.chmod(0o700)
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(str(PROFILE_DIR), headless=False, accept_downloads=False,
                                                    viewport=None)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        filled, unanswered, left = 0, [], []
        for frame in page.frames:   # some forms sit inside a frame (Greenhouse on a company site)
            try:
                fields = frame.evaluate(READ_FIELDS_JS)
            except Exception:
                continue
            if not fields:
                continue
            resume = tailor.resume_for(job_id)
            actions, un, lf = plan(fields, prof, saved, note, job["company"], job["title"],
                                   s["answer_match_min"] / 100, resume.exists(), resume)
            n, missed = apply_actions(frame, fields, actions)
            filled += n
            unanswered += un + missed
            left += lf
            for a in actions:
                if a["from"].startswith("answer #"):
                    answer_bank.mark_used(conn, int(a["from"][8:]))
        msg = (f"Filled {filled} boxes on {time.strftime('%d %b, %I:%M %p')}."
               + (f" Tick these yourself: {'; '.join(left[:5])}." if left else "")
               + ("" if filled else " Nothing to fill on that page: it may need you to press Apply first, "
                  "then press Fill again."))
        cover.put(conn, job_id, "\n".join(dict.fromkeys(unanswered)), "unanswered")
        cover.put(conn, job_id, msg, "fill_log")
        # Leave the window for you. The helper quits when you close it, or after a while.
        try:
            page.wait_for_event("close", timeout=WAIT_FOR_YOU * 1000)
        except Exception:
            pass
        try:
            ctx.close()
        except Exception:
            pass
    conn.close()
    return msg


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not argv[1].isdigit():
        print(__doc__)
        return 2
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK, "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("A form is already open. Close that window first.")
            return 0
        print(run(int(argv[1])), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
