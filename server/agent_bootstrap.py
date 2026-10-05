from __future__ import annotations

"""Autonomous local OpenWorkGraph bootstrap for AI agents.

The bootstrap performs safe machine actions itself. It stops only at boundaries
that genuinely require the person (OS privacy approval, AI access the person
explicitly turned off, or an app restart that cannot be performed from inside the running app).

This module intentionally uses only the Python standard library plus
server.connections so it can run from a fresh GitHub checkout before OWG's
runtime dependencies are installed.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

from . import connections


ROOT = Path(__file__).resolve().parents[1]
HEALTH_URL = "http://127.0.0.1:8787/health"


def _log_path() -> Path:
    return Path(tempfile.gettempdir()) / "openworkgraph-agent-bootstrap.log"


def _json_command(command: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None,
                  timeout: int = 90) -> dict[str, Any]:
    run = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    stdout = (run.stdout or "").strip()
    if run.returncode != 0:
        return {
            "ok": False,
            "returncode": run.returncode,
            "stderr": (run.stderr or "")[-4000:],
            "stdout": stdout[-4000:],
        }
    try:
        payload = json.loads(stdout)
    except Exception:
        return {
            "ok": False,
            "returncode": run.returncode,
            "stderr": (run.stderr or "")[-4000:],
            "stdout": stdout[-4000:],
            "error": "command did not return JSON",
        }
    return {"ok": True, "payload": payload}


def _installed_version(root: Path) -> str:
    try:
        return (root / "VERSION").read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _health(expected_version: str = "") -> dict[str, Any]:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=1.5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        version_ok = not expected_version or str(payload.get("version") or "") == expected_version
        mode_ok = str(payload.get("mode") or "") in {"observe", "demo"}
        return {
            "ok": response.status == 200 and payload.get("status") == "ok" and version_ok and mode_ok,
            "payload": payload,
            "expected_version": expected_version or None,
            "version_ok": version_ok,
            "mode_ok": mode_ok,
        }
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "expected_version": expected_version or None}


def _wait_for_health(timeout: float = 75.0, *, expected_version: str = "") -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    latest = _health(expected_version)
    while time.monotonic() < deadline:
        if latest.get("ok"):
            return latest
        time.sleep(0.75)
        latest = _health(expected_version)
    return latest


def _wait_for_runtime(timeout: float = 75.0) -> tuple[Path, Path | None] | None:
    """Wait for both the durable source root and its private Python runtime."""
    deadline = time.monotonic() + timeout
    installed = connections._find_installed_runtime()
    while time.monotonic() < deadline:
        if installed is not None and installed[1] is not None:
            return installed
        time.sleep(0.75)
        installed = connections._find_installed_runtime()
    return installed if installed is not None and installed[1] is not None else None


def _launch_background(command: list[str], *, cwd: Path | None = None) -> dict[str, Any]:
    log = _log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    handle = open(log, "a", encoding="utf-8")
    try:
        process = subprocess.Popen(
            command,
            cwd=str(cwd) if cwd else None,
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=(os.name != "nt"),
        )
    finally:
        handle.close()
    return {"pid": process.pid, "log": str(log), "_process": process}


def _install_if_needed() -> dict[str, Any]:
    installed = connections._find_installed_runtime()
    if installed is not None and installed[1] is not None:
        return {
            "ok": True,
            "performed": False,
            "runtime": installed,
        }

    # A source root without its private Python is an incomplete/provisioning
    # install. Re-enter the official idempotent installer instead of asking the
    # person to repair it manually.
    instruction = connections._install_instruction()
    command = list(instruction.get("command") or [])
    if not command:
        return {
            "ok": False,
            "status": "unsupported_local_platform",
            "platform": instruction.get("platform"),
            "reason": (
                "The OpenWorkGraph desktop companion currently has official installers only for macOS and Windows."
            ),
        }

    launched = _launch_background(command, cwd=ROOT)
    process = launched.pop("_process")
    installed = None
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        installed = connections._find_installed_runtime()
        if installed is not None:
            break
        code = process.poll()
        if code is not None and code != 0:
            return {
                "ok": False,
                "status": "install_failed",
                "returncode": code,
                "log": launched["log"],
            }
        time.sleep(1.0)

    if installed is None:
        return {
            "ok": False,
            "status": "install_not_detected",
            "log": launched["log"],
        }
    return {
        "ok": True,
        "performed": True,
        "runtime": installed,
        "installer_pid": launched["pid"],
        "log": launched["log"],
        "_process": process,
    }


def _start_if_needed(root: Path, python: Path) -> dict[str, Any]:
    expected_version = _installed_version(root)
    health = _health(expected_version)
    if health.get("ok"):
        return {"ok": True, "performed": False, "health": health}

    command = [str(python), str(root / "start.py"), "--mode", "observe"]
    launched = _launch_background(command, cwd=root)
    health = _wait_for_health(expected_version=expected_version)
    return {
        "ok": bool(health.get("ok")),
        "performed": True,
        "pid": launched["pid"],
        "log": launched["log"],
        "health": health,
    }


def _runtime_env(root: Path, client_id: str) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root)
    env["WORKFLOW_OBSERVER_API"] = "http://127.0.0.1:8787"
    env["WORKFLOW_OBSERVER_AUTH_DIR"] = str(root / "data" / "auth")
    env["WORKFLOW_OBSERVER_DATA"] = str(root / "data" / "live")
    env["OWG_MCP_CLIENT"] = client_id
    return env


def _permission_state(root: Path, python: Path) -> dict[str, Any]:
    script = (
        "import json;"
        "from collector.permissions import sensor_permissions, missing;"
        "p=sensor_permissions();"
        "print(json.dumps({'permissions':p,'missing':missing(p)}))"
    )
    result = _json_command([str(python), "-c", script], cwd=root, env=_runtime_env(root, "bootstrap"))
    if not result.get("ok"):
        return {"ok": False, "permissions": {}, "missing": [], "error": result}
    payload = result["payload"]
    return {
        "ok": True,
        "permissions": payload.get("permissions") or {},
        "missing": list(payload.get("missing") or []),
    }


def _access_and_context_probe(root: Path, python: Path, client_id: str) -> dict[str, Any]:
    script = r"""
import json
from server.ai_access import ai_access_enabled, resets_on_restart
out = {
    "enabled": bool(ai_access_enabled()),
    "resets_on_restart": bool(resets_on_restart()),
    "probe": {"ok": False, "reason": "ai_access_off"},
}
if out["enabled"]:
    try:
        from mcp_server import secure_runtime
        secure_runtime.authorize_tool("bootstrap_probe")
        payload = secure_runtime.secure_get("/v1/workflow-trace", {"limit": 1, "scope": "current"})
        out["probe"] = {
            "ok": True,
            "returned": len(list(payload.get("rows") or [])),
            "detail_level": secure_runtime.detail_level(),
        }
    except Exception as exc:
        out["probe"] = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
print(json.dumps(out))
""".strip()
    result = _json_command(
        [str(python), "-c", script],
        cwd=root,
        env=_runtime_env(root, client_id),
    )
    if not result.get("ok"):
        return {
            "enabled": None,
            "probe": {"ok": False, "reason": "probe_command_failed"},
            "error": result,
        }
    return result["payload"]


def _connect_known(root: Path, python: Path, client_id: str) -> dict[str, Any]:
    result = _json_command(
        [str(python), str(root / "owg_connect.py"), "on", client_id, "--mcp"],
        cwd=root,
        env=_runtime_env(root, client_id),
    )
    if not result.get("ok"):
        return {"ok": False, "error": result}
    return {"ok": True, "result": result["payload"]}


def _restart_action(connection: dict[str, Any]) -> dict[str, Any] | None:
    if not connection.get("ok"):
        return None
    result = connection.get("result") or {}
    mcp = result.get("mcp") or {}
    restart = mcp.get("restart_needed")
    if restart:
        return {
            "kind": "restart_ai_client",
            "app": restart.get("app") or result.get("label") or "AI client",
            "instruction": "Quit and reopen this AI client so it loads the new OpenWorkGraph MCP connection, then rerun bootstrap.",
        }
    takes_effect = ((result.get("changes") or {}).get("mcp") or {}).get("takes_effect")
    if takes_effect and "immediately" not in str(takes_effect).lower():
        return {
            "kind": "reload_ai_client",
            "app": result.get("label") or result.get("id") or "AI client",
            "takes_effect": takes_effect,
            "instruction": (
                f"The MCP configuration is installed and takes effect {takes_effect}. "
                "Reload/start the required client session, then rerun bootstrap so the connection can be verified."
            ),
        }
    return None


def _mac_permission_action(missing: list[str]) -> dict[str, Any] | None:
    if not missing:
        return None
    labels = {
        "accessibility": "Accessibility",
        "input_monitoring": "Input Monitoring",
    }
    human = [labels.get(item, item) for item in missing]
    return {
        "kind": "macos_privacy_permissions",
        "permissions": human,
        "instruction": (
            "macOS requires your approval for " + " and ".join(human) +
            ". In System Settings → Privacy & Security, allow OpenWorkGraph/its launcher, then rerun the bootstrap."
        ),
    }


def _ai_access_action() -> dict[str, Any]:
    return {
        "kind": "ai_access",
        "instruction": (
            "OpenWorkGraph AI access is OFF because the person previously disabled it or configured it to reset OFF on restart. "
            "Turn AI access on in the local dashboard, then rerun the same bootstrap command. "
            "New installations normally start ON at Redacted."
        ),
    }


def run(
    *,
    local: bool = False,
    remote: bool = False,
    client_id: str = "",
    self_route: bool = False,
    name: str = "",
) -> dict[str, Any]:
    """Install, start, connect and verify OWG with minimal human involvement."""
    if local and remote:
        return {"status": "error", "error": "--local and --remote are mutually exclusive"}
    if remote:
        return connections.setup_self(remote=True, name=name)
    if not local:
        diagnostic = connections.setup_self(name=name)
        diagnostic["bootstrap_available"] = True
        diagnostic["instruction"] = (
            "This command will not guess whether a shell is the user's computer. "
            "Rerun bootstrap with --local only when this process really runs on the user's Mac/Windows machine, "
            "or --remote for a hosted/cloud environment."
        )
        return diagnostic
    if bool(client_id) == bool(self_route):
        return {
            "status": "error",
            "error": "local bootstrap requires exactly one of --client <known-id> or --self --name <agent>",
        }

    if sys.platform not in {"darwin", "win32"}:
        return {
            "status": "unsupported_local_platform",
            "environment": "local",
            "writes_performed": False,
            "platform": sys.platform,
            "reason": "Official autonomous desktop bootstrap currently supports macOS and Windows.",
        }

    installed = _install_if_needed()
    if not installed.get("ok"):
        return installed
    root, python = installed["runtime"]
    if python is None:
        runtime = _wait_for_runtime(180)
        if runtime is not None:
            root, python = runtime
    if python is None:
        return {
            "status": "needs_runtime",
            "installed_root": str(root),
            "install_performed": bool(installed.get("performed")),
            "instruction": "Open OpenWorkGraph once so its private Python runtime finishes provisioning, then rerun bootstrap.",
        }

    if installed.get("performed"):
        # The official installer owns the first launch. Wait for that exact
        # bootstrap instead of racing it with a second start.py process.
        health = _wait_for_health(180, expected_version=_installed_version(root))
        installer_process = installed.pop("_process", None)
        if not health.get("ok"):
            code = installer_process.poll() if installer_process is not None else None
            return {
                "status": "install_started_but_not_healthy",
                "installed_root": str(root),
                "install_performed": True,
                "installer_exit_code": code,
                "log": installed.get("log"),
                "health": health,
            }
        started = {"ok": True, "performed": False, "health": health, "started_by": "official_installer"}
    else:
        installed.pop("_process", None)
        started = _start_if_needed(root, python)
        if not started.get("ok"):
            return {
                "status": "start_failed",
                "installed_root": str(root),
                "install_performed": False,
                "start": started,
            }

    if client_id:
        client = client_id
        connection = _connect_known(root, python, client)
        if not connection.get("ok"):
            return {
                "status": "connection_failed",
                "installed_root": str(root),
                "health": started.get("health"),
                "connection": connection,
            }
        agent_actions: list[dict[str, Any]] = []
    else:
        client = connections._external_client_id(name)
        descriptor = connections._generic_local_descriptor(name, root, python)
        connection = {"ok": True, "descriptor": descriptor["connection"], "client_id": client}
        agent_actions = [{
            "kind": "register_mcp_descriptor",
            "instruction": (
                "Register the returned stdio descriptor using your own native MCP registration mechanism now. "
                "Do not ask the user to edit config files."
            ),
            "descriptor": descriptor["connection"],
        }]

    permissions = _permission_state(root, python)
    access = _access_and_context_probe(root, python, client)

    user_actions: list[dict[str, Any]] = []
    permission_action = _mac_permission_action(list(permissions.get("missing") or []))
    if permission_action:
        user_actions.append(permission_action)
    if access.get("enabled") is False:
        user_actions.append(_ai_access_action())
    restart = _restart_action(connection)
    if restart:
        user_actions.append(restart)

    ready = (
        bool(started.get("health", {}).get("ok"))
        and bool(connection.get("ok"))
        and bool(permissions.get("ok"))
        and not permissions.get("missing")
        and access.get("enabled") is True
        and bool((access.get("probe") or {}).get("ok"))
        and not user_actions
        and not agent_actions
    )

    if ready:
        status = "ready"
    elif user_actions:
        status = "needs_user_action"
    elif agent_actions:
        status = "needs_agent_action"
    else:
        status = "verification_failed"

    return {
        "status": status,
        "environment": "local",
        "installed_root": str(root),
        "install_performed": bool(installed.get("performed")),
        "start_performed": bool(started.get("performed")),
        "health": started.get("health"),
        "connection": connection,
        "permissions": permissions,
        "ai_access": {
            "enabled": access.get("enabled"),
            "resets_on_restart": access.get("resets_on_restart"),
        },
        "context_probe": access.get("probe"),
        "agent_actions": agent_actions,
        "user_actions": user_actions,
        "done_when": (
            "status is ready after health, MCP configuration, OS permission state, AI access, and a bounded context read all pass"
        ),
    }
