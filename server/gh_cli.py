from __future__ import annotations

"""Find an existing GitHub CLI in desktop app launches with a minimal PATH.

Finder-launched macOS apps typically omit Homebrew paths. Do not alter PATH or
execute through a shell. Only use executable files from the existing PATH,
the two standard Homebrew installs, or a user-provided explicit override.
"""
import os
from pathlib import Path
import shutil


def find_gh_cli(name: str = "gh") -> str | None:
    if name != "gh":
        return None

    override = os.environ.get("OWG_GH_CLI_PATH", "").strip()
    if override:
        p = Path(override).expanduser()
        # Explicit path is a local user/operator configuration, not input from
        # a recorded event or a remote MCP request.
        if p.is_absolute() and p.is_file() and os.access(p, os.X_OK):
            return str(p)
        return None

    path_gh = shutil.which("gh")
    if path_gh:
        return path_gh
    if os.name == "posix":
        for location in ("/opt/homebrew/bin/gh", "/usr/local/bin/gh"):
            p = Path(location)
            if p.is_file() and os.access(p, os.X_OK):
                return str(p)
    return None


__all__ = ["find_gh_cli"]
