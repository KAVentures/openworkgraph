from __future__ import annotations

"""Cursor hook bridge for structural OpenWorkGraph evidence.

Cursor runs hooks synchronously and reads their stdout. This bridge answers
first, before any other work, with a reply that never blocks anything
(``beforeSubmitPrompt`` gets ``{"continue": true}``; every other hook ``{}``),
then translates and posts the event. Any failure still exits 0, and Cursor
itself fails open on crashes and timeouts. No payload, secret or error text is
ever printed.
"""

import json
import os
import sys

from adapters.claude_code_hook import PROJECT_ROOT, _shell_quote
from shared.cursor_hook_adapter import SUPPORTED_EVENTS, cursor_hook_to_agent_events

OWG_MARKER = "adapters.cursor_hook"
HOOK_TIMEOUT_SECONDS = 5


def hook_command(command: str | None = None) -> str:
    python = _shell_quote(command or sys.executable)
    root = _shell_quote(PROJECT_ROOT)
    if os.name == "nt":
        return f"cd /d {root} && {python} -m {OWG_MARKER}"
    return f"cd {root} && {python} -m {OWG_MARKER}"


def hooks_fragment(command: str | None = None) -> dict:
    handler = {"command": hook_command(command), "timeout": HOOK_TIMEOUT_SECONDS}
    return {"version": 1, "hooks": {event: [dict(handler)] for event in SUPPORTED_EVENTS}}


def _reply(hook: str) -> None:
    sys.stdout.write(json.dumps({"continue": True} if hook == "beforeSubmitPrompt" else {}))
    sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    if argv and "--print-hooks" in argv:
        print(json.dumps(hooks_fragment(), indent=2))
        return 0
    hook = ""
    try:
        raw = sys.stdin.buffer.read(2_000_001)
        payload = json.loads(raw.decode("utf-8")) if len(raw) <= 2_000_000 else {}
        hook = str(payload.get("hook_event_name") or "") if isinstance(payload, dict) else ""
    except Exception:
        payload = {}
    _reply(hook)
    try:
        events = cursor_hook_to_agent_events(payload)
        if events:
            from adapters._agent_client import post_agent_events

            post_agent_events(events, timeout=0.5)
    except Exception:
        if os.getenv("OWG_AGENT_ADAPTER_DEBUG", "").strip() == "1":
            print("OpenWorkGraph Cursor hook skipped one event", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
