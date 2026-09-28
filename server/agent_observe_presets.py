from __future__ import annotations

"""Observe presets for GitHub Copilot (VS Code), Gemini CLI and Cursor.

Same rules as the other connectors (server.agent_config_writer): back up before
writing, keep every other setting, never overwrite a setting the user chose for
themselves, and refuse (changing nothing) when a file is not plain JSON.

* Copilot: VS Code user settings enable Copilot's OpenTelemetry export to
  OpenWorkGraph with content capture explicitly off.
* Gemini CLI: ~/.gemini/settings.json ``telemetry`` block exports OTLP over
  HTTP to OpenWorkGraph with ``logPrompts`` explicitly false (Gemini's default is
  true, which would put prompts and tool arguments into its log events).
* Cursor: ~/.cursor/hooks.json gets OpenWorkGraph's non-blocking hooks next to
  any hooks the user already has.

Copilot and Gemini cannot send an Authorization header from a settings file,
so their endpoint path carries a separate write-only token.
"""

import json
from pathlib import Path
from typing import Any

from . import agent_config_writer as writer
from .agent_config_writer import ConfigConflict


def otlp_base_url(source: str) -> str:
    from adapters._agent_client import _base_url

    from .agent_auth import ensure_agent_otlp_path_token

    return f"{_base_url()}/agent-ingest/otlp/{source}/{ensure_agent_otlp_path_token()}"


def _load(path: Path, what: str) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigConflict(
            f"{path} is not plain JSON ({what} allows comments, which OpenWorkGraph will not rewrite); "
            "use manual setup"
        ) from exc
    if not isinstance(data, dict):
        raise ConfigConflict(f"{path} is not a JSON object; use manual setup")
    return data


def _write(path: Path, data: dict[str, Any]) -> str | None:
    backup = writer._backup(path)
    writer._atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return backup


# --- GitHub Copilot in VS Code --------------------------------------------------------------

def copilot_settings() -> dict[str, Any]:
    return {
        "github.copilot.chat.otel.enabled": True,
        "github.copilot.chat.otel.exporterType": "otlp-http",
        "github.copilot.chat.otel.otlpEndpoint": otlp_base_url("copilot"),
        "github.copilot.chat.otel.captureContent": False,
    }


def copilot_status(path: Path) -> dict[str, Any]:
    try:
        data = _load(path, "VS Code settings.json")
    except ConfigConflict as exc:
        return {"configured": False, "path": str(path), "error": str(exc)}
    desired = copilot_settings()
    return {"configured": all(data.get(k) == v for k, v in desired.items()), "path": str(path)}


def copilot_connect(path: Path) -> dict[str, Any]:
    data = _load(path, "VS Code settings.json")
    desired = copilot_settings()
    ours = str(desired["github.copilot.chat.otel.otlpEndpoint"])
    endpoint = data.get("github.copilot.chat.otel.otlpEndpoint")
    if endpoint not in (None, "", ours) and data.get("github.copilot.chat.otel.enabled") is True:
        raise ConfigConflict(
            "VS Code already sends Copilot telemetry to another endpoint; OpenWorkGraph will not redirect it. "
            "Use manual setup or an OTLP collector that forwards to OpenWorkGraph."
        )
    if data.get("github.copilot.chat.otel.captureContent") is True:
        raise ConfigConflict(
            "Copilot content capture is on in VS Code settings; OpenWorkGraph only connects with content capture off."
        )
    data.update(desired)
    backup = _write(path, data)
    return {"configured": True, "path": str(path), "backup": backup, "note": "Takes effect after VS Code reloads (Developer: Reload Window)."}


def copilot_disconnect(path: Path) -> dict[str, Any]:
    data = _load(path, "VS Code settings.json")
    desired = copilot_settings()
    removed = [k for k, v in desired.items() if k in data and data[k] == v]
    for key in removed:
        del data[key]
    backup = _write(path, data) if removed else None
    return {"configured": False, "path": str(path), "removed": len(removed), "backup": backup}


# --- Gemini CLI ----------------------------------------------------------------------------

def gemini_telemetry() -> dict[str, Any]:
    return {
        "enabled": True,
        "target": "local",
        "otlpEndpoint": otlp_base_url("gemini"),
        "otlpProtocol": "http",
        "logPrompts": False,
    }


def gemini_status(path: Path) -> dict[str, Any]:
    try:
        data = _load(path, "Gemini settings.json")
    except ConfigConflict as exc:
        return {"configured": False, "path": str(path), "error": str(exc)}
    telemetry = data.get("telemetry") if isinstance(data.get("telemetry"), dict) else {}
    return {"configured": all(telemetry.get(k) == v for k, v in gemini_telemetry().items()), "path": str(path)}


def gemini_connect(path: Path) -> dict[str, Any]:
    data = _load(path, "Gemini settings.json")
    desired = gemini_telemetry()
    existing = data.get("telemetry")
    if existing is not None and not isinstance(existing, dict):
        raise ConfigConflict(f"'telemetry' in {path} has an unexpected shape; use manual setup")
    existing = dict(existing or {})
    # Only take over a telemetry block that is ours, empty, or switched off.
    foreign = sorted(
        k for k, v in existing.items()
        if desired.get(k) != v and not (k == "enabled" and v is False)
    )
    if foreign:
        raise ConfigConflict(
            f"Gemini CLI already has its own telemetry settings ({', '.join(foreign)}); "
            "OpenWorkGraph will not change them. Use manual setup."
        )
    data["telemetry"] = {**existing, **desired}
    backup = _write(path, data)
    return {"configured": True, "path": str(path), "backup": backup, "note": "Takes effect in new Gemini CLI sessions."}


def gemini_disconnect(path: Path) -> dict[str, Any]:
    data = _load(path, "Gemini settings.json")
    telemetry = data.get("telemetry") if isinstance(data.get("telemetry"), dict) else None
    if telemetry is None:
        return {"configured": False, "path": str(path), "removed": 0, "backup": None}
    desired = gemini_telemetry()
    removed = [k for k, v in desired.items() if telemetry.get(k) == v]
    for key in removed:
        del telemetry[key]
    if not telemetry:
        del data["telemetry"]
    backup = _write(path, data) if removed else None
    return {"configured": False, "path": str(path), "removed": len(removed), "backup": backup}


# --- Cursor --------------------------------------------------------------------------------

def _is_owg_cursor_handler(handler: Any) -> bool:
    from adapters.cursor_hook import OWG_MARKER

    return isinstance(handler, dict) and OWG_MARKER in str(handler.get("command") or "")


def _load_cursor(path: Path) -> dict[str, Any]:
    data = _load(path, "Cursor hooks.json")
    hooks = data.get("hooks")
    if hooks is not None and not isinstance(hooks, dict):
        raise ConfigConflict(f"'hooks' in {path} has an unexpected shape; use manual setup")
    for event, handlers in (hooks or {}).items():
        if not isinstance(handlers, list):
            raise ConfigConflict(f"hooks.{event} in {path} has an unexpected shape; use manual setup")
    return data


def _strip_cursor(data: dict[str, Any]) -> int:
    removed = 0
    hooks = data.get("hooks") or {}
    for event in list(hooks):
        kept = [h for h in hooks[event] if not _is_owg_cursor_handler(h)]
        removed += len(hooks[event]) - len(kept)
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    return removed


def cursor_status(path: Path) -> dict[str, Any]:
    from shared.cursor_hook_adapter import SUPPORTED_EVENTS

    try:
        data = _load_cursor(path)
    except ConfigConflict as exc:
        return {"configured": False, "path": str(path), "error": str(exc)}
    present = {event for event, handlers in (data.get("hooks") or {}).items() if any(_is_owg_cursor_handler(h) for h in handlers)}
    return {"configured": set(SUPPORTED_EVENTS) <= present, "path": str(path)}


def cursor_connect(path: Path) -> dict[str, Any]:
    from adapters.cursor_hook import hooks_fragment

    data = _load_cursor(path)
    version = data.get("version", 1)
    if version != 1:
        raise ConfigConflict(f"{path} uses hooks version {version!r}; OpenWorkGraph writes version 1. Use manual setup.")
    _strip_cursor(data)
    data["version"] = 1
    hooks = data.setdefault("hooks", {})
    for event, handlers in hooks_fragment()["hooks"].items():
        hooks.setdefault(event, []).extend(handlers)
    backup = _write(path, data)
    return {"configured": True, "path": str(path), "backup": backup, "note": "Takes effect after Cursor restarts."}


def cursor_disconnect(path: Path) -> dict[str, Any]:
    data = _load_cursor(path)
    removed = _strip_cursor(data)
    if not data.get("hooks"):
        data.pop("hooks", None)
    backup = _write(path, data) if removed else None
    return {"configured": False, "path": str(path), "removed": removed, "backup": backup}


# --- manual setup (shown by the dashboard's Manual setup button) ---------------------------

def manual_setup_material() -> dict[str, Any]:
    """Manual equivalents of the Copilot, Gemini CLI and Cursor Observe switches."""
    from adapters.cursor_hook import hooks_fragment

    return {
        "vscode": {
            "label": "GitHub Copilot (VS Code)",
            "method": "copilot_otel_http_json",
            "one_click": True,
            "settings": copilot_settings(),
            "instructions": "Use the Observe switch on the VS Code + GitHub Copilot row, or merge these keys into VS Code's user settings.json and reload the window. Content capture stays off.",
            "content_logging_enabled": False,
        },
        "gemini_cli": {
            "label": "Gemini CLI",
            "method": "gemini_otel_http_json_logs",
            "one_click": True,
            "settings": {"telemetry": gemini_telemetry()},
            "instructions": "Use the Observe switch on the Gemini CLI row, or merge this telemetry block into ~/.gemini/settings.json. logPrompts must stay false (Gemini's default is true).",
            "content_logging_enabled": False,
        },
        "cursor": {
            "label": "Cursor",
            "method": "cursor_hooks",
            "one_click": True,
            "settings": hooks_fragment(),
            "instructions": "Use the Observe switch on the Cursor row, or merge these hooks into ~/.cursor/hooks.json and restart Cursor. Only non-blocking hooks are used; OpenWorkGraph never answers a permission hook.",
            "content_logging_enabled": False,
        },
    }
