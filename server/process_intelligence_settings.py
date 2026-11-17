"""Whether the process-intelligence MCP tools are listed (light: no event-log imports)."""
from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
from pathlib import Path

_FLAG = "process_intelligence_mcp_tools.json"
_LOCK = threading.RLock()


def _flag_path() -> Path:
    from .local_auth import auth_dir
    return auth_dir() / _FLAG


def tools_enabled() -> bool:
    """Whether the MCP catalog should include the process-intelligence tools."""
    override = os.getenv("OWG_PROCESS_INTELLIGENCE_TOOLS")
    if override is not None:
        return override == "1"
    path = _flag_path()
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 512 or (os.name == "posix" and info.st_mode & 0o077):
            return False
        payload = json.loads(path.read_text(encoding="utf-8"))
        return isinstance(payload, dict) and payload.get("version") == 1 and payload.get("enabled") is True
    except (OSError, UnicodeError, ValueError, TypeError):
        return False


def write_flag(enabled: bool) -> None:
    path = _flag_path()
    with _LOCK:
        if path.is_symlink():
            raise OSError("unsafe process-intelligence settings location")
        fd, temporary = tempfile.mkstemp(prefix=".owg-pi-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump({"version": 1, "enabled": enabled}, output)
                output.flush()
                os.fsync(output.fileno())
            if os.name == "posix":
                os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)



__all__ = ["tools_enabled", "write_flag"]
