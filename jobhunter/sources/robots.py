"""robots.txt: what a site asks bots not to read.

Read the way RFC 9309 (and Google) says:
- the group for our name, else the "*" group;
- the longest matching rule wins, and Allow wins a tie;
- "*" matches anything, "$" means end of the link;
- no robots.txt (a 4xx answer) means everything is allowed;
- a server error or no answer means nothing is allowed, to be safe.

Python's own robotparser takes the first matching rule instead, which gets sites
like "Disallow: /  Allow: /careers" wrong, so this is our own small reader.
"""

import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

AGENT = "job-hunter"
UA = "Mozilla/5.0 (X11; Linux x86_64) job-hunter/0.1 (personal job search)"
KEEP_FOR = 3600  # seconds a host's robots.txt is reused

_cache: dict[str, tuple[float, list | None]] = {}
_lock = threading.Lock()


def parse_rules(text: str, agent: str = AGENT) -> list[tuple[bool, str]]:
    """The (allow, pattern) rules that apply to us."""
    groups: list[tuple[list[str], list[tuple[bool, str]]]] = []
    agents: list[str] = []
    rules: list[tuple[bool, str]] = []
    last_was_agent = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "user-agent":
            if not last_was_agent:
                agents, rules = [], []
                groups.append((agents, rules))
            agents.append(value.lower())
            last_was_agent = True
        elif key in ("allow", "disallow"):
            last_was_agent = False
            if groups and value:
                rules.append((key == "allow", value))
        else:
            last_was_agent = False
    ours = [r for a, r in groups if agent.lower() in a]
    if not ours:
        ours = [r for a, r in groups if "*" in a]
    return [rule for group in ours for rule in group]


def _matches(pattern: str, path: str) -> bool:
    regex = "".join(".*" if ch == "*" else re.escape(ch) for ch in pattern.rstrip("$"))
    return re.match(regex + ("$" if pattern.endswith("$") else ""), path) is not None


def allowed_by(rules: list[tuple[bool, str]] | None, url: str) -> bool:
    if rules is None:
        return False
    u = urllib.parse.urlsplit(url)
    path = (u.path or "/") + (f"?{u.query}" if u.query else "")
    best_len, best_allow = -1, True
    for allow, pattern in rules:
        if _matches(pattern, path) and (len(pattern) > best_len or (len(pattern) == best_len and allow)):
            best_len, best_allow = len(pattern), allow
    return best_allow


def _fetch(host: str) -> list | None:
    req = urllib.request.Request(f"https://{host}/robots.txt", headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as f:
            return parse_rules(f.read(512 * 1024).decode("utf-8", "ignore"))
    except urllib.error.HTTPError as e:
        return [] if 400 <= e.code < 500 and e.code != 429 else None
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def rules_for(host: str) -> list | None:
    with _lock:
        hit = _cache.get(host)
        if hit and time.time() - hit[0] < KEEP_FOR:
            return hit[1]
    found = _fetch(host)
    with _lock:
        _cache[host] = (time.time(), found)
    return found


def allowed(url: str) -> bool:
    return allowed_by(rules_for(urllib.parse.urlsplit(url).netloc), url)
