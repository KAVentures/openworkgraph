from __future__ import annotations

"""Claude Code hook bridge for structural OpenWorkGraph evidence.

The hook must never make Claude Code depend on OpenWorkGraph availability. Any
parse/network/storage failure therefore exits successfully without echoing native
payloads, secrets, prompts, arguments, or tool results.
"""

import argparse
import json
import os
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


def settings_fragment(command: str | None = None) -> dict:
    executable = command or sys.executable
    handler = {
        "type": "command",
        "command": executable,
        "args": ["-m", "adapters.claude_code_hook"],
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
