"""Phase 5: the Stats page numbers, and small SVG charts drawn on the server (no scripts needed)."""

import json
import time
from collections import Counter
from datetime import date, datetime, timedelta

from . import outreach, tracker

DAY = 86400


def _day_start(d: date) -> float:
    return datetime.combine(d, datetime.min.time()).timestamp()


def per_day(conn, sql: str, days: int, today: date | None = None) -> list[tuple[date, int]]:
    """Counts per day for the last N days. sql selects one timestamp column with a ? for 'since'."""
    today = today or date.today()
    first = today - timedelta(days=days - 1)
    counts = Counter(date.fromtimestamp(r[0]) for r in conn.execute(sql, (_day_start(first),)) if r[0])
    return [(first + timedelta(days=i), counts.get(first + timedelta(days=i), 0)) for i in range(days)]


def streak(conn, goal: int, today: date | None = None) -> int:
    """Days in a row you met the applications goal (today counts once it's met)."""
    if goal <= 0:
        return 0
    today = today or date.today()
    days = dict(per_day(conn, "SELECT applied_at FROM applications WHERE applied_at >= ?", 366, today))
    n, d = 0, today
    if days.get(d, 0) < goal:
        d -= timedelta(days=1)          # today isn't over yet
    while days.get(d, 0) >= goal:
        n += 1
        d -= timedelta(days=1)
    return n


def funnel(conn) -> list[tuple[str, int]]:
    """How many applications reached each stage (a job that got an interview also counts as replied)."""
    stages = [r[0] for r in conn.execute("SELECT stage FROM applications")]
    order = ["applied", "emailed", "replied", "interview", "offer"]
    reached = Counter()
    for st in stages:
        if st == "rejected":
            reached["applied"] += 1
            continue
        for s in order[:order.index(st) + 1]:
            reached[s] += 1
    return [(tracker.STAGE_NAMES[s], reached[s]) for s in order]


def reply_rate(conn) -> tuple[int, int]:
    """(people who replied, people emailed): first emails only, follow-ups don't count twice."""
    sent = conn.execute("SELECT COUNT(DISTINCT job_id || to_addr) FROM emails WHERE status = 'sent' "
                        "AND kind != 'followup'").fetchone()[0]
    replied = conn.execute("SELECT COUNT(DISTINCT job_id || to_addr) FROM emails WHERE status = 'sent' "
                           "AND replied = 1").fetchone()[0]
    return replied, sent


def by_kind(conn) -> list[tuple[str, int, int]]:
    rows = conn.execute("SELECT kind, COUNT(*), SUM(replied) FROM emails WHERE status = 'sent' "
                        "AND kind != 'followup' GROUP BY kind").fetchall()
    return [(outreach.KINDS[r[0]], r[1], r[2] or 0) for r in rows]


def skill_gaps(conn, days: int = 30, top: int = 12) -> list[tuple[str, int]]:
    """Skills Claude said good jobs wanted and your resume lacks, most asked first."""
    since = time.time() - days * DAY
    c = Counter()
    names = {}
    for (raw,) in conn.execute("SELECT ai_json FROM jobs WHERE rule_ok = 1 AND ai_json IS NOT NULL "
                               "AND first_seen >= ?", (since,)):
        for skill in (json.loads(raw).get("missing") or [])[:4]:
            key = " ".join(str(skill).lower().split())[:60]
            if key:
                c[key] += 1
                names.setdefault(key, " ".join(str(skill).split())[:60])
    return [(names[k], n) for k, n in c.most_common(top)]


def sources(conn, days: int = 7, top: int = 10) -> list[tuple[str, int]]:
    """Where the jobs that passed your rules came from, last N days."""
    since = time.time() - days * DAY
    rows = conn.execute("SELECT source_name, COUNT(*) FROM jobs WHERE rule_ok = 1 AND first_seen >= ? "
                        "GROUP BY source_name ORDER BY 2 DESC", (since,)).fetchall()
    out = [(r[0], r[1]) for r in rows[:top]]
    rest = sum(r[1] for r in rows[top:])
    return out + ([("Other", rest)] if rest else [])


def skips(conn, days: int = 30) -> list[tuple[str, int]]:
    since = time.time() - days * DAY
    return [(r[0], r[1]) for r in conn.execute(
        "SELECT reason, COUNT(*) FROM skips WHERE ts >= ? GROUP BY reason ORDER BY 2 DESC", (since,))]


# ---------- charts (SVG geometry; colours come from style.css) ----------

def _nice_max(v: int) -> int:
    """A clean top for the axis, so the 4 gridlines land on round numbers."""
    for step in (1, 2, 5, 10, 20, 25, 50, 100, 250, 500, 1000, 2500, 5000):
        if step * 4 >= v:
            return step * 4
    return v


def _rounded_top(x: float, y: float, w: float, h: float, r: float = 4) -> str:
    """A column with a rounded data end (top) and a square base."""
    r = min(r, w / 2, h)
    return (f"M{x:.1f},{y + h:.1f} V{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} "
            f"H{x + w - r:.1f} Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} V{y + h:.1f} Z")


def _rounded_right(x: float, y: float, w: float, h: float, r: float = 4) -> str:
    r = min(r, h / 2, w)
    return (f"M{x:.1f},{y:.1f} H{x + w - r:.1f} Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} "
            f"V{y + h - r:.1f} Q{x + w:.1f},{y + h:.1f} {x + w - r:.1f},{y + h:.1f} H{x:.1f} Z")


def columns(series: list[tuple[date, int]], width: int = 1100, height: int = 220) -> dict:
    """Column chart over days: one series, value label on the tallest column only."""
    left, bottom, top_pad = 32, 24, 16
    plot_w, plot_h = width - left - 8, height - bottom - top_pad
    top_v = _nice_max(max([v for _, v in series] + [1]))
    slot = plot_w / max(len(series), 1)
    bar_w = min(24, slot - 2)
    peak = max(range(len(series)), key=lambda i: series[i][1]) if series else -1
    bars = []
    for i, (d, v) in enumerate(series):
        h = plot_h * v / top_v
        x = left + i * slot + (slot - bar_w) / 2
        y = top_pad + plot_h - h
        bars.append({"path": _rounded_top(x, y, bar_w, h) if v else "", "x": x + bar_w / 2, "y": y,
                     "tip": f"{d:%a %d %b}: {v}", "label": v if (i == peak and v) else None,
                     "hit_x": left + i * slot, "hit_w": slot,
                     "tick": f"{d:%d %b}" if (len(series) - 1 - i) % 7 == 0 else None})
    grid = [{"y": top_pad + plot_h - plot_h * k / 4, "v": top_v * k // 4} for k in range(5)]
    return {"w": width, "h": height, "bars": bars, "grid": grid, "left": left, "right": width - 8,
            "base": top_pad + plot_h, "plot_top": top_pad, "plot_h": plot_h}


def hbars(rows: list[tuple[str, int]], width: int = 520, label_w: int = 170) -> dict:
    """Horizontal bars: one series, value at each bar's tip (few bars, so every one is labelled)."""
    bar_h, gap = 18, 12
    plot_w = width - label_w - 48
    top_v = max([v for _, v in rows] + [1])
    out = []
    for i, (name, v) in enumerate(rows):
        y = i * (bar_h + gap) + 4
        w = max(plot_w * v / top_v, 2 if v else 0)
        out.append({"name": name, "v": v, "y": y, "ty": y + bar_h / 2,
                    "path": _rounded_right(label_w, y, w, bar_h) if v else "", "vx": label_w + w + 6})
    return {"w": width, "h": len(rows) * (bar_h + gap) + 8, "rows": out, "label_w": label_w}


def page(conn, s: dict) -> dict:
    apps = per_day(conn, "SELECT applied_at FROM applications WHERE applied_at >= ?", 30)
    mails = per_day(conn, "SELECT sent_at FROM emails WHERE status = 'sent' AND sent_at >= ?", 30)
    week_ago = time.time() - 7 * DAY
    replied, emailed = reply_rate(conn)
    return {
        "applied_today": tracker.applied_today(conn), "goal_apps": s["goal_applications"],
        "emails_today": outreach.sent_today(conn), "goal_emails": s["goal_emails"],
        "streak": streak(conn, s["goal_applications"]),
        "applied_week": conn.execute("SELECT COUNT(*) FROM applications WHERE applied_at >= ?", (week_ago,)).fetchone()[0],
        "replied": replied, "emailed": emailed,
        "rate": round(100 * replied / emailed) if emailed else None,
        "apps_chart": columns(apps), "apps_rows": apps, "apps_total": sum(v for _, v in apps),
        "mails_total": sum(v for _, v in mails),
        "funnel": hbars(funnel(conn)), "kinds": by_kind(conn),
        "gaps": hbars(skill_gaps(conn), label_w=200), "sources": hbars(sources(conn), label_w=200),
        "skips": hbars(skips(conn)),
    }
