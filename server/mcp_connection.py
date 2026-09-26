from __future__ import annotations

import sys
from pathlib import Path

from .local_auth import local_security_note


ROOT = Path(__file__).resolve().parents[1]


def stdio_connection_config() -> dict:
    """Return the portable local MCP launch contract used by client installers.

    The saved client configuration points at one OpenWorkGraph-owned launcher
    file instead of duplicating module paths, auth directories, or PYTHONPATH
    details in every third-party client.
    """
    launcher = ROOT / "mcp_server" / "launcher.py"
    return {
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(launcher)],
        "env": {},
        "security_note": local_security_note(),
    }
