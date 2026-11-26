"""The ONE place that talks to Claude.

It runs the `claude` command that is already logged in on this laptop
(your Claude plan, no extra bill).

Safety (security rule 4):
- Claude gets NO tools: it can't run commands, open files, browse or send anything.
  It can only send back text, in the exact shape we ask for (a JSON schema).
- No MCP servers, no settings, no saved sessions.
- It runs inside an empty folder, so it can't pick up any project files.
- Our own code decides what to do with the answer.

To switch to a paid API key later, only this file needs to change.
"""

import json
import os
import shutil
import subprocess

from .config import DATA_DIR

SANDBOX = DATA_DIR / "ai_sandbox"
ALLOWED_MODELS = ("haiku", "sonnet", "opus")


class AIError(Exception):
    """Claude couldn't answer (plan limit reached, logged out, timeout...)."""


def available() -> bool:
    return shutil.which("claude") is not None


def ask_json(system: str, prompt: str, schema: dict, model: str = "haiku",
             timeout: int = 300) -> dict:
    if model not in ALLOWED_MODELS:
        raise AIError(f"Unknown model {model!r}")
    if not available():
        raise AIError("The claude command is not installed or not on PATH.")
    SANDBOX.mkdir(parents=True, exist_ok=True)
    os.chmod(SANDBOX, 0o700)

    command = [
        "claude", "-p",
        "--tools", "",
        "--strict-mcp-config",
        "--setting-sources", "",
        "--no-session-persistence",
        "--model", model,
        "--output-format", "json",
        "--system-prompt", system,
        "--json-schema", json.dumps(schema),
    ]
    try:
        done = subprocess.run(
            command, input=prompt, capture_output=True, text=True,
            timeout=timeout, cwd=SANDBOX,
        )
    except subprocess.TimeoutExpired as e:
        raise AIError("Claude took too long to answer.") from e

    try:
        result = json.loads(done.stdout)
    except json.JSONDecodeError as e:
        detail = (done.stderr or done.stdout or "").strip()[:300]
        raise AIError(f"Claude gave no usable answer: {detail}") from e

    if result.get("is_error") or result.get("subtype") != "success":
        raise AIError(f"Claude error: {str(result.get('result'))[:300]}")
    answer = result.get("structured_output")
    if not isinstance(answer, dict):
        raise AIError("Claude's answer was not in the expected shape.")
    return answer
