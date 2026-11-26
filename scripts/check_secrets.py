"""Stops a Git commit if it would upload personal files or secret keys.

Runs by itself before every commit (see .githooks/pre-commit).
"""

import re
import subprocess
import sys

BLOCKED_PATHS = re.compile(r"(^|/)(data|secrets)/|\.(pdf|tex|db|sqlite3|env)$|(^|/)\.env$")

KEY_PATTERNS = {
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "Anthropic key": re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
    "OpenAI-style key": re.compile(r"\bsk-[A-Za-z0-9]{32,}"),
    "Google API key": re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    "Google OAuth token": re.compile(r"ya29\.[0-9A-Za-z_-]{20,}"),
    "Google OAuth secret": re.compile(r"GOCSPX-[0-9A-Za-z_-]{20,}"),
    "GitHub token": re.compile(r"\b(ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{20,}"),
    "Telegram bot token": re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),
    "Slack token": re.compile(r"xox[abposr]-[A-Za-z0-9-]{10,}"),
    "AWS key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
}


def staged_files() -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        capture_output=True, text=True, check=True,
    ).stdout
    return [line for line in out.splitlines() if line]


def staged_text(path: str) -> str:
    out = subprocess.run(["git", "show", f":{path}"], capture_output=True, check=True).stdout
    return out.decode("utf-8", errors="ignore")


def main() -> int:
    problems = []
    for path in staged_files():
        if BLOCKED_PATHS.search(path):
            problems.append(f"{path}: personal or secret file, must not be committed")
            continue
        text = staged_text(path)
        for name, pattern in KEY_PATTERNS.items():
            if pattern.search(text):
                problems.append(f"{path}: looks like it contains a {name}")
    if problems:
        print("Commit stopped. These would leak private data:")
        for p in problems:
            print("  -", p)
        print("Remove them from the commit (git restore --staged <file>) and try again.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
