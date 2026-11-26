"""The background worker.

    python -m jobhunter.worker tick     # what the timer runs every 15 minutes
    python -m jobhunter.worker daily    # collect + rules + score + today's list, now
    python -m jobhunter.worker watch    # check watch-list companies, now
    python -m jobhunter.worker refresh  # re-run rules, scoring and list on saved jobs (no download)
    python -m jobhunter.worker status   # when things last ran

'tick' decides what is due:
- the daily run: once a day, the first tick after the hour in Settings (9 AM),
  so it runs at 9 if the laptop is on, or right after you switch it on later.
- the watch-list check: every N minutes (Settings, at least 15).
Nothing runs while the emergency stop is on.
"""

import fcntl
import sys
import time
from datetime import date, datetime

from . import activity, collect, ranking, rules, scoring, settings_store, telegram
from .config import DATA_DIR, DB_PATH, make_private_dirs
from .db import connect, init_db
from .sources import portals

RETRY_AFTER = 30 * 60   # if a daily run failed, try again after 30 minutes


def _log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def _mark(conn, name, started=None, ok=None, message=""):
    if started is not None:
        conn.execute("INSERT INTO runs (name, last_started) VALUES (?, ?) "
                     "ON CONFLICT(name) DO UPDATE SET last_started = excluded.last_started", (name, started))
    if ok is not None:
        conn.execute("UPDATE runs SET last_finished = ?, last_ok = ?, message = ? WHERE name = ?",
                     (time.time(), int(ok), message[:300], name))
    conn.commit()


def _run_row(conn, name):
    return conn.execute("SELECT * FROM runs WHERE name = ?", (name,)).fetchone()


def daily(conn, s) -> str:
    _mark(conn, "daily", started=time.time())
    try:
        jobs = collect.fetch_all(s, log=_log)
        new = collect.save(conn, jobs, s)
        _log(f"{len(new)} new jobs saved.")
        collect.apply_rules(conn, s, log=_log)
        scored = scoring.score_pending(conn, s, limit=s["ai_jobs_per_run"], log=_log)
        _log(f"Claude scored {scored} jobs.")
        picked = ranking.pick_today(conn, s)
    except Exception as e:
        _mark(conn, "daily", ok=False, message=str(e))
        activity.log(conn, "worker", f"Daily run failed: {e}")
        raise
    msg = f"Daily run: {len(jobs)} collected, {len(new)} new, {scored} scored, {picked} on today's list."
    _mark(conn, "daily", ok=True, message=msg)
    activity.log(conn, "worker", msg)
    _send_daily_alert(conn, picked)
    return msg


def _send_daily_alert(conn, picked: int) -> None:
    if not picked:
        return
    top = conn.execute(
        "SELECT j.title, j.company FROM picks p JOIN jobs j ON j.id = p.job_id "
        "WHERE p.day = ? ORDER BY p.rank LIMIT 3", (date.today().isoformat(),)).fetchall()
    lines = [f"{picked} jobs are ready for you today. Top ones:"]
    lines += [f"- {r['title']} at {r['company']}" for r in top]
    try:
        if telegram.send("\n".join(lines)):
            activity.log(conn, "alert", f"Telegram: daily list ({picked} jobs).")
    except telegram.TelegramError as e:
        activity.log(conn, "alert", f"Telegram failed: {e}")


def watch(conn, s) -> str:
    _mark(conn, "watch", started=time.time())
    wanted = s["watch_list"]
    targets = [b for b in collect.all_boards(s)
               if any(rules.name_in(b["name"], [w]) or b["slug"] == w.lower() for w in wanted)]
    watch_portals = [p for p in portals.default_portals() if rules.name_in(p["name"], wanted)]
    if not targets and not watch_portals:
        msg = "Watch list: no matching company boards." if wanted else "Watch list is empty."
        _mark(conn, "watch", ok=True, message=msg)
        return msg
    jobs = collect.fetch_all(s, log=_log, only_boards=targets) if targets else []
    for p in watch_portals:
        try:
            jobs += portals.fetch_portal(p)
        except Exception as e:  # one broken careers site must not stop the others
            _log(f"{p['name']} careers failed: {e}")
    new = collect.save(conn, jobs, s, added_by="watch")
    collect.apply_rules(conn, s, log=_log)
    alerts = 0
    for job in conn.execute(
        "SELECT * FROM jobs WHERE added_by = 'watch' AND rule_ok = 1 AND alerted = 0 AND status = 'new'"
    ).fetchall():
        try:
            sent = telegram.send(f"New job at {job['company']} (watch list):\n{job['title']}\n"
                                 f"{job['location']}\n{job['url']}")
        except telegram.TelegramError as e:
            activity.log(conn, "alert", f"Telegram failed: {e}")
            break
        conn.execute("UPDATE jobs SET alerted = 1 WHERE id = ?", (job["id"],))
        conn.commit()
        if sent:
            alerts += 1
            activity.log(conn, "alert", f"Telegram: watch-list job {job['title']} at {job['company']}.")
    if new:
        scoring.score_pending(conn, s, limit=20, log=_log)
        ranking.pick_today(conn, s)
    msg = f"Watch list: {len(targets) + len(watch_portals)} sites, {len(new)} new jobs, {alerts} alerts."
    _mark(conn, "watch", ok=True, message=msg)
    return msg


def refresh(conn, s) -> str:
    """Re-run rules, scoring and today's list on saved jobs. No downloading."""
    collect.apply_rules(conn, s, log=_log)
    scored = scoring.score_pending(conn, s, limit=s["ai_jobs_per_run"], log=_log)
    picked = ranking.pick_today(conn, s)
    msg = f"Refresh: {scored} scored, {picked} on today's list."
    activity.log(conn, "worker", msg)
    return msg


def tick(conn, s) -> None:
    now = time.time()
    hour = datetime.now().hour
    d = _run_row(conn, "daily")
    done_today = d and d["last_ok"] and d["last_finished"] and \
        date.fromtimestamp(d["last_finished"]) == date.today()
    recently_tried = d and d["last_started"] and now - d["last_started"] < RETRY_AFTER
    if hour >= s["run_after_hour"] and not done_today and not recently_tried:
        _log(daily(conn, s))

    w = _run_row(conn, "watch")
    if not w or not w["last_started"] or now - w["last_started"] >= s["watch_interval_minutes"] * 60:
        _log(watch(conn, s))


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "tick"
    make_private_dirs()
    init_db(DB_PATH, DATA_DIR)
    lock = open(DATA_DIR / "worker.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        _log("Another run is already going. Skipping.")
        return 0

    conn = connect(DB_PATH)
    try:
        s = settings_store.get_all(conn)
        if command == "status":
            for r in conn.execute("SELECT * FROM runs"):
                fin = datetime.fromtimestamp(r["last_finished"]).strftime("%d %b %H:%M") if r["last_finished"] else "-"
                print(f"{r['name']:6} last finished {fin}  ok={r['last_ok']}  {r['message']}")
            return 0
        if settings_store.get(conn, settings_store.PAUSED_KEY):
            _log("Emergency stop is on. Doing nothing.")
            return 0
        if command == "tick":
            tick(conn, s)
        elif command == "daily":
            _log(daily(conn, s))
        elif command == "watch":
            _log(watch(conn, s))
        elif command == "refresh":
            _log(refresh(conn, s))
        else:
            print(__doc__)
            return 2
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
