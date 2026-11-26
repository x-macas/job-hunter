-- One row only (id = 1): your website password and the lockout counter.
CREATE TABLE IF NOT EXISTS auth (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    password_hash TEXT    NOT NULL,
    failed_count  INTEGER NOT NULL DEFAULT 0,
    locked_until  REAL    NOT NULL DEFAULT 0,
    created_at    REAL    NOT NULL
);

-- Everything you can change on the Settings page. Value is stored as JSON.
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at REAL NOT NULL
);

-- Your details from the Profile page (name, links, notice period, ...).
CREATE TABLE IF NOT EXISTS profile (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at REAL NOT NULL
);

-- Everything the website does, with a time. Shown on the Activity log page.
CREATE TABLE IF NOT EXISTS activity (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    kind    TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS activity_ts ON activity (ts DESC);

-- Every job the website has seen, from every source.
CREATE TABLE IF NOT EXISTS jobs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    source         TEXT NOT NULL,          -- greenhouse, lever, ashby, remotive, remoteok, hn, adzuna, link
    source_id      TEXT NOT NULL,
    source_name    TEXT NOT NULL,          -- shown on the card, e.g. "Remotive"
    company        TEXT NOT NULL,
    title          TEXT NOT NULL,
    location       TEXT NOT NULL DEFAULT '',
    remote         INTEGER NOT NULL DEFAULT 0,
    url            TEXT NOT NULL,          -- the job page (always linked on the card)
    apply_url      TEXT,
    description    TEXT,                   -- plain text, filled only for jobs that pass the first rules
    posted_at      REAL,
    first_seen     REAL NOT NULL,
    last_seen      REAL NOT NULL,
    job_type       TEXT NOT NULL DEFAULT 'unknown',   -- full_time, intern, part_time, contract, unknown
    salary_text    TEXT NOT NULL DEFAULT '',
    salary_min_lpa REAL,
    salary_max_lpa REAL,
    min_years      REAL,
    rule_ok        INTEGER,                -- 1 kept, 0 dropped, NULL not checked
    rule_reason    TEXT NOT NULL DEFAULT '',
    ai_score       INTEGER,
    ai_json        TEXT,
    ai_at          REAL,
    status         TEXT NOT NULL DEFAULT 'new',       -- new, skipped, applied
    added_by       TEXT NOT NULL DEFAULT 'auto',      -- auto, watch, link
    alerted        INTEGER NOT NULL DEFAULT 0,
    UNIQUE (source, source_id)
);
CREATE INDEX IF NOT EXISTS jobs_company ON jobs (company);
CREATE INDEX IF NOT EXISTS jobs_seen ON jobs (last_seen);

-- Why you skipped a job. Used to rank future jobs better.
CREATE TABLE IF NOT EXISTS skips (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id  INTEGER NOT NULL REFERENCES jobs (id),
    reason  TEXT NOT NULL,
    ts      REAL NOT NULL
);

-- The daily list: which jobs were picked for which day, in what order.
CREATE TABLE IF NOT EXISTS picks (
    day     TEXT NOT NULL,
    job_id  INTEGER NOT NULL REFERENCES jobs (id),
    rank    INTEGER NOT NULL,
    final   REAL NOT NULL,
    PRIMARY KEY (day, job_id)
);

-- When each background task last ran.
CREATE TABLE IF NOT EXISTS runs (
    name          TEXT PRIMARY KEY,
    last_started  REAL,
    last_finished REAL,
    last_ok       INTEGER,
    message       TEXT NOT NULL DEFAULT ''
);

-- Companies you ticked on the Check page, per day.
CREATE TABLE IF NOT EXISTS hand_checks (
    day      TEXT NOT NULL,
    company  TEXT NOT NULL,
    ts       REAL NOT NULL,
    PRIMARY KEY (day, company)
);

-- Check page, "paste the page": the job titles seen on a company's careers page
-- the last time you pasted it, so the next paste shows only the new ones.
CREATE TABLE IF NOT EXISTS pasted_pages (
    company   TEXT PRIMARY KEY,
    titles    TEXT NOT NULL,          -- JSON {title: how many times}
    ts        REAL NOT NULL,          -- when you pasted it
    prev_ts   REAL,                   -- the paste before that
    last_new  TEXT NOT NULL DEFAULT '[]'  -- JSON list of what was new this time
);

-- Phase 2. Jobs you applied to, and where each one stands (the Tracker board).
CREATE TABLE IF NOT EXISTS applications (
    job_id      INTEGER PRIMARY KEY REFERENCES jobs (id),
    stage       TEXT NOT NULL DEFAULT 'applied',  -- applied, emailed, replied, interview, offer, rejected
    applied_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    note        TEXT NOT NULL DEFAULT ''
);

-- Your saved answers to form questions. {company} and {role} are filled in per job.
CREATE TABLE IF NOT EXISTS answers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    question    TEXT NOT NULL,
    answer      TEXT NOT NULL DEFAULT '',
    used        INTEGER NOT NULL DEFAULT 0,
    updated_at  REAL NOT NULL
);

-- Per-job text: the cover note, and questions the form filler couldn't answer.
CREATE TABLE IF NOT EXISTS job_notes (
    job_id      INTEGER NOT NULL REFERENCES jobs (id),
    kind        TEXT NOT NULL,       -- cover, unanswered, fill_log
    text        TEXT NOT NULL,
    ts          REAL NOT NULL,
    PRIMARY KEY (job_id, kind)
);

-- Job-alert emails already read (by Message-ID), so each is read once.
CREATE TABLE IF NOT EXISTS email_seen (
    msg_id  TEXT PRIMARY KEY,
    ts      REAL NOT NULL,
    jobs    INTEGER NOT NULL DEFAULT 0
);
