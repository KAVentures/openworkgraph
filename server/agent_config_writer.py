from __future__ import annotations

"""Explicit, reversible one-click agent-observation setup.

The dashboard owner clicks Connect/Disconnect; nothing here runs automatically.
Every write is conservative:

* the existing file is backed up before the first change of each write;
* only OpenWorkGraph-owned entries are added or removed, everything else is
  preserved byte-for-byte (TOML) or key-for-key (JSON);
* files that cannot be parsed, or that already contain a foreign
  configuration for the same section, are never modified; the caller falls
  back to the manual copy/paste flow instead;
* writes are atomic (temp file + rename) and keep the original file mode.
"""

import json
import os
import shutil
import tempfile
import time
import tomllib
from pathlib import Path
from typing import Any, Callable

CLAUDE_HOOK_MARKER = "adapters.claude_code_hook"
CODEX_BLOCK_START = "# >>> OpenWorkGraph agent observation (managed; remove via dashboard) >>>"
CODEX_BLOCK_END = "# <<< OpenWorkGraph agent observation <<<"


class ConfigConflict(Exception):
    """The file exists but cannot be changed safely; use manual setup."""


def claude_settings_path() -> Path:
    override = os.getenv("OWG_CLAUDE_SETTINGS_PATH", "").strip()
    return Path(override) if override else Path.home() / ".claude" / "settings.json"


def codex_config_path() -> Path:
    override = os.getenv("OWG_CODEX_CONFIG_PATH", "").strip()
    if override:
        return Path(override)
    codex_home = os.getenv("CODEX_HOME", "").strip()
    return (Path(codex_home) if codex_home else Path.home() / ".codex") / "config.toml"


def _backup(path: Path) -> str | None:
    if not path.exists():
        return None
    backup = path.with_name(f"{path.name}.owg-backup-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(path, backup)
    return str(backup)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --- Claude Code: hooks in ~/.claude/settings.json ---------------------------

def _load_claude(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigConflict(f"{path} is not valid JSON; fix it or use manual setup") from exc
    if not isinstance(data, dict):
        raise ConfigConflict(f"{path} is not a JSON object; use manual setup")
    hooks = data.get("hooks")
    if hooks is not None and not isinstance(hooks, dict):
        raise ConfigConflict(f"'hooks' in {path} has an unexpected shape; use manual setup")
    return data


def _is_owg_handler(handler: Any) -> bool:
    # Also matches the pre-0.89 form, which kept the module name in "args".
    if not isinstance(handler, dict):
        return False
    return CLAUDE_HOOK_MARKER in str(handler.get("command", "")) + " " + " ".join(map(str, handler.get("args") or []))


def _strip_owg_hooks(data: dict[str, Any]) -> int:
    """Remove OpenWorkGraph handlers in place; return how many were removed."""
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return 0
    removed = 0
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        kept_groups = []
        for group in groups:
            if isinstance(group, dict) and isinstance(group.get("hooks"), list):
                before = len(group["hooks"])
                group["hooks"] = [h for h in group["hooks"] if not _is_owg_handler(h)]
                removed += before - len(group["hooks"])
                if not group["hooks"]:
                    continue
            kept_groups.append(group)
        if kept_groups:
            hooks[event] = kept_groups
        else:
            del hooks[event]
    if not hooks:
        data.pop("hooks", None)
    return removed


def claude_status() -> dict[str, Any]:
    path = claude_settings_path()
    try:
        data = _load_claude(path)
    except ConfigConflict as exc:
        return {"configured": False, "path": str(path), "error": str(exc)}
    configured = any(
        _is_owg_handler(h)
        for groups in (data.get("hooks") or {}).values() if isinstance(groups, list)
        for group in groups if isinstance(group, dict)
        for h in (group.get("hooks") or []) if isinstance(group.get("hooks"), list)
    )
    return {"configured": configured, "path": str(path)}


def claude_connect(fragment_factory: Callable[[], dict]) -> dict[str, Any]:
    path = claude_settings_path()
    data = _load_claude(path)
    # Replace, never duplicate: older OpenWorkGraph handlers (e.g. the pre-0.89
    # command/args form) are removed before the current ones are added.
    _strip_owg_hooks(data)
    hooks = data.setdefault("hooks", {})
    for event, groups in fragment_factory()["hooks"].items():
        existing = hooks.setdefault(event, [])
        if not isinstance(existing, list):
            raise ConfigConflict(f"hooks.{event} in {path} has an unexpected shape; use manual setup")
        existing.extend(groups)
    backup = _backup(path)
    _atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return {"configured": True, "path": str(path), "backup": backup,
            "note": "Takes effect in new Claude Code sessions."}


def claude_disconnect() -> dict[str, Any]:
    path = claude_settings_path()
    if not path.exists():
        return {"configured": False, "path": str(path), "backup": None}
    data = _load_claude(path)
    if not _strip_owg_hooks(data):
        return {"configured": False, "path": str(path), "backup": None}
    backup = _backup(path)
    _atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return {"configured": False, "path": str(path), "backup": backup}


# --- Codex: [otel] in ~/.codex/config.toml ------------------------------------

def _strip_codex_block(text: str) -> tuple[str, bool]:
    start = text.find(CODEX_BLOCK_START)
    if start < 0:
        return text, False
    end = text.find(CODEX_BLOCK_END, start)
    if end < 0:
        raise ConfigConflict("The OpenWorkGraph block in the Codex config is incomplete; fix it manually")
    end += len(CODEX_BLOCK_END)
    if text[end:end + 1] == "\n":
        end += 1
    head = text[:start].rstrip("\n")
    tail = text[end:].lstrip("\n")
    joined = head + ("\n\n" if head and tail else "\n" if head else "") + tail
    return joined, True


def _parse_toml(text: str, path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigConflict(f"{path} is not valid TOML; fix it or use manual setup") from exc


def codex_status() -> dict[str, Any]:
    path = codex_config_path()
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    return {"configured": CODEX_BLOCK_START in text, "path": str(path)}


def codex_connect(snippet: str) -> dict[str, Any]:
    path = codex_config_path()
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    remainder, _ = _strip_codex_block(original)
    if "otel" in _parse_toml(remainder, path):
        raise ConfigConflict(
            f"{path} already has its own [otel] settings. OpenWorkGraph will not overwrite them; "
            "merge the keys manually instead."
        )
    block = f"{CODEX_BLOCK_START}\n{snippet.strip()}\n{CODEX_BLOCK_END}\n"
    body = remainder.rstrip("\n")
    updated = (body + "\n\n" if body else "") + block
    parsed = _parse_toml(updated, path)
    if "trace_exporter" not in (parsed.get("otel") or {}):
        raise ConfigConflict("Generated Codex configuration did not validate; use manual setup")
    backup = _backup(path)
    _atomic_write(path, updated)
    return {"configured": True, "path": str(path), "backup": backup,
            "note": "Takes effect the next time Codex starts."}


def codex_disconnect() -> dict[str, Any]:
    path = codex_config_path()
    if not path.exists():
        return {"configured": False, "path": str(path), "backup": None}
    original = path.read_text(encoding="utf-8")
    remainder, removed = _strip_codex_block(original)
    if not removed:
        return {"configured": False, "path": str(path), "backup": None}
    _parse_toml(remainder, path)
    backup = _backup(path)
    _atomic_write(path, remainder)
    return {"configured": False, "path": str(path), "backup": backup}
