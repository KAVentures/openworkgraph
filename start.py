from __future__ import annotations

import argparse
import os
import platform
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

from connector.config import load_device_token, load_gateway_settings
from server.local_auth import (
    ensure_api_token,
    ensure_browser_secret,
    ensure_mcp_token,
    write_browser_pairing_bundle,
)

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config.json"
EXAMPLE = ROOT / "config.example.json"
API_HOST = "127.0.0.1"
API_PORT = 8787
DASHBOARD = f"http://{API_HOST}:{API_PORT}"
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip() if (ROOT / "VERSION").exists() else "unknown"


def ensure_config() -> None:
    if not CONFIG.exists():
        shutil.copy2(EXAMPLE, CONFIG)


def port_is_open(host: str, port: int, timeout: float = 0.2) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_for_api(process: subprocess.Popen, timeout: float = 15.0) -> bool:
    """Wait only for the API process we launched; never accept a replacement listener."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(f"{DASHBOARD}/health", timeout=1) as response:
                if response.status == 200 and process.poll() is None:
                    return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


def stop_process(p: subprocess.Popen | None) -> None:
    if p is None or p.poll() is not None:
        return
    p.terminate()
    try:
        p.wait(timeout=3)
    except subprocess.TimeoutExpired:
        p.kill()


def mode_environment(mode: str) -> dict[str, str]:
    env = os.environ.copy()
    data_dir = ROOT / "data" / ("demo" if mode == "demo" else "live")
    auth_dir = ROOT / "data" / "auth"
    data_dir.mkdir(parents=True, exist_ok=True)
    auth_dir.mkdir(parents=True, exist_ok=True)
    env["WORKFLOW_OBSERVER_DATA"] = str(data_dir)
    env["WORKFLOW_OBSERVER_AUTH_DIR"] = str(auth_dir)
    env["WORKFLOW_OBSERVER_MODE"] = mode
    env["WORKFLOW_OBSERVER_DASHBOARD_BOOTSTRAP"] = secrets.token_urlsafe(32)

    # Create local capabilities before child processes start so collector/API and
    # any later MCP process converge on the same installation credentials.
    ensure_api_token(directory=auth_dir)
    ensure_mcp_token(directory=auth_dir)
    ensure_browser_secret(directory=auth_dir)
    write_browser_pairing_bundle(ROOT / "browser_extension", directory=auth_dir)

    now = datetime.now(timezone.utc)
    run_start = now - timedelta(hours=2) if mode == "demo" else now
    env["WORKFLOW_OBSERVER_RUN_STARTED_AT"] = run_start.isoformat()
    return env


def dashboard_url(env: dict[str, str]) -> str:
    # URL fragments are not transmitted in the HTTP request. The page exchanges
    # this launcher-only secret for an HttpOnly local session and immediately
    # removes the fragment from the address bar/history state.
    return f"{DASHBOARD}/#bootstrap={env['WORKFLOW_OBSERVER_DASHBOARD_BOOTSTRAP']}"


def reset_demo_data(env: dict[str, str]) -> None:
    """Make every demo launch deterministic without touching live observations or auth."""
    data_dir = Path(env["WORKFLOW_OBSERVER_DATA"])
    for name in (
        "workflow_observer.db", "workflow_observer.db-wal", "workflow_observer.db-shm",
        "outbox.db", "outbox.db-wal", "outbox.db-shm", "events.jsonl",
        ".presentation_people.json", ".display_redaction_key",
    ):
        try:
            (data_dir / name).unlink(missing_ok=True)
        except Exception:
            pass


def _start_gateway_connector(env: dict[str, str]) -> subprocess.Popen | None:
    """Start sharing only when this endpoint was explicitly configured and enrolled."""
    auth_dir = Path(env["WORKFLOW_OBSERVER_AUTH_DIR"])
    settings = load_gateway_settings(CONFIG, auth_dir=auth_dir)
    if not settings.enabled:
        print("Organization Gateway: OFF — local-only mode.")
        return None
    if not settings.url:
        print("Organization Gateway: configured but URL is empty; local capture will continue only.")
        return None
    if not load_device_token(settings):
        print("Organization Gateway: configured but endpoint is not enrolled; local capture will continue only.")
        print("Enroll with: python -m connector.enroll --gateway <url> --organization <id> --enrollment-token <token>")
        return None

    connector_env = env.copy()
    connector_env.pop("WORKFLOW_OBSERVER_DASHBOARD_BOOTSTRAP", None)
    print(f"Organization Gateway: connected mode configured for {settings.url}")
    print("Gateway sharing is a separate worker; stopping/failing it never stops local capture.")
    return subprocess.Popen(
        [sys.executable, "-m", "connector.sync", "--config", str(CONFIG)],
        cwd=ROOT,
        env=connector_env,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["demo", "observe"], default="observe")
    args = parser.parse_args()
    ensure_config()
    env = mode_environment(args.mode)
    if args.mode == "demo":
        reset_demo_data(env)
    system = platform.system()

    print(f"\nOpenWorkGraph / Workflow Observer {VERSION}")
    print("===========================================")
    print("Evidence is captured and stored locally first. It leaves this computer only through an explicit export/AI connection or an explicitly enrolled organization Gateway.")
    print("Raw rich evidence is canonical; inferred tasks and process labels are regeneratable hints rather than ground truth.")
    print("Keyboard activity is counted for effort/timing, but key identities and typed text are never stored. Click/scroll interactions are enabled; screenshots are OFF by default.")
    print("Local API access is capability-protected; the browser sensor authenticates the OpenWorkGraph server before sending browser evidence.")
    print("Local AI clients use MCP over stdio. HTTP MCP is OFF by default and starts only when explicitly requested for a client that needs it.")
    if system == "Windows":
        print("Windows: foreground app/title plus keyboard/click/scroll capture are supported. Native UI control semantics use Microsoft UI Automation on a best-effort basis; browser semantics work through the extension.")
    elif system == "Darwin":
        print("macOS: approve Accessibility/Input Monitoring permission if requested for native desktop interaction capture.")
    if args.mode == "observe":
        print("LIVE mode uses its own database; demo data is excluded.")
    else:
        print("DEMO mode uses a separate synthetic-data database and resets on each demo launch.")

    if port_is_open(API_HOST, API_PORT):
        raise RuntimeError(
            "Local port 8787 is already in use. OpenWorkGraph will not start capture or send evidence "
            "to an unknown localhost service. Close the process using port 8787 and launch again."
        )

    api = subprocess.Popen([
        sys.executable, "-m", "uvicorn", "server.secure_app:app",
        "--host", API_HOST, "--port", str(API_PORT)
    ], cwd=ROOT, env=env)

    collector = None
    gateway_connector = None
    try:
        if not wait_for_api(api):
            raise RuntimeError("The authenticated local dashboard could not start on port 8787.")

        opened_dashboard = dashboard_url(env)
        if args.mode == "demo":
            subprocess.check_call([sys.executable, "demo_data.py"], cwd=ROOT, env=env)
            webbrowser.open(opened_dashboard)
            print("\nDemo is open in your browser.")
            print("Demo data is isolated from your real observations and is never Gateway-synchronized.")
            print("AI access starts OFF. Enable it in the dashboard only if you want an MCP client to read this run.")
            print("Close this window or press Ctrl+C when finished.\n")
            while True:
                time.sleep(1)
        else:
            gateway_connector = _start_gateway_connector(env)
            webbrowser.open(opened_dashboard)
            print("\nLIVE observation has started.")
            print("The dashboard shows THIS RUN only and begins at 0 on every launch.")
            print("Unchanged focus is summarized as a span rather than stored as repeated polling rows.")
            print("The durable local outbox retries capture events if the local API is temporarily unavailable.")
            print("AI access starts OFF on every launch and can be enabled from the dashboard.")
            print("Reload browser_extension/ after upgrades; the dashboard warns if its version is stale.")
            print("Press Ctrl+C to stop.\n")
            collector_env = env.copy()
            collector_env.pop("WORKFLOW_OBSERVER_DASHBOARD_BOOTSTRAP", None)
            collector = subprocess.Popen([
                sys.executable, "-m", "collector.secure_main", "--config", str(CONFIG)
            ], cwd=ROOT, env=collector_env)
            collector.wait()
    except KeyboardInterrupt:
        print("\nStopping Workflow Observer…")
    finally:
        stop_process(gateway_connector)
        stop_process(collector)
        stop_process(api)
        print("Stopped. Local live/demo data remain separated in data/.\n")


if __name__ == "__main__":
    main()
