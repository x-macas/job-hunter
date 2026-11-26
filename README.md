# job-hunter

A personal website that finds jobs for me, helps me apply, and keeps track of everything.

I built it because looking for jobs and filling forms every day was tiring. Now the website
does the boring parts, and I spend about 15 minutes a day on it.

## What it does

- **Finds jobs** every morning from company job boards, job sites and job-alert emails
- **Picks the best 20** for me based on my resume and my rules (role, pay, location, experience)
- **Fills application forms** in a browser, and I check and click Submit myself
- **Writes short emails** to HR, hiring managers, employees and alumni, and sends them after I approve
- **Follows up** if nobody replies, and tells me on Telegram when someone does
- **Tracks everything** on a board: applied, emailed, replied, interview
- **Helps me prepare** for interviews with company info and real questions people were asked

## Status

| Phase | What | Status |
|---|---|---|
| 0 | Website base: login, settings, profile, activity log, emergency stop | Done |
| 1 | Finding and ranking jobs, daily list, alerts | Mostly done |
| 2 | Applying: saved answers, form filling, tracker | Next |
| 3 | Emails: drafts, approvals, sending, follow-ups, replies | |
| 4 | Company info and interview prep | |
| 5 | Stats, goals, backups, phone access | |
| 6 | A tweaked resume for each job (LaTeX) | |

## Built with

Python, Flask, SQLite, Waitress, systemd timers, Telegram Bot API, Claude (via the `claude` CLI, no tools).
Later: Playwright, Gmail API.

## Job sources

- 110 company job boards (Greenhouse, Lever, Ashby, SmartRecruiters, Workable)
- 45 big-company careers sites read directly (Amazon, Atlassian, JPMorgan, Oracle, and 41 on Workday
  such as Walmart, Salesforce, Nvidia, Adobe, Micron, Target, BlackRock). India-only searches, once a day,
  at most 3 requests at a time, with polite retries. Sites that block automated reading are left alone.
- Remotive and Remote OK (each job links back to them, checked at most a few times a day)
- Hacker News "Who is hiring?"
- Adzuna India (free key)
- Coming: job-alert emails from LinkedIn, Naukri, Instahyre, Cutshort, Wellfound

## How a job is picked

1. **Rules** (plain code, instant): role, title words, level, location, age, job type,
   experience, pay, bonds or fees, blocked companies.
2. **Claude** reads the jobs that pass and scores each one against my resume. It has no tools:
   it only returns a score and short reasons. Job text is treated as data, never as instructions.
3. **Ranking**: big companies get a boost and saved spots, fresh jobs a small boost,
   unknown pay at startups a small penalty. Top 20 go on the Today page.

## Security

It runs on my own laptop and only listens on `127.0.0.1`, so it isn't on the public internet.

- One password, stored as a hash. 5 wrong tries locks the login for 15 minutes.
- Every form has a CSRF token. Strict cookie and browser security headers.
- Personal data (`data/`) and keys (`secrets/`) are only readable by my user and never go to Git.
  A pre-commit check blocks commits that contain them or anything that looks like a key.
- An activity log of everything it does, and a Stop button that pauses all sending at once.

## Run it

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
git config core.hooksPath .githooks
.venv/bin/python run.py                    # website at http://127.0.0.1:8000
.venv/bin/python -m jobhunter.worker daily # find jobs now
.venv/bin/python -m pytest -q              # run the tests
sh deploy/install.sh                       # run everything in the background (systemd)
```
