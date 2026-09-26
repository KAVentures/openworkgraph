from __future__ import annotations

"""Claude Code hook bridge for structural OpenWorkGraph evidence.

The hook must never make Claude Code depend on OpenWorkGraph availability. Any
parse/network/storage failure therefore exits successfully without echoing native
payloads, secrets, prompts, arguments, or tool results.
"""

import argparse
import json
import os
import shlex
import subprocess
import sys

from adapters._agent_client import post_agent_events
from shared.claude_code_adapter import claude_hook_to_agent_events

SUPPORTED_EVENTS = [
    "SessionStart",
    "SessionEnd",
    "PostToolUse",
    "PostToolUseFailure",
    "PermissionRequest",
    "PermissionDenied",
    "SubagentStart",
    "SubagentStop",
    "StopFailure",
]


# Repository root that contains the ``adapters`` package. The package is not
# installed into the venv, so ``-m adapters.claude_code_hook`` only resolves when
# the working directory is this root. Claude Code runs hooks from the session's
# project directory, so the generated command must ``cd`` here first.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _shell_quote(value: str) -> str:
    """Quote one command argument for the host platform's default shell."""
    if os.name == "nt":
        return subprocess.list2cmdline([value])
    return shlex.quote(value)


def hook_command(command: str | None = None) -> str:
    executable = command or sys.executable
    root = _shell_quote(PROJECT_ROOT)
    python = _shell_quote(executable)
    if os.name == "nt":
        # ``/d`` lets cmd.exe change both directory and drive. Single-quote
        # POSIX escaping is invalid in cmd.exe, so use Windows command-line
        # quoting via ``subprocess.list2cmdline`` above.
        return f"cd /d {root} && {python} -m adapters.claude_code_hook"
    return f"cd {root} && {python} -m adapters.claude_code_hook"


def settings_fragment(command: str | None = None) -> dict:
    handler = {
        "type": "command",
        # Single shell command string: platform-native quoting survives spaces
        # (e.g. "Application Support") without relying on a separate args field.
        "command": hook_command(command),
        # OpenWorkGraph is observational and never returns a Claude Code control
        # decision. Run the bridge in the background so local telemetry cannot
        # add latency to the triggering tool/lifecycle event.
        "async": True,
        "timeout": 2,
    }
    return {
        "hooks": {
            event: [{"hooks": [dict(handler)]}]
            for event in SUPPORTED_EVENTS
        }
    }


def _debug_notice() -> None:
    if os.getenv("OWG_AGENT_ADAPTER_DEBUG", "").strip() == "1":
        # Do not print the exception: HTTP/library exceptions can contain URLs,
        # headers, filesystem paths, or fragments of the native payload.
        print("OpenWorkGraph agent hook skipped one event", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OpenWorkGraph Claude Code hook bridge")
    parser.add_argument("--print-settings", action="store_true", help="print a Claude Code settings fragment")
    args = parser.parse_args(argv)
    if args.print_settings:
        print(json.dumps(settings_fragment(), indent=2))
        return 0

    try:
        raw = sys.stdin.buffer.read(2_000_001)
        if len(raw) > 2_000_000:
            return 0
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            return 0
        events = claude_hook_to_agent_events(payload)
        if events:
            post_agent_events(events)
    except Exception:
        _debug_notice()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
