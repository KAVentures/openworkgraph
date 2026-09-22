from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from .config import load_device_token, load_gateway_settings

_LOCK = threading.RLock()
_PROCESS: subprocess.Popen | None = None
_CONFIG_PATH: Path | None = None


def _alive(process: subprocess.Popen | None) -> bool:
    return process is not None and process.poll() is None


def start_sync_worker(config_path: Path) -> dict[str, Any]:
    """Start exactly one optional sync worker for the running local app."""
    global _PROCESS, _CONFIG_PATH
    config_path = config_path.resolve()
    auth_dir = Path(os.getenv("WORKFLOW_OBSERVER_AUTH_DIR", config_path.parent / "data" / "auth"))
    settings = load_gateway_settings(config_path, auth_dir=auth_dir)

    with _LOCK:
        if _alive(_PROCESS):
            return status()
        if not settings.enabled or not settings.url or not load_device_token(settings):
            _PROCESS = None
            _CONFIG_PATH = config_path
            return status()

        env = os.environ.copy()
        env.pop("WORKFLOW_OBSERVER_DASHBOARD_BOOTSTRAP", None)
        _PROCESS = subprocess.Popen(
            [sys.executable, "-m", "connector.sync", "--config", str(config_path)],
            cwd=config_path.parent,
            env=env,
        )
        _CONFIG_PATH = config_path
        return status()


def stop_sync_worker() -> dict[str, Any]:
    global _PROCESS
    with _LOCK:
        process = _PROCESS
        _PROCESS = None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=4)
        return status()


def restart_sync_worker(config_path: Path) -> dict[str, Any]:
    stop_sync_worker()
    return start_sync_worker(config_path)


def status() -> dict[str, Any]:
    with _LOCK:
        process = _PROCESS
        running = _alive(process)
        return {
            "worker_running": running,
            "worker_pid": int(process.pid) if running and process is not None else None,
            "worker_exit_code": None if running or process is None else process.poll(),
            "config_path": str(_CONFIG_PATH) if _CONFIG_PATH is not None else None,
        }
