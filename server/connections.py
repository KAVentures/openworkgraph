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
    <python> <root>/owg_connect.py setup --self              # safe environment routing for any agent
    <python> <root>/owg_connect.py setup --client claude_code # native setup for a known local client
    <python> <root>/owg_connect.py on claude_code            # mcp + observe
    <python> <root>/owg_connect.py off cursor --mcp
    <python> <root>/owg_connect.py remove codex --observe

The CLI prints JSON so agents can drive it as easily as people.
"""

import argparse
import json
import os
import re
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


# --- agent bootstrap / stable installed runtime --------------------------------

def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.expanduser().resolve() == right.expanduser().resolve()
    except Exception:
        return os.path.normcase(os.path.abspath(str(left))) == os.path.normcase(os.path.abspath(str(right)))


def _installed_root_candidates() -> list[Path]:
    """Stable per-user OpenWorkGraph source roots, never arbitrary checkouts."""
    candidates: list[Path] = []
    override = str(os.getenv("OWG_INSTALLED_ROOT") or "").strip()
    if override:
        candidates.append(Path(override).expanduser())

    home = _home()
    if sys.platform == "darwin":
        candidates.append(home / "Library" / "Application Support" / "WorkflowObserver")
    elif sys.platform == "win32":
        local = Path(os.getenv("LOCALAPPDATA", str(home / "AppData" / "Local"))) / "OpenWorkGraph"
        # The offline/clickable installer keeps mutable source in this payload
        # subdirectory. The older ZIP bootstrap installs source at the root.
        candidates.extend([local / ".openworkgraph-src", local])

    unique: list[Path] = []
    for candidate in candidates:
        if not any(_same_path(candidate, seen) for seen in unique):
            unique.append(candidate)
    return unique


def _installed_python(root: Path) -> Path | None:
    """Return a durable interpreter shipped/created by an installed OWG runtime."""
    override = str(os.getenv("OWG_INSTALLED_PYTHON") or "").strip()
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_file():
            return candidate

    for candidate in (
        root / ".venv" / "bin" / "python",
        root / ".venv" / "Scripts" / "python.exe",
    ):
        if candidate.is_file():
            return candidate

    marker = root / "EMBEDDED_PYTHONW.txt"
    if marker.is_file():
        try:
            pythonw = root / marker.read_text(encoding="utf-8").strip()
            python = pythonw.with_name("python.exe")
            if python.is_file():
                return python
        except Exception:
            pass

    if sys.platform == "darwin":
        for app in (
            Path("/Applications/OpenWorkGraph.app"),
            _home() / "Applications" / "OpenWorkGraph.app",
        ):
            payload = app / "Contents" / "Resources" / "openworkgraph"
            embedded = payload / "EMBEDDED_PYTHON.txt"
            if not embedded.is_file():
                continue
            try:
                candidate = payload / embedded.read_text(encoding="utf-8").strip()
            except Exception:
                continue
            if candidate.is_file():
                return candidate
    return None


def _find_installed_runtime() -> tuple[Path, Path | None] | None:
    for root in _installed_root_candidates():
        if (root / "owg_connect.py").is_file() and (root / "mcp_server" / "launcher.py").is_file():
            return root, _installed_python(root)
    return None


def _install_instruction() -> dict[str, Any]:
    if sys.platform == "darwin":
        return {
            "platform": "macos",
            "command": [
                "/bin/bash",
                "-lc",
                "curl -fsSL https://github.com/KAVentures/openworkgraph/releases/latest/download/install.sh | bash",
            ],
            "human_action": "Approve macOS Accessibility/Input Monitoring when OpenWorkGraph asks.",
        }
    if sys.platform == "win32":
        return {
            "platform": "windows",
            "command": [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                "irm https://github.com/KAVentures/openworkgraph/releases/latest/download/install.ps1 | iex",
            ],
            "human_action": "Approve any Windows security/permission prompts shown by the installer.",
        }
    return {
        "platform": sys.platform,
        "command": [],
        "human_action": "Install OpenWorkGraph using the repository's supported local-install instructions, then rerun setup.",
    }


def _setup_access_status() -> dict[str, Any]:
    try:
        from .ai_access import ai_access_enabled
        enabled: bool | None = ai_access_enabled()
    except Exception:
        enabled = None
    return {
        "changed": False,
        "currently_enabled": enabled,
        "required": True,
        "instruction": "OpenWorkGraph AI access remains a human-controlled setting. If access is off, ask the person to enable it in the local dashboard.",
    }


def _external_client_id(name: str) -> str:
    clean = re.sub(r"[^a-z0-9]+", "_", str(name or "").strip().lower()).strip("_")
    clean = clean[:48] or "agent"
    return f"external_{clean}"


def _remote_gateway_result(name: str = "") -> dict[str, Any]:
    """Describe the only supported remote path without pretending this checkout is the user's machine."""
    return {
        "status": "needs_gateway",
        "environment": "remote",
        "agent_name": str(name or "").strip(),
        "writes_performed": False,
        "do_not_install_here": True,
        "reason": (
            "This agent runs outside the user's OpenWorkGraph computer. Installing the desktop companion "
            "in this environment would observe the wrong machine."
        ),
        "remote_mcp": {
            "transport": "streamable_http",
            "endpoint": "https://<customer-controlled-openworkgraph-gateway>/mcp",
            "authentication": "delegated OIDC",
            "prerequisites": [
                "the user's computer is enrolled in a customer-controlled OpenWorkGraph Gateway",
                "the Gateway has per-person remote MCP and delegated OIDC configured",
                "the user's permitted evidence has synchronized to that Gateway",
            ],
        },
        "repo_alone_is_enough": False,
        "instruction": (
            "Do not ask for a local client id and do not install OpenWorkGraph in this sandbox. "
            "If the user has a configured organization Gateway, ask for or use its public HTTPS /mcp "
            "resource through this AI's normal remote-MCP connection flow. Otherwise remote access is "
            "not available from the GitHub repository alone."
        ),
    }


def _generic_local_descriptor(name: str, installed_root: Path, python: Path) -> dict[str, Any]:
    client_id = _external_client_id(name)
    return {
        "status": "descriptor",
        "environment": "local",
        "integration": "generic_local_mcp",
        "client_name": str(name or "").strip() or "Unknown local MCP client",
        "client_id": client_id,
        "writes_performed": False,
        "installed_root": str(installed_root),
        "connection": {
            "name": SERVER_NAME,
            "transport": "stdio",
            "command": str(python),
            "args": [str(installed_root / "mcp_server" / "launcher.py"), "--client", client_id],
        },
        "observe_changed": False,
        "ai_access": _setup_access_status(),
        "instruction": (
            "Register this stdio MCP descriptor using your own native MCP registration mechanism. "
            "OpenWorkGraph does not know this client's config format and therefore will not guess or "
            "edit another product's files. Do not enable Observe unless the person separately asks."
        ),
    }


def setup_self(*, local: bool = False, remote: bool = False, name: str = "") -> dict[str, Any]:
    """Route an arbitrary agent without guessing whether its shell is the user's computer."""
    if local and remote:
        raise ValueError("--local and --remote are mutually exclusive")
    if remote:
        return _remote_gateway_result(name)
    if not local:
        installed = _find_installed_runtime()
        detected = [cid for cid, client in CLIENTS.items() if client.detect().exists()]
        return {
            "status": "environment_required",
            "writes_performed": False,
            "installed_runtime_found": installed is not None,
            "detected_known_clients": detected,
            "instruction": (
                "Decide whether this shell is running on the user's actual OpenWorkGraph computer. "
                "If yes, rerun setup --self --local --name <your-agent-name> (or use --client <id> "
                "for a supported client). If this is a cloud/remote/sandbox environment, rerun "
                "setup --self --remote --name <your-agent-name>. Do not infer local merely from OS."
            ),
        }

    installed = _find_installed_runtime()
    if installed is None:
        install = _install_instruction()
        if not install["command"]:
            return {
                "status": "unsupported_local_platform",
                "environment": "local",
                "writes_performed": False,
                "platform": install["platform"],
                "reason": "The OpenWorkGraph desktop companion currently has official installers only for macOS and Windows.",
                "instruction": (
                    "Do not install the companion into a different remote machine. If this is truly the user's "
                    "Linux workstation, local desktop capture is not supported yet."
                ),
            }
        return {
            "status": "needs_install",
            "environment": "local",
            "writes_performed": False,
            "install": install,
            "then": "Rerun setup --self --local --name <your-agent-name> after installation.",
            "ai_access": {"changed": False},
        }

    installed_root, python = installed
    if python is None:
        return {
            "status": "needs_runtime",
            "environment": "local",
            "writes_performed": False,
            "installed_root": str(installed_root),
            "instruction": "Open OpenWorkGraph once so its private runtime is available, then rerun setup.",
            "ai_access": {"changed": False},
        }
    return _generic_local_descriptor(name, installed_root, python)


def setup_connection(client_id: str, *, allow_source: bool = False) -> dict[str, Any]:
    """Safely bootstrap one local AI's MCP context connection.

    A GitHub checkout is an installation contract, not the durable runtime.
    By default this refuses to save an MCP command pointing into an arbitrary
    clone. allow_source is an explicit developer escape hatch.
    """
    if client_id not in CLIENTS:
        raise KeyError(client_id)

    if allow_source:
        connected = change(client_id, "on", ("mcp",))
        return {
            "status": "configured",
            "runtime": "source_override",
            "source_root": str(ROOT),
            "connection": connected,
            "observe_changed": False,
            "ai_access": _setup_access_status(),
            "warning": "Development override: MCP points at this source checkout.",
        }

    installed = _find_installed_runtime()
    if installed is None:
        return {
            "status": "needs_install",
            "writes_performed": False,
            "client": client_id,
            "install": _install_instruction(),
            "then": f"Rerun owg_connect.py setup --client {client_id}.",
            "ai_access": {"changed": False},
        }

    installed_root, python = installed
    if python is None:
        return {
            "status": "needs_runtime",
            "writes_performed": False,
            "client": client_id,
            "installed_root": str(installed_root),
            "instruction": "Open OpenWorkGraph once so its private runtime is available, then rerun setup.",
            "ai_access": {"changed": False},
        }

    if not _same_path(ROOT, installed_root) or not _same_path(Path(sys.executable), python):
        return {
            "status": "delegate",
            "writes_performed": False,
            "client": client_id,
            "installed_root": str(installed_root),
            "command": [str(python), str(installed_root / "owg_connect.py"), "on", client_id, "--mcp"],
            "instruction": "Execute this command exactly. It uses the installed runtime's backward-compatible MCP-only connection command.",
            "ai_access": {"changed": False},
        }

    connected = change(client_id, "on", ("mcp",))
    return {
        "status": "configured",
        "runtime": "installed",
        "installed_root": str(installed_root),
        "connection": connected,
        "observe_changed": False,
        "ai_access": _setup_access_status(),
    }


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

    def install() -> dict[str, Any]:
        result = writer.claude_connect(settings_fragment)
        # A connected Claude Code client should receive the structural session
        # brief by default. This remains an explicit, reversible local setting.
        from . import agent_brief
        result["session_start_brief"] = agent_brief.set_enabled("claude-code", True)
        return result

    def remove() -> dict[str, Any]:
        result = writer.claude_disconnect()
        from . import agent_brief
        result["session_start_brief"] = agent_brief.set_enabled("claude-code", False)
        return result

    return Target(
        path=writer.claude_settings_path,
        installed=lambda: bool(writer.claude_status().get("configured")),
        install=install,
        remove=remove,
        applies="in new Claude Code sessions",
    )


def _preset_target(path_fn: Callable[[], Path], name: str, applies: str) -> Target:
    from server import agent_observe_presets as presets

    status = getattr(presets, f"{name}_status")
    connect = getattr(presets, f"{name}_connect")
    disconnect = getattr(presets, f"{name}_disconnect")
    return Target(
        path=path_fn,
        installed=lambda: bool(status(path_fn()).get("configured")),
        install=lambda: connect(path_fn()),
        remove=lambda: disconnect(path_fn()),
        applies=applies,
    )


def _copilot_observe() -> Target:
    return _preset_target(lambda: _app_support("Code", "User", "settings.json"), "copilot", "after VS Code reloads its window")


def _gemini_observe() -> Target:
    return _preset_target(lambda: _home() / ".gemini" / "settings.json", "gemini", "in new Gemini CLI sessions")


def _cursor_observe() -> Target:
    return _preset_target(lambda: _home() / ".cursor" / "hooks.json", "cursor", "after Cursor restarts")


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
               _cursor_observe),
        Client("vscode", "VS Code + GitHub Copilot", lambda: _app_support("Code", "User"),
               _json_target(lambda: _app_support("Code", "User", "mcp.json"), "servers", "vscode", "vscode",
                            "after VS Code reloads its MCP servers"),
               _copilot_observe),
        Client("windsurf", "Windsurf", lambda: _home() / ".codeium" / "windsurf",
               _json_target(lambda: _home() / ".codeium" / "windsurf" / "mcp_config.json", "mcpServers",
                            "windsurf", "plain", "after Windsurf refreshes its MCP servers"),
               None, "Windsurf does not expose an execution trace to observe."),
        Client("gemini_cli", "Gemini CLI", lambda: _home() / ".gemini",
               _json_target(lambda: _home() / ".gemini" / "settings.json", "mcpServers", "gemini_cli", "plain",
                            "in new Gemini CLI sessions"),
               _gemini_observe),
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
FRAMEWORK_CLIENTS = {
    "claude-code": "claude_code",
    "codex": "codex",
    "github-copilot": "vscode",
    "gemini-cli": "gemini_cli",
    "cursor": "cursor",
}


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
            "mcp_master_switch": "MCP reads also require the dashboard AI-access switch. A new install starts OFF; afterward the person's choice is remembered unless Privacy is set to reset it on restart.",
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
    parser.add_argument("action", choices=["list", "setup", "bootstrap", "on", "off", "remove"])
    parser.add_argument("client_pos", nargs="?", choices=sorted(CLIENTS))
    parser.add_argument("--client", dest="client_opt", choices=sorted(CLIENTS),
                        help="known local client id")
    parser.add_argument("--self", dest="self_route", action="store_true",
                        help="route an arbitrary agent without guessing another product's config")
    environment = parser.add_mutually_exclusive_group()
    environment.add_argument("--local", action="store_true",
                             help="this shell is the user's actual OpenWorkGraph computer")
    environment.add_argument("--remote", action="store_true",
                             help="this shell is a cloud/remote/sandbox agent environment")
    parser.add_argument("--name", default="", help="agent/product name for generic self-routing and audit identity")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--mcp", action="store_true", help="only the MCP context connection")
    group.add_argument("--observe", action="store_true", help="only agent observation")
    parser.add_argument("--allow-source", action="store_true",
                        help="development only: allow setup to point MCP at this checkout")
    args = parser.parse_args(argv)

    if args.action == "list":
        if any((args.client_pos, args.client_opt, args.self_route, args.local, args.remote, args.name, args.allow_source)):
            parser.error("list does not take setup-routing arguments")
        print(json.dumps(list_connections(), indent=2))
        return 0

    if args.client_pos and args.client_opt and args.client_pos != args.client_opt:
        parser.error("client was supplied twice with different values")
    client_id = args.client_opt or args.client_pos

    if args.action == "bootstrap":
        if args.observe or args.mcp:
            parser.error("bootstrap configures MCP context automatically; do not pass --mcp or --observe")
        if args.allow_source:
            parser.error("--allow-source is development-only for setup --client, not autonomous bootstrap")
        if args.self_route and client_id:
            parser.error("use either --self or --client, not both")
        if args.self_route and not args.name and not args.remote:
            parser.error("bootstrap --self requires --name for a local/diagnostic external agent")
        if not args.self_route and not client_id and args.local:
            parser.error("local bootstrap requires --client <id> or --self --name <agent>")
        from .agent_bootstrap import run as run_agent_bootstrap
        result = run_agent_bootstrap(
            local=args.local,
            remote=args.remote,
            client_id=client_id or "",
            self_route=bool(args.self_route),
            name=args.name,
        )
        print(json.dumps(result, indent=2))
        return 0 if result.get("status") not in {"error", "install_failed", "install_not_detected", "connection_failed", "start_failed"} else 2

    if args.action == "setup":
        if args.observe:
            print(json.dumps({
                "error": "setup configures MCP context only; enable Observe separately and only when the person asks for it",
                "observe_changed": False,
            }, indent=2))
            return 2
        if args.self_route:
            if client_id:
                parser.error("use either --self or --client, not both")
            if args.allow_source:
                parser.error("--allow-source is only for setup --client")
            try:
                result = setup_self(local=args.local, remote=args.remote, name=args.name)
            except ValueError as exc:
                print(json.dumps({"error": str(exc)}, indent=2))
                return 2
            print(json.dumps(result, indent=2))
            return 0
        if args.local or args.remote or args.name:
            parser.error("--local/--remote/--name require setup --self")
        if not client_id:
            parser.error("setup requires --self or a client id")
        try:
            result = setup_connection(client_id, allow_source=args.allow_source)
        except writer.ConfigConflict as exc:
            print(json.dumps({"error": str(exc), "manual_setup_required": True}, indent=2))
            return 2
        print(json.dumps(result, indent=2))
        return 0

    if not client_id:
        parser.error("a client is required for on/off/remove")
    if any((args.self_route, args.local, args.remote, args.name)):
        parser.error("--self/--local/--remote/--name are valid only with setup/bootstrap")
    if args.allow_source:
        parser.error("--allow-source is valid only with setup")
    kinds = ("mcp",) if args.mcp else ("observe",) if args.observe else KINDS
    try:
        result = change(client_id, args.action, kinds)
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