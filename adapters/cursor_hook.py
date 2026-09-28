from __future__ import annotations

"""Cursor hook bridge for structural OpenWorkGraph evidence.

Cursor runs hooks synchronously and reads their stdout. The foreground hook
therefore does only privacy-safe projection, writes Cursor's non-blocking reply,
then hands the already-allowlisted structural events to a detached helper process.
Network delivery/spooling never sits on Cursor's synchronous hook path.

Raw prompt text, tool arguments/results, workspace paths and error text are never
passed to the helper. Any failure still exits 0.
"""

import json
import os
import subprocess
import sys

from adapters.claude_code_hook import PROJECT_ROOT, _shell_quote
from shared.cursor_hook_adapter import SUPPORTED_EVENTS, cursor_hook_to_agent_events

OWG_MARKER = "adapters.cursor_hook"
HOOK_TIMEOUT_SECONDS = 5
MAX_DELIVERY_BYTES = 256_000


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


def _debug_notice() -> None:
    if os.getenv("OWG_AGENT_ADAPTER_DEBUG", "").strip() == "1":
        print("OpenWorkGraph Cursor hook skipped one event", file=sys.stderr)


def _deliver(events: list[dict]) -> int:
    """Detached helper entrypoint; input is already privacy-safe structural data."""
    try:
        from adapters._agent_client import post_agent_events

        post_agent_events(events, timeout=0.5)
    except Exception:
        _debug_notice()
    return 0


def _spawn_delivery(events: list[dict]) -> None:
    if not events:
        return
    try:
        raw = json.dumps(events, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except Exception:
        return
    if len(raw) > MAX_DELIVERY_BYTES:
        return

    kwargs: dict = {
        "cwd": PROJECT_ROOT,
        "stdin": subprocess.PIPE,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True

    try:
        child = subprocess.Popen([sys.executable, "-m", OWG_MARKER, "--deliver"], **kwargs)
        if child.stdin is not None:
            child.stdin.write(raw)
            child.stdin.close()
    except Exception:
        # Observation must never make Cursor depend on OpenWorkGraph.
        try:
            child.kill()  # type: ignore[name-defined]
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    argv = list(argv or [])
    if "--print-hooks" in argv:
        print(json.dumps(hooks_fragment(), indent=2))
        return 0

    if "--deliver" in argv:
        try:
            raw = sys.stdin.buffer.read(MAX_DELIVERY_BYTES + 1)
            if len(raw) > MAX_DELIVERY_BYTES:
                return 0
            value = json.loads(raw.decode("utf-8"))
            events = [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
        except Exception:
            events = []
        return _deliver(events)

    hook = ""
    try:
        raw = sys.stdin.buffer.read(2_000_001)
        payload = json.loads(raw.decode("utf-8")) if len(raw) <= 2_000_000 else {}
        hook = str(payload.get("hook_event_name") or "") if isinstance(payload, dict) else ""
    except Exception:
        payload = {}

    # Cursor's decision is returned before any network/spool work. Projection is
    # local and allowlisted; delivery happens in a separate process.
    _reply(hook)
    try:
        events = cursor_hook_to_agent_events(payload)
        _spawn_delivery(events)
    except Exception:
        _debug_notice()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
