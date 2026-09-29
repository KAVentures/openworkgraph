from __future__ import annotations

"""Claude Code SessionStart hook: fetch OpenWorkGraph's content-free brief.

Installed only while the person has turned briefs on for Claude Code. Unlike the
observational hooks it runs synchronously, because Claude Code adds its output
(``additionalContext``) to the new session. It must never block or break Claude
Code: any failure prints nothing and exits 0, and the request times out fast.
"""

import json
import sys
from urllib.request import Request, urlopen

from adapters._agent_client import _base_url
from adapters.claude_code_hook import PROJECT_ROOT, _shell_quote  # noqa: F401  (shared quoting rules)

BRIEF_PATH = "/agent-brief/v1/brief"
TIMEOUT_SECONDS = 2.0
# A resumed session already has its context; a new, cleared or compacted one does not.
BRIEF_SOURCES = {"startup", "clear", "compact", ""}


def hook_command(command: str | None = None) -> str:
    import os

    executable = command or sys.executable
    root, python = _shell_quote(PROJECT_ROOT), _shell_quote(executable)
    if os.name == "nt":
        return f"cd /d {root} && {python} -m adapters.claude_code_brief"
    return f"cd {root} && {python} -m adapters.claude_code_brief"


def hook_handler(command: str | None = None) -> dict:
    # Synchronous on purpose (no "async"): Claude Code only reads the output of
    # synchronous hooks. The short timeout bounds any delay at session start.
    return {"type": "command", "command": hook_command(command), "timeout": 5}


def fetch_brief(payload: dict) -> str:
    from server.agent_auth import ensure_agent_brief_token
    from shared.tool_detail import workspace_ref

    body = json.dumps({
        "framework": "claude-code",
        "session_id": str(payload.get("session_id") or "")[:128],
        "workspace_ref": workspace_ref(payload.get("cwd")) if payload.get("cwd") else "",
    }).encode("utf-8")
    request = Request(_base_url() + BRIEF_PATH, data=body, method="POST", headers={
        "Authorization": f"Bearer {ensure_agent_brief_token()}",
        "Content-Type": "application/json",
    })
    with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        value = json.loads(response.read(64_000).decode("utf-8") or "{}")
    text = value.get("text") if isinstance(value, dict) else ""
    return text if isinstance(text, str) else ""


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(1_000_001)
        payload = json.loads(raw.decode("utf-8")) if raw and len(raw) <= 1_000_000 else {}
        if not isinstance(payload, dict) or str(payload.get("source") or "") not in BRIEF_SOURCES:
            return 0
        text = fetch_brief(payload)
        if text:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
