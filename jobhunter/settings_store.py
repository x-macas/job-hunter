"""All the rules you can change on the Settings page.

Each setting has a type, a default and limits. The defaults here are
general. Your own choices are stored in the database, so they never
appear in the code on GitHub.
"""

import json
import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    group: str
    kind: str  # "int", "list", "bool" or "text"
    default: object
    help: str = ""
    min: int | None = None
    max: int | None = None
    choices: tuple = field(default_factory=tuple)


SETTINGS = [
    # Jobs
    Setting("roles", "Roles to look for", "Jobs", "list", [
        "Software Engineer", "SDE", "SWE", "Member of Technical Staff",
        "Backend Developer", "Backend Engineer", "Forward Deployed Engineer",
        "Python Developer", "Java Developer", "Node.js Developer", "C++ Developer",
        "Platform Engineer", "Infrastructure Engineer", "Graduate Software Engineer",
        "Associate Software Engineer", "Trainee Software Engineer",
        "Full Stack Developer", "AI Engineer", "LLM Engineer",
        "Solutions Engineer", "Applications Engineer",
    ], "One per line. A job title must contain one of these."),
    Setting("title_blocklist", "Hide titles with these words", "Jobs", "list", [
        "Senior", "Sr.", "Lead", "Staff", "Principal", "Manager", "Director",
        "Architect", "Head of", "VP", "QA", "Test Engineer", "Support", "Sales",
        "Frontend",
    ], "One per line."),
    Setting("max_experience_years", "Most years of experience a job can ask for", "Jobs",
            "int", 1, min=0, max=10),
    Setting("locations", "Locations", "Jobs", "list",
            ["India", "Remote"], "One per line. 'Remote' covers remote jobs anywhere."),
    Setting("max_job_age_days", "Hide jobs older than (days)", "Jobs", "int", 7, min=1, max=60),
    Setting("daily_top_n", "Jobs on the Today page", "Jobs", "int", 20, min=5, max=100),
    Setting("other_companies_fulltime_only", "Other companies: full-time jobs only", "Jobs",
            "bool", True, "Big companies can show internships, part-time and contract too."),
    Setting("skills", "Your skills", "Jobs", "list", [],
            "One per line. Jobs using these get a higher score."),

    # Pay
    Setting("min_lpa_fulltime", "Lowest pay for full-time jobs (LPA)", "Pay", "int", 10,
            "Only used when the job lists a salary.", min=0, max=200),
    Setting("min_lpa_startup_service", "Lowest pay for startups and service companies (LPA)",
            "Pay", "int", 12, "Jobs with no salary listed are still shown, ranked lower.",
            min=0, max=200),

    # Companies
    Setting("big_companies", "Big companies", "Companies", "list", [
        "Google", "Microsoft", "Amazon", "Apple", "Meta", "Netflix", "Adobe",
        "Salesforce", "Oracle", "SAP", "IBM", "Intel", "Nvidia", "Qualcomm", "Cisco",
        "Uber", "LinkedIn", "Atlassian", "Intuit", "PayPal", "ServiceNow", "Nutanix",
        "Rubrik", "Goldman Sachs", "JPMorgan Chase", "Morgan Stanley", "D. E. Shaw",
        "Tower Research", "Walmart Global Tech", "Flipkart", "Razorpay", "PhonePe",
        "Paytm", "Zomato", "Swiggy", "CRED", "Zoho", "Freshworks", "Meesho",
        "Myntra", "Postman", "BrowserStack", "Media.net", "Samsung",
    ], "One per line. These can show any job type and get ranked first."),
    Setting("big_company_boost", "Score boost for big companies", "Companies", "int", 15,
            min=0, max=50),
    Setting("big_company_spots", "Spots kept for big companies on the Today page",
            "Companies", "int", 10, min=0, max=100),
    Setting("blocked_companies", "Never show or email", "Companies", "list", [],
            "One per line."),
    Setting("watch_list", "Watch list (instant alert)", "Companies", "list", [],
            "One per line. New matching jobs here send a Telegram alert right away."),
    Setting("watch_interval_minutes", "Check watch list every (minutes)", "Companies", "int",
            30, min=15, max=720),

    # Emails
    Setting("email_daily_cap", "Most emails per day", "Emails", "int", 15, min=0, max=40),
    Setting("approvals_per_kind", "Approvals needed per email kind before auto-send",
            "Emails", "int", 2, min=1, max=50),
    Setting("followup_1_days", "First follow-up after (days)", "Emails", "int", 5,
            min=1, max=30),
    Setting("followup_2_days", "Second follow-up after (days)", "Emails", "int", 10,
            min=2, max=60),

    # Daily routine
    Setting("run_after_hour", "Daily run: first laptop start after (hour, 0-23)",
            "Daily routine", "int", 9, min=0, max=23),
    Setting("goal_applications", "Daily goal: applications", "Daily routine", "int", 3,
            min=0, max=50),
    Setting("goal_emails", "Daily goal: emails", "Daily routine", "int", 5, min=0, max=50),
    Setting("nudge_hour", "Evening nudge at (hour, 0-23)", "Daily routine", "int", 19,
            min=0, max=23),

    # Backup
    Setting("backup_dir", "Backup folder", "Backup", "text", "~/job-hunter-backups",
            "A folder on this laptop."),
    Setting("backup_keep", "Backups to keep", "Backup", "int", 30, min=1, max=365),
]

BY_KEY = {s.key: s for s in SETTINGS}
GROUPS = list(dict.fromkeys(s.group for s in SETTINGS))

# The emergency stop. Not on the Settings form; it has its own button.
PAUSED_KEY = "paused"


def get(conn, key: str):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is not None:
        return json.loads(row["value"])
    if key == PAUSED_KEY:
        return False
    return BY_KEY[key].default


def get_all(conn) -> dict:
    return {s.key: get(conn, s.key) for s in SETTINGS}


def put(conn, key: str, value) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
        (key, json.dumps(value), time.time()),
    )


def parse_form(form) -> tuple[dict, dict]:
    """Turn the Settings form into clean values.

    Returns (values, errors). Nothing is saved if there are any errors.
    """
    values, errors = {}, {}
    for s in SETTINGS:
        raw = form.get(s.key, "")
        if s.kind == "bool":
            values[s.key] = raw == "on"
        elif s.kind == "list":
            items = [line.strip() for line in raw.splitlines()]
            values[s.key] = list(dict.fromkeys(i for i in items if i))[:500]
        elif s.kind == "text":
            values[s.key] = raw.strip()[:300]
        elif s.kind == "int":
            try:
                number = int(raw.strip())
            except ValueError:
                errors[s.key] = "Enter a whole number."
                continue
            if s.min is not None and number < s.min:
                errors[s.key] = f"Must be at least {s.min}."
            elif s.max is not None and number > s.max:
                errors[s.key] = f"Must be at most {s.max}."
            else:
                values[s.key] = number
    if (
        "followup_1_days" in values
        and "followup_2_days" in values
        and values["followup_2_days"] <= values["followup_1_days"]
    ):
        errors["followup_2_days"] = "Must be later than the first follow-up."
    return values, errors
