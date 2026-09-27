from __future__ import annotations

"""One place to connect AI clients to OpenWorkGraph and switch them on/off.

Every supported client can have up to two connections:

* ``mcp``: the client can read the work context you allow (MCP tools).
* ``observe``: OpenWorkGraph records the client's structural execution
  (hooks / OTel traces; never prompts, responses, tool arguments or results).

Turning a connection **on** installs the client's configuration the first time
(see server.agent_config_writer for the backup / preserve / refuse rules) and
sets a runtime switch. Turning it **off** only flips the runtime switch, which
the MCP gate and agent-ingest routes check on every call, so it takes effect
immediately without restarting the client. **remove** uninstalls the
configuration entirely.

The same functions back the dashboard (``/v1/connections``) and the CLI::

    <python> <root>/owg_connect.py list
    <python> <root>/owg_connect.py on claude_code            # mcp + observe
    <python> <root>/owg_connect.py off cursor --mcp
    <python> <root>/owg_connect.py remove codex --observe

The CLI prints JSON so agents can drive it as easily as people.
"""

import argparse
import json
import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from server import agent_config_writer as writer

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "mcp_server" / "launcher.py"
SERVER_NAME = "openworkgraph"
KINDS = ("mcp", "observe")

CODEX_MCP_START = "# >>> OpenWorkGraph MCP context (managed; remove via dashboard) >>>"
CODEX_MCP_END = "# <<< OpenWorkGraph MCP context <<<"


# --- paths -----------------------------------------------------------------------

_home = writer._home


def _app_support(*parts: str) -> Path:
    """Per-user application config directory for desktop apps."""
    home = _home()
    if sys.platform == "darwin":
        return home.joinpath("Library", "Application Support", *parts)
    if sys.platform == "win32" and not os.getenv("OWG_CONNECTIONS_HOME"):
        return Path(os.getenv("APPDATA", str(home / "AppData" / "Roaming"))).joinpath(*parts)
    return home.joinpath(".config", *parts)


def _codex_home() -> Path:
    return writer.codex_config_path().parent


def _data_dir() -> Path:
    return Path(os.getenv("WORKFLOW_OBSERVER_DATA", ROOT / "data"))


# --- MCP launch entry ---------------------------------------------------------

def mcp_command(client: str) -> tuple[str, list[str]]:
    return sys.executable, [str(LAUNCHER), "--client", client]


def _json_entry(client: str, style: str) -> dict[str, Any]:
    command, args = mcp_command(client)
    if style == "claude_code":
        return {"type": "stdio", "command": command, "args": args, "env": {}}
    if style == "vscode":
        return {"type": "stdio", "command": command, "args": args}
    if style == "copilot_cli":
        return {"type": "local", "command": command, "args": args, "env": {}, "tools": ["*"]}
    return {"command": command, "args": args}


# --- JSON mcpServers-style files ----------------------------------------------

def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise writer.ConfigConflict(f"{path} is not valid JSON; fix it or use manual setup") from exc
    if not isinstance(data, dict):
        raise writer.ConfigConflict(f"{path} is not a JSON object; use manual setup")
    return data


def _json_mcp_installed(path: Path, key: str) -> bool:
    try:
        servers = _load_json(path).get(key)
    except writer.ConfigConflict:
        return False
    return isinstance(servers, dict) and SERVER_NAME in servers


def _json_mcp_install(path: Path, key: str, entry: dict[str, Any]) -> dict[str, Any]:
    data = _load_json(path)
    servers = data.setdefault(key, {})
    if not isinstance(servers, dict):
        raise writer.ConfigConflict(f"'{key}' in {path} has an unexpected shape; use manual setup")
    if servers.get(SERVER_NAME) == entry:
        return {"path": str(path), "backup": None}
    servers[SERVER_NAME] = entry
    backup = writer._backup(path)
    writer._atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    # Some clients (notably Claude Code with ~/.claude.json) rewrite their file
    # while running; confirm the entry actually landed.
    if not _json_mcp_installed(path, key):
        raise writer.ConfigConflict(f"{path} was changed by another program during the write; try again")
    return {"path": str(path), "backup": backup}


def _json_mcp_remove(path: Path, key: str) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "backup": None}
    data = _load_json(path)
    servers = data.get(key)
    if not isinstance(servers, dict) or SERVER_NAME not in servers:
        return {"path": str(path), "backup": None}
    del servers[SERVER_NAME]
    backup = writer._backup(path)
    writer._atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return {"path": str(path), "backup": backup}


# --- Codex TOML [mcp_servers.openworkgraph] -----------------------------------

def _codex_mcp_path() -> Path:
    # Same file as Codex observation, so both managed blocks live together.
    return writer.codex_config_path()


def _codex_mcp_installed() -> bool:
    path = _codex_mcp_path()
    return path.exists() and CODEX_MCP_START in path.read_text(encoding="utf-8")


def _codex_mcp_install() -> dict[str, Any]:
    path = _codex_mcp_path()
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    remainder, _ = writer._strip_codex_block(original, CODEX_MCP_START, CODEX_MCP_END)
    parsed = writer._parse_toml(remainder, path)
    if SERVER_NAME in (parsed.get("mcp_servers") or {}):
        raise writer.ConfigConflict(
            f"{path} already defines [mcp_servers.{SERVER_NAME}] by hand; remove it or keep using it"
        )
    command, args = mcp_command("codex")
    block = "\n".join([
        CODEX_MCP_START,
        f"[mcp_servers.{SERVER_NAME}]",
        f"command = {json.dumps(command)}",
        "args = [" + ", ".join(json.dumps(a) for a in args) + "]",
        CODEX_MCP_END,
        "",
    ])
    body = remainder.rstrip("\n")
    updated = (body + "\n\n" if body else "") + block
    if SERVER_NAME not in (writer._parse_toml(updated, path).get("mcp_servers") or {}):
        raise writer.ConfigConflict("Generated Codex MCP configuration did not validate; use manual setup")
    if updated == original:
        return {"path": str(path), "backup": None}
    backup = writer._backup(path)
    writer._atomic_write(path, updated)
    return {"path": str(path), "backup": backup}


def _codex_mcp_remove() -> dict[str, Any]:
    path = _codex_mcp_path()
    if not path.exists():
        return {"path": str(path), "backup": None}
    original = path.read_text(encoding="utf-8")
    remainder, removed = writer._strip_codex_block(original, CODEX_MCP_START, CODEX_MCP_END)
    if not removed:
        return {"path": str(path), "backup": None}
    writer._parse_toml(remainder, path)
    backup = writer._backup(path)
    writer._atomic_write(path, remainder)
    return {"path": str(path), "backup": backup}


# --- registry -------------------------------------------------------------------

@dataclass(frozen=True)
class Target:
    path: Callable[[], Path]
    installed: Callable[[], bool]
    install: Callable[[], dict[str, Any]]
    remove: Callable[[], dict[str, Any]]
    applies: str  # when a fresh install takes effect
    # Long-running apps load MCP/telemetry config only at startup. When set,
    # a matching process that started before the install is reported as
    # "restart needed" instead of silently waiting.
    process: Callable[[str, list[str]], str] | None = None


def _json_target(path_fn: Callable[[], Path], key: str, client: str, style: str, applies: str,
                 process: Callable[[str, list[str]], str] | None = None) -> Target:
    return Target(
        path=path_fn,
        installed=lambda: _json_mcp_installed(path_fn(), key),
        install=lambda: _json_mcp_install(path_fn(), key, _json_entry(client, style)),
        remove=lambda: _json_mcp_remove(path_fn(), key),
        applies=applies,
        process=process,
    )


# --- restart detection ----------------------------------------------------------

def _codex_process(exe: str, cmdline: list[str]) -> str:
    """Return a human app name if this is a Codex engine process, else ''."""
    first = cmdline[0] if cmdline else exe
    if Path(first).name.lower() not in {"codex", "codex.exe"}:
        return ""
    return "the ChatGPT app" if "ChatGPT.app" in first else "Codex"


def _claude_desktop_process(exe: str, cmdline: list[str]) -> str:
    first = cmdline[0] if cmdline else exe
    if first.endswith("Claude.app/Contents/MacOS/Claude") or (
        "AnthropicClaude" in first and Path(first).name.lower() == "claude.exe"
    ):
        return "Claude Desktop"
    return ""


_PROCESS_CACHE: dict[str, Any] = {"at": 0.0, "procs": []}


def _processes() -> list[tuple[str, list[str], float]]:
    """(exe, cmdline, start time) for running processes, cached briefly."""
    import time
    now = time.monotonic()
    if now - _PROCESS_CACHE["at"] < 4.0:
        return _PROCESS_CACHE["procs"]
    procs: list[tuple[str, list[str], float]] = []
    try:
        import psutil
        for proc in psutil.process_iter(["exe", "cmdline", "create_time"]):
            try:
                info = proc.info
                procs.append((str(info.get("exe") or ""), [str(x) for x in (info.get("cmdline") or [])],
                              float(info.get("create_time") or 0)))
            except Exception:
                continue
    except Exception:
        procs = []
    _PROCESS_CACHE.update(at=now, procs=procs)
    return procs


def _running_since(match: Callable[[str, list[str]], str]) -> tuple[float, str] | None:
    """Earliest start time of a running process the matcher recognizes."""
    found: tuple[float, str] | None = None
    for exe, cmdline, started in _processes():
        try:
            name = match(exe, cmdline)
        except Exception:
            continue
        if name and started and (found is None or started < found[0]):
            found = (started, name)
    return found


def _installed_at(client_id: str, kind: str, path: Path) -> float | None:
    recorded = ((_read_switches().get("installed_at") or {}).get(kind) or {}).get(client_id)
    backups = [p.stat().st_mtime for p in path.parent.glob(f"{path.name}.owg-backup-*") if p.exists()]
    candidates = [float(recorded)] if recorded else []
    candidates += [max(backups)] if backups else []
    return max(candidates) if candidates else None


def _restart_needed(client_id: str, kind: str, target: Target) -> dict[str, Any] | None:
    if target.process is None:
        return None
    installed_at = _installed_at(client_id, kind, target.path())
    running = _running_since(target.process)
    if installed_at is None or running is None or running[0] >= installed_at:
        return None
    from datetime import datetime, timezone
    return {
        "app": running[1],
        "running_since": datetime.fromtimestamp(running[0], timezone.utc).isoformat(),
    }


def _claude_observe() -> Target:
    from adapters.claude_code_hook import settings_fragment
    return Target(
        path=writer.claude_settings_path,
        installed=lambda: bool(writer.claude_status().get("configured")),
        install=lambda: writer.claude_connect(settings_fragment),
        remove=writer.claude_disconnect,
        applies="in new Claude Code sessions",
    )


def _codex_observe() -> Target:
    def install() -> dict[str, Any]:
        from adapters._agent_client import _base_url
        from adapters.codex_config import config_snippet
        from server.agent_auth import ensure_agent_ingest_token
        return writer.codex_connect(config_snippet(token=ensure_agent_ingest_token(), base_url=_base_url()))
    return Target(
        path=writer.codex_config_path,
        installed=lambda: bool(writer.codex_status().get("configured")),
        install=install,
        remove=writer.codex_disconnect,
        applies="the next time Codex starts",
        process=_codex_process,
    )


@dataclass(frozen=True)
class Client:
    id: str
    label: str
    detect: Callable[[], Path]
    mcp: Target | None
    observe: Callable[[], Target] | None
    observe_unavailable: str = ""


def _copilot_home() -> Path:
    override = os.getenv("COPILOT_HOME", "").strip()
    return Path(override) if override and not os.getenv("OWG_CONNECTIONS_HOME") else _home() / ".copilot"


def _claude_code_json() -> Path:
    override = os.getenv("OWG_CLAUDE_CODE_MCP_PATH", "").strip()
    return Path(override) if override else _home() / ".claude.json"


CLIENTS: dict[str, Client] = {
    c.id: c for c in [
        Client("claude_code", "Claude Code", lambda: _home() / ".claude",
               _json_target(_claude_code_json, "mcpServers", "claude_code", "claude_code", "in new Claude Code sessions"),
               _claude_observe),
        Client("claude_desktop", "Claude Desktop", lambda: _app_support("Claude"),
               _json_target(lambda: _app_support("Claude", "claude_desktop_config.json"), "mcpServers",
                            "claude_desktop", "plain", "after you quit and reopen Claude Desktop",
                            _claude_desktop_process),
               None, "Claude Desktop has no execution hooks to observe."),
        Client("codex", "Codex", _codex_home,
               Target(_codex_mcp_path, _codex_mcp_installed, _codex_mcp_install, _codex_mcp_remove,
                      "in new Codex chats"),
               _codex_observe),
        Client("cursor", "Cursor", lambda: _home() / ".cursor",
               _json_target(lambda: _home() / ".cursor" / "mcp.json", "mcpServers", "cursor", "plain",
                            "after Cursor reloads its MCP servers"),
               None, "Cursor does not expose an execution trace to observe."),
        Client("vscode", "VS Code + GitHub Copilot", lambda: _app_support("Code", "User"),
               _json_target(lambda: _app_support("Code", "User", "mcp.json"), "servers", "vscode", "vscode",
                            "after VS Code reloads its MCP servers"),
               None, "VS Code does not expose an execution trace to observe."),
        Client("windsurf", "Windsurf", lambda: _home() / ".codeium" / "windsurf",
               _json_target(lambda: _home() / ".codeium" / "windsurf" / "mcp_config.json", "mcpServers",
                            "windsurf", "plain", "after Windsurf refreshes its MCP servers"),
               None, "Windsurf does not expose an execution trace to observe."),
        Client("gemini_cli", "Gemini CLI", lambda: _home() / ".gemini",
               _json_target(lambda: _home() / ".gemini" / "settings.json", "mcpServers", "gemini_cli", "plain",
                            "in new Gemini CLI sessions"),
               None, "Gemini CLI observation is not supported yet."),
        Client("copilot_cli", "GitHub Copilot CLI", _copilot_home,
               _json_target(lambda: _copilot_home() / "mcp-config.json", "mcpServers", "copilot_cli",
                            "copilot_cli", "in new Copilot CLI sessions"),
               None, "Copilot CLI observation is not supported yet."),
        Client("kiro", "Kiro", lambda: _home() / ".kiro",
               _json_target(lambda: _home() / ".kiro" / "settings" / "mcp.json", "mcpServers", "kiro", "plain",
                            "after Kiro reloads its MCP servers"),
               None, "Kiro observation is not supported yet."),
        Client("amazon_q", "Amazon Q Developer", lambda: _home() / ".aws" / "amazonq",
               _json_target(lambda: _home() / ".aws" / "amazonq" / "mcp.json", "mcpServers", "amazon_q", "plain",
                            "in new Amazon Q Developer sessions"),
               None, "Amazon Q Developer observation is not supported yet."),
    ]
}


def _target(client: Client, kind: str) -> Target | None:
    if kind == "mcp":
        return client.mcp
    return client.observe() if client.observe else None


# --- runtime switches ---------------------------------------------------------

_LOCK = threading.RLock()
_CACHE: dict[str, Any] = {"mtime": None, "data": {}}


def _switch_path() -> Path:
    return _data_dir() / "connections.json"


def _read_switches() -> dict[str, dict[str, bool]]:
    path = _switch_path()
    with _LOCK:
        try:
            mtime = path.stat().st_mtime_ns
        except FileNotFoundError:
            _CACHE.update(mtime=None, data={})
            return {}
        if _CACHE["mtime"] == mtime:
            return _CACHE["data"]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        data = data if isinstance(data, dict) else {}
        _CACHE.update(mtime=mtime, data=data)
        return data


def _write_switch(client_id: str, kind: str, enabled: bool) -> None:
    path = _switch_path()
    with _LOCK:
        data = json.loads(json.dumps(_read_switches()))
        data.setdefault(kind, {})[client_id] = bool(enabled)
        writer._atomic_write(path, json.dumps(data, indent=2) + "\n")
        # Do not depend on filesystem timestamp granularity after an atomic
        # replace. Windows can report the same mtime for rapid successive
        # writes, which previously let the cache return the pre-write state.
        try:
            mtime = path.stat().st_mtime_ns
        except FileNotFoundError:
            mtime = None
        _CACHE.update(mtime=mtime, data=json.loads(json.dumps(data)))


def _record_install(client_id: str, kind: str) -> None:
    import time
    with _LOCK:
        data = json.loads(json.dumps(_read_switches()))
        data.setdefault("installed_at", {}).setdefault(kind, {})[client_id] = time.time()
        writer._atomic_write(_switch_path(), json.dumps(data, indent=2) + "\n")
        _CACHE.update(mtime=_switch_path().stat().st_mtime_ns, data=data)


def is_enabled(client_id: str | None, kind: str) -> bool:
    """Runtime switch; unknown clients and never-touched switches default to on."""
    if not client_id:
        return True
    value = (_read_switches().get(kind) or {}).get(client_id)
    return True if value is None else bool(value)


# Agent frameworks whose observation is controlled by a client switch.
FRAMEWORK_CLIENTS = {"claude-code": "claude_code", "codex": "codex"}


def observation_active(client_id: str) -> bool:
    """True when observation is both installed and switched on for this client."""
    client = CLIENTS.get(client_id)
    if client is None or client.observe is None:
        return True
    try:
        return is_enabled(client_id, "observe") and bool(client.observe().installed())
    except writer.ConfigConflict:
        return False


def hidden_frameworks() -> set[str]:
    """Frameworks whose stored history the Agents view hides by default."""
    return {fw for fw, cid in FRAMEWORK_CLIENTS.items() if not observation_active(cid)}


# --- public operations ----------------------------------------------------------

def _kind_status(client: Client, kind: str) -> dict[str, Any]:
    if kind == "observe" and client.observe is None:
        return {"supported": False, "reason": client.observe_unavailable}
    target = _target(client, kind)
    try:
        installed = target.installed()
        error = None
    except writer.ConfigConflict as exc:
        installed, error = False, str(exc)
    enabled = is_enabled(client.id, kind)
    status = {
        "supported": True,
        "installed": installed,
        "enabled": enabled,
        "on": installed and enabled,
        "path": str(target.path()),
    }
    if error:
        status["error"] = error
    if installed and enabled:
        restart = _restart_needed(client.id, kind, target)
        if restart:
            status["restart_needed"] = restart
    return status


def client_status(client_id: str) -> dict[str, Any]:
    client = CLIENTS[client_id]
    return {
        "id": client.id,
        "label": client.label,
        "detected": client.detect().exists(),
        "mcp": _kind_status(client, "mcp"),
        "observe": _kind_status(client, "observe"),
    }


def list_connections() -> dict[str, Any]:
    return {
        "clients": [client_status(cid) for cid in CLIENTS],
        "cli": cli_command(),
        "notes": {
            "on_off": "On/off takes effect immediately; no restart needed once a client is set up.",
            "mcp_master_switch": "MCP reads also require the dashboard AI-access switch, which resets to OFF on restart.",
        },
    }


def _kinds_for(client: Client, kinds: tuple[str, ...]) -> list[str]:
    return [k for k in kinds if not (k == "observe" and client.observe is None)]


def change(client_id: str, action: str, kinds: tuple[str, ...] = KINDS) -> dict[str, Any]:
    """Apply on/off/remove to one client. Raises KeyError / ValueError / ConfigConflict."""
    if client_id not in CLIENTS:
        raise KeyError(client_id)
    if action not in {"on", "off", "remove"}:
        raise ValueError(f"unknown action {action!r}")
    if any(k not in KINDS for k in kinds):
        raise ValueError("kinds must be mcp and/or observe")
    client = CLIENTS[client_id]
    selected = _kinds_for(client, kinds)
    if not selected:
        raise ValueError(f"{client.label} supports only: mcp")
    changes: dict[str, Any] = {}
    for kind in selected:
        target = _target(client, kind)
        if action == "on":
            result = {"backup": None}
            if not target.installed():
                result = target.install()
                result["takes_effect"] = target.applies
                _record_install(client.id, kind)
            _write_switch(client.id, kind, True)
        elif action == "off":
            _write_switch(client.id, kind, False)
            result = {"takes_effect": "immediately"}
        else:
            result = target.remove()
            _write_switch(client.id, kind, False)
        changes[kind] = result
    return {"action": action, "changes": changes, **client_status(client.id)}


# --- CLI ------------------------------------------------------------------------------

def cli_command() -> str:
    """A command that works from any directory (for people and agents)."""
    import shlex
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(ROOT / 'owg_connect.py'))}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="owg_connect.py",
        description="Connect AI clients to OpenWorkGraph and switch them on/off. Prints JSON.",
    )
    parser.add_argument("action", choices=["list", "on", "off", "remove"])
    parser.add_argument("client", nargs="?", choices=sorted(CLIENTS))
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--mcp", action="store_true", help="only the MCP context connection")
    group.add_argument("--observe", action="store_true", help="only agent observation")
    args = parser.parse_args(argv)

    if args.action == "list":
        print(json.dumps(list_connections(), indent=2))
        return 0
    if not args.client:
        parser.error("a client is required for on/off/remove")
    kinds = ("mcp",) if args.mcp else ("observe",) if args.observe else KINDS
    try:
        result = change(args.client, args.action, kinds)
    except writer.ConfigConflict as exc:
        print(json.dumps({"error": str(exc), "manual_setup_required": True}, indent=2))
        return 2
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())