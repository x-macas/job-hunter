"""Small helpers that read job text: dates, HTML, experience, pay, job type, location."""

import html
import re
from datetime import datetime
from html.parser import HTMLParser


# ---------- dates ----------

def iso_to_ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


# ---------- HTML to plain text ----------

class _TextMaker(HTMLParser):
    BLOCKS = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "tr", "section"}

    def __init__(self):
        super().__init__()
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in self.BLOCKS:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1
        elif tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_to_text(raw: str | None) -> str:
    if not raw:
        return ""
    if "&lt;" in raw[:200]:  # Greenhouse sends HTML that is escaped once more
        raw = html.unescape(raw)
    maker = _TextMaker()
    maker.feed(raw)
    text = "".join(maker.parts)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


# ---------- experience ----------

_NUM = r"(\d{1,2}(?:\.\d)?)"
_EXP_PATTERNS = [
    re.compile(_NUM + r"\s*\+?\s*(?:-|–|to)\s*\d{1,2}\s*\+?\s*(?:years?|yrs?)", re.I),
    re.compile(_NUM + r"\s*\+\s*(?:years?|yrs?)", re.I),
    re.compile(_NUM + r"\s*(?:years?|yrs?)(?:'|’)?\s*(?:of\s+)?(?:\w+\s+){0,4}?experience", re.I),
    re.compile(r"(?:minimum|at\s+least|min\.?)\s*(?:of\s+)?" + _NUM + r"\s*(?:years?|yrs?)", re.I),
]


def min_years(text: str) -> float | None:
    """Smallest 'years of experience' the post asks for, or None if it doesn't say.

    We take the smallest number so we never hide a job by mistake.
    Claude also reads the post and gives its own answer later.
    """
    found = []
    for pattern in _EXP_PATTERNS:
        for match in pattern.finditer(text or ""):
            value = float(match.group(1))
            if value <= 20:
                found.append(value)
    return min(found) if found else None


# ---------- pay ----------

def salary_lpa(text: str, usd_to_inr: float = 88.0) -> tuple[float | None, float | None]:
    """Read yearly pay in lakhs (LPA) from salary text. Returns (min, max)."""
    if not text:
        return None, None
    t = text.replace(",", "")

    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(\d+(?:\.\d+)?)\s*(?:lpa|lakhs?|l\b)", t, re.I)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:lpa|lakhs?)", t, re.I)
    if m:
        return float(m.group(1)), float(m.group(1))

    m = re.search(r"(?:₹|inr|rs\.?)\s*(\d{5,9})(?:\s*(?:-|–|to)\s*(?:₹|inr|rs\.?)?\s*(\d{5,9}))?", t, re.I)
    if m and not re.search(r"month|/mo\b", t, re.I):
        low = float(m.group(1)) / 100000
        high = float(m.group(2)) / 100000 if m.group(2) else low
        return round(low, 1), round(high, 1)

    m = re.search(r"\$\s*(\d+(?:\.\d+)?)\s*(k)?(?:\s*(?:-|–|to)\s*\$?\s*(\d+(?:\.\d+)?)\s*(k)?)?", t, re.I)
    if m and not re.search(r"hour|/hr|month|/mo\b", t, re.I):
        low = float(m.group(1)) * (1000 if m.group(2) else 1)
        high = float(m.group(3)) * (1000 if (m.group(4) or m.group(2)) else 1) if m.group(3) else low
        if low >= 10000:
            return round(low * usd_to_inr / 100000, 1), round(high * usd_to_inr / 100000, 1)
    return None, None


# ---------- job type ----------

def job_type_from(title: str, hint: str = "") -> str:
    text = f"{title} {hint}".lower()
    if re.search(r"\bintern(ship)?\b|\btrainee\b", text):
        return "intern"
    if re.search(r"part[\s-]?time", text):
        return "part_time"
    if re.search(r"\bcontract(or)?\b|\bfreelance\b|\btemporary\b", text):
        return "contract"
    if re.search(r"full[\s-]?time|fulltime|permanent", text):
        return "full_time"
    return "unknown"


# ---------- location ----------

INDIA_WORDS = [
    "india", "bengaluru", "bangalore", "hyderabad", "pune", "mumbai", "chennai", "delhi",
    "new delhi", "gurgaon", "gurugram", "noida", "kolkata", "ahmedabad", "jaipur", "kochi",
    "coimbatore", "indore", "chandigarh", "thiruvananthapuram", "trivandrum", "mysore",
    "mysuru", "bhubaneswar", "nagpur", "raipur", "vadodara", "surat", "lucknow", "goa",
]
GLOBAL_WORDS = ["anywhere", "worldwide", "global", "apac", "asia", "international", "any location"]
_REMOTE = re.compile(r"\bremote\b|work from home|\bwfh\b|distributed", re.I)
_FILLER = re.compile(r"\bremote\b|work from home|\bwfh\b|distributed|first|only|friendly|"
                     r"[(),/|;:\-–•]|\bor\b|\band\b|\bin\b", re.I)


def is_india(location: str) -> bool:
    low = location.lower()
    return any(re.search(rf"\b{re.escape(w)}\b", low) for w in INDIA_WORDS)


def is_open_remote(location: str, remote_flag: bool = False) -> bool:
    """Remote that India can apply to: no country limit, or a global/APAC limit."""
    low = location.lower()
    if not (remote_flag or _REMOTE.search(low)):
        return False
    if is_india(low) or any(w in low for w in GLOBAL_WORDS):
        return True
    leftover = _FILLER.sub(" ", low).strip()
    return leftover == ""


def location_ok(location: str, remote_flag: bool, wanted: list[str]) -> bool:
    for want in wanted:
        w = want.strip().lower()
        if not w:
            continue
        if w == "india" and is_india(location):
            return True
        if w == "remote" and is_open_remote(location, remote_flag):
            return True
        if w not in ("india", "remote") and w in location.lower():
            return True
    return False
