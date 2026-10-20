from __future__ import annotations

"""Default-off local Open Work MCP tool catalog selection.

Only a privileged local dashboard can change this setting. It does NOT grant
AI access to existing work, saved history, provider inventory, provider source
read permissions, or permission to execute a business operation. The old
environment variable remains a deliberate local-launcher override.
"""
import json
import os
from pathlib import Path
import stat
import tempfile
import threading

from .local_auth import auth_dir

_LOCK = threading.RLock()
_NAME = "open_work_mcp_tools.json"


def _path() -> Path:
    return auth_dir() / _NAME


def _read_enabled() -> bool:
    path = _path()
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_size > 512:
            return False
        if os.name == "posix" and (info.st_mode & 0o077):
            return False
        payload = json.loads(path.read_text(encoding="utf-8"))
        return (isinstance(payload, dict) and payload.get("version") == 1
                and payload.get("enabled") is True)
    except (OSError, UnicodeError, TypeError, ValueError):
        return False


def experimental_tools_enabled() -> bool:
    override = os.getenv("OWG_OPEN_WORK_EXPERIMENTAL_TOOLS")
    if override is not None:
        return override == "1"
    return _read_enabled()


def settings_status() -> dict[str, bool | str]:
    override = os.getenv("OWG_OPEN_WORK_EXPERIMENTAL_TOOLS")
    return {
        "enabled": experimental_tools_enabled(),
        "saved_enabled": _read_enabled(),
        "environment_override_active": override is not None,
        "local_only": True,
        "existing_ai_access_still_required": True,
        "existing_source_read_opt_in_still_required": True,
        "permits_business_actions": False,
        "connector_restart_required_to_refresh_tool_list": True,
    }


def set_experimental_tools_enabled(enabled: bool) -> dict[str, bool | str]:
    if type(enabled) is not bool:
        raise ValueError("enabled must be a boolean")
    path = _path()
    with _LOCK:
        if path.is_symlink():
            raise OSError("unsafe Open Work settings location")
        fd, temporary = tempfile.mkstemp(prefix=".owg-open-work-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump({"version": 1, "enabled": enabled}, output)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            if os.name == "posix":
                os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return settings_status()


__all__ = [
    "experimental_tools_enabled", "settings_status",
    "set_experimental_tools_enabled",
]
