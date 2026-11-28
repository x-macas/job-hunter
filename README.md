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
| 1 | Finding and ranking jobs, daily list, alerts | Done (email parts wait for Gmail) |
| 2 | Applying: saved answers, cover notes, form filling, tracker | Done, being tried |
| 3 | Emails: drafts, approvals, sending, follow-ups, replies, LinkedIn notes | Built, waits for Gmail |
| 4 | Company info, interview prep, mock interview | Built |
| 5 | Stats, skill gaps, goals and streak, evening nudge, backups, phone access | Built (phone access waits) |
| 6 | A tweaked resume for each job (LaTeX) | Built, needs LaTeX installed + my .tex |

## Built with

Python, Flask, SQLite, Waitress, systemd timers, Telegram Bot API, Claude (via the `claude` CLI, no tools),
Playwright (form filling), Gmail over IMAP/SMTP with an app password.

## Job sources

- 176 company job boards (Greenhouse, including its European site, Lever, Ashby, SmartRecruiters,
  Workable, Jobvite, Freshteam, mynexthire, Recruiterbox, Rippling, Kula, Pinpoint, Gem, Recruitee,
  JazzHR, Keka, PyjamaHR, Recruiterflow and Zoho Recruit job pages)
- 153 company careers sites read directly: Workday (Walmart, Salesforce, Citi, Nvidia, Adobe, ModMed,
  Medtronic, Amgen, Eli Lilly...), Oracle Recruiting (JPMorgan, Oracle, Uber, BNY, Kotak...), Amazon,
  Atlassian, Avature (EA, Tesco), Eightfold (Microsoft, Qualcomm, Ericsson...), iCIMS Jibe (AMD, DocuSign,
  Booking.com), SAP's own site, MediaTek's own site and a few companies' own public job lists or plain job
  pages (Wayfair, Zerodha, MakeMyTrip, Fidelity, iRage, Jio, Scaler, Juspay, Magicpin, Tally, Shiprocket).
  When a list says how many years a job asks for, that goes to the experience rule too.
  India-only searches, at most 3 requests at a time, with polite retries.
- 21 companies whose sitemap lists their job pages but not the city (Teradata, D. E. Shaw India, Dassault,
  Quadeye, Times Internet, Deel, iCIMS sites like Rambus and MSCI, and sites on Trakstar Hire like Easebuzz and
  MoEngage): each new job page is opened once, one per second or slower, for its title and city, and
  never opened again.
- 3 companies whose robots.txt asks bots to stay away but don't block (Futures First, Urban Company,
  Lenskart). They are read only if Settings allow it ("Also read careers sites whose robots.txt asks
  bots to stay away"), and only every few hours. Sites that really block are never read.
- **Every 30 minutes** a quick check reads the newest jobs on every company site and sends a
  Telegram alert for new ones that fit. The full lists are read once each morning.
- 14 companies whose job search asks bots to stay away, but whose robots.txt points to a sitemap
  (a plain list of every job page): Optum, Arm, NetApp, Moody's, Nomura, Standard Chartered, Swiss Re,
  Synopsys, Bloomberg, Intuit and others.
  Only the sitemap is read, every 12 hours. The title and city come from each link, and a link that
  wasn't there before is a new job. Our own robots.txt reader checks every link first.
- Sites that refuse plain requests or use a bot check are left alone. On the Check page you can
  paste such a site's job list and see only the titles that are new since your last paste.
- Remotive and Remote OK (each job links back to them, checked at most a few times a day)
- Hacker News "Who is hiring?"
- Adzuna India (free key)
- Job-alert emails in Gmail (LinkedIn, Naukri, Instahyre, Cutshort, Wellfound, Indeed, foundit and
  companies' own alerts), read every check once Gmail is connected with an app password. Claude picks out
  the jobs but can only point at links that are really in the email.

## How applying works

1. **Apply here** on a job opens its Apply page.
2. **Cover note**: Claude writes a short one from my resume and the job post. I edit it.
3. **Fill the form for me** opens the job's form in a separate, empty browser (no saved logins) and fills
   what it knows: my Profile (name, email, phone, links, college, CTC, notice period, country), my saved
   answers (matched even when a form words the question differently), the resume file and the cover note.
   It never clicks Submit, never ticks "I agree" boxes, never touches password boxes, and never changes
   a box that already has something in it. Custom drop-downs are only picked when an option really matches.
4. Questions it couldn't answer are listed on the Apply page with a box to answer them once, for every form after.
5. **I applied** puts the job on the Tracker board (Applied, Emailed, Replied, Interview, Offer / Rejected),
   with a note per job and a flag when there's been no reply for a while.

## How emails work

- People to email come from a contact search: the company's own website (the careers@/hr@/jobs@ addresses it
  publishes) and Hunter.io (free key: names, job titles and work emails, with how sure it is). Found people
  are suggestions on the job's page; I add the ones I want. I can also add people myself, and a job's official
  apply email is offered too. An address is never taken from a job post's text.
- Each day the website looks up people for the top few jobs on its own (Settings), and saves what it finds.
- Claude writes a short, plain draft with my resume attached. It waits in the Outbox until I approve it.
- After I approve 2 emails of one kind without changing them, that kind sends on its own: new drafts wait
  30 minutes (so I can still stop them), then go out. If I change or reject one, it needs 2 approvals again.
- At most 15 emails a day. The Stop button stops everything. Every send is in the activity log.
- Follow-ups are drafted after 5 and 10 days if nobody replied, in the same email thread.
- Replies move the job to Replied and send a short Telegram alert. The website never answers people itself.
- Alert emails and replies count only when Gmail itself confirms the sender (DMARC/DKIM/SPF pass), so a
  look-alike address can't sneak links in.

## How a job is picked

1. **Rules** (plain code, instant): role, title words, level, location, age, job type,
   experience, pay, bonds or fees, blocked companies.
2. **Claude** reads the jobs that pass and scores each one against my resume. It has no tools:
   it only returns a score and short reasons. Job text is treated as data, never as instructions.
3. **Ranking**: big companies get a boost and saved spots, fresh jobs a small boost,
   unknown pay at startups a small penalty. Top 20 go on the Today page.

## A resume tweaked for each job

Upload the resume's `.tex` on the Profile page. On a job's page, **Suggest edits** asks Claude for a few small
changes (reorder skills, move the best bullet up, use the job's words for things I really did). It can only
change text that is already there. Our code drops any edit that isn't an exact single match, breaks braces, or
adds a LaTeX command the resume doesn't already use (so nothing that reads or writes files or runs programs).
I tick the edits I want; it's compiled with shell escape OFF and file access limited to its own folder.
Emails and the form filler then use that PDF for that job. Needs LaTeX: `sudo dnf install texlive-scheme-medium`.

## Backups

Once a day the website saves one `.tar.gz` in `~/job-hunter-backups` (Settings), on this laptop only:
the database, the resume and a few small data files. The newest 30 are kept. Passwords and tokens are not
in it, and it refuses a USB stick or a cloud-synced folder. **Back up now** is on the Stats page.

To restore: stop the website (`systemctl --user stop job-hunter-web job-hunter-tick.timer`), unpack a backup
into `data/` (`tar -xzf ~/job-hunter-backups/job-hunter-....tar.gz -C data/`), then start them again.

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
