from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .local_auth import auth_dir

ROOT = Path(__file__).resolve().parents[1]
HOST = "127.0.0.1"
_LOCK = threading.RLock()
_PROCESS: subprocess.Popen | None = None
_ENDPOINT: str | None = None
_INSTANCE_NONCE: str | None = None
_TOKEN: str | None = None


def _port_free(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((HOST, port))
        return True
    except OSError:
        return False


def _candidate_ports() -> list[int]:
    ports = [8788]
    for _ in range(8):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((HOST, 0))
            ports.append(int(s.getsockname()[1]))
    return ports


def _write_state(endpoint: str | None) -> None:
    path = auth_dir() / "mcp_endpoint.json"
    payload = {"endpoint": endpoint, "running": bool(endpoint), "updated_at": datetime.now(timezone.utc).isoformat()}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    try: os.chmod(tmp, 0o600)
    except Exception: pass
    os.replace(tmp, path)
    try: os.chmod(path, 0o600)
    except Exception: pass


def _alive() -> bool:
    return _PROCESS is not None and _PROCESS.poll() is None and bool(_ENDPOINT) and bool(_TOKEN)


def _verify_instance(port: int, nonce: str, token: str) -> bool:
    request = urllib.request.Request(
        f"http://{HOST}:{port}/openworkgraph-id",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=0.4) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read().decode("utf-8"))
            return payload.get("server") == "OpenWorkGraph" and payload.get("instance_nonce") == nonce
    except Exception:
        return False


def status() -> dict[str, Any]:
    global _PROCESS, _ENDPOINT, _INSTANCE_NONCE, _TOKEN
    with _LOCK:
        if _PROCESS is not None and _PROCESS.poll() is not None:
            _PROCESS = None; _ENDPOINT = None; _INSTANCE_NONCE = None; _TOKEN = None; _write_state(None)
        return {"running": _alive(), "endpoint": _ENDPOINT if _alive() else None, "transport": "streamable-http" if _alive() else None}


def start_http_mcp() -> dict[str, Any]:
    global _PROCESS, _ENDPOINT, _INSTANCE_NONCE, _TOKEN
    with _LOCK:
        current = status()
        if current["running"]:
            # An explicit start request may need the currently active token for
            # setup UI, but ordinary status calls never reveal it.
            return {**current, "token": _TOKEN}

        for port in _candidate_ports():
            if not _port_free(port):
                continue
            nonce = secrets.token_urlsafe(32)
            token = secrets.token_urlsafe(48)
            env = os.environ.copy()
            env.pop("WORKFLOW_OBSERVER_DASHBOARD_BOOTSTRAP", None)
            env["WORKFLOW_OBSERVER_MCP_INSTANCE_NONCE"] = nonce
            env["WORKFLOW_OBSERVER_MCP_BRIDGE_TOKEN"] = token
            process = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "mcp_server.compact_http_app:app", "--host", HOST, "--port", str(port)],
                cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            deadline = time.time() + 4.0
            while time.time() < deadline:
                if process.poll() is not None:
                    break
                if _verify_instance(port, nonce, token):
                    _PROCESS = process
                    _ENDPOINT = f"http://{HOST}:{port}/mcp"
                    _INSTANCE_NONCE = nonce
                    _TOKEN = token
                    _write_state(_ENDPOINT)
                    return {"running": True, "endpoint": _ENDPOINT, "transport": "streamable-http", "token": token}
                time.sleep(0.08)
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=1)
                except Exception: process.kill()
        _TOKEN = None
        _write_state(None)
        return {"running": False, "endpoint": None, "transport": None, "error": "Could not start and verify local HTTP MCP"}


def stop_http_mcp() -> dict[str, Any]:
    global _PROCESS, _ENDPOINT, _INSTANCE_NONCE, _TOKEN
    with _LOCK:
        process = _PROCESS
        _PROCESS = None; _ENDPOINT = None; _INSTANCE_NONCE = None; _TOKEN = None
        if process is not None and process.poll() is None:
            process.terminate()
            try: process.wait(timeout=2)
            except Exception: process.kill()
        _write_state(None)
        return {"running": False, "endpoint": None, "transport": None}
