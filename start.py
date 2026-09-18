from __future__ import annotations

import argparse
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config.json"
EXAMPLE = ROOT / "config.example.json"
DASHBOARD = "http://127.0.0.1:8787"
MCP_HOST = "127.0.0.1"
MCP_PORT = 8788
MCP_ENDPOINT = f"http://{MCP_HOST}:{MCP_PORT}/mcp"
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip() if (ROOT / "VERSION").exists() else "unknown"


def ensure_config() -> None:
    if not CONFIG.exists():
        shutil.copy2(EXAMPLE, CONFIG)


def wait_for_api(timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{DASHBOARD}/health", timeout=1) as r:
                return r.status == 200
        except Exception:
            time.sleep(0.25)
    return False


def port_is_open(host: str, port: int, timeout: float = 0.2) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
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
    data_dir.mkdir(parents=True, exist_ok=True)
    env["WORKFLOW_OBSERVER_DATA"] = str(data_dir)
    env["WORKFLOW_OBSERVER_MODE"] = mode
    env["WORKFLOW_OBSERVER_MCP_ENDPOINT"] = MCP_ENDPOINT
    now = datetime.now(timezone.utc)
    # Demo rows intentionally describe a recent synthetic work period. Starting
    # the demo run two hours earlier keeps those rows inside scope=current.
    run_start = now - timedelta(hours=2) if mode == "demo" else now
    env["WORKFLOW_OBSERVER_RUN_STARTED_AT"] = run_start.isoformat()
    return env


def start_local_mcp(env: dict[str, str]) -> tuple[subprocess.Popen | None, bool]:
    """Start the local streamable-HTTP MCP endpoint without making capture depend on it.

    If another OpenWorkGraph MCP instance already owns the port, reuse it. A
    failure to start MCP is reported to the console but never prevents the local
    observer/dashboard from running.
    """
    if port_is_open(MCP_HOST, MCP_PORT):
        return None, True

    mcp_env = env.copy()
    mcp_env["MCP_TRANSPORT"] = "streamable-http"
    mcp_env["MCP_HOST"] = MCP_HOST
    mcp_env["MCP_PORT"] = str(MCP_PORT)
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "mcp_server.main"],
            cwd=ROOT,
            env=mcp_env,
        )
    except Exception:
        return None, False

    deadline = time.time() + 5.0
    while time.time() < deadline:
        if port_is_open(MCP_HOST, MCP_PORT):
            return process, True
        if process.poll() is not None:
            return process, False
        time.sleep(0.15)
    return process, port_is_open(MCP_HOST, MCP_PORT)


def reset_demo_data(env: dict[str, str]) -> None:
    """Make every demo launch deterministic without touching live observations."""
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
    print("Your data stays on this computer in this prototype. Raw evidence is preserved, with separate searchable context and content-minimized operational layers for AI/MCP.")
    print("Keyboard activity is counted for effort/timing, but key identities and typed text are never stored. Click/scroll interactions are enabled; screenshots are OFF by default.")
    print("Optional browser semantic sensor files are installed in browser_extension/ for navigation and page-control context.")
    if system == "Windows":
        print("Windows: foreground app/title plus keyboard/click/scroll capture are supported. Native UI control semantics use Microsoft UI Automation on a best-effort basis; browser semantics work through the extension.")
    elif system == "Darwin":
        print("macOS: approve Accessibility/Input Monitoring permission if requested for native desktop interaction capture.")
    if args.mode == "observe":
        print("LIVE mode uses its own database; demo data is excluded.")
    else:
        print("DEMO mode uses a separate synthetic-data database and resets on each demo launch.")

    api = subprocess.Popen([
        sys.executable, "-m", "uvicorn", "server.main:app",
        "--host", "127.0.0.1", "--port", "8787"
    ], cwd=ROOT, env=env)

    collector = None
    mcp_process = None
    try:
        if not wait_for_api():
            raise RuntimeError("The local dashboard could not start.")

        mcp_process, mcp_ready = start_local_mcp(env)
        if mcp_ready:
            print(f"Local MCP is ready at {MCP_ENDPOINT}")
        else:
            print("Warning: local MCP could not start. Workflow capture and exports still work normally.")

        if args.mode == "demo":
            subprocess.check_call([sys.executable, "demo_data.py"], cwd=ROOT, env=env)
            webbrowser.open(DASHBOARD)
            print("\nDemo is open in your browser.")
            print("Demo data is isolated from your real observations.")
            print("Close this window or press Ctrl+C when finished.\n")
            while True:
                time.sleep(1)
        else:
            webbrowser.open(DASHBOARD)
            print("\nLIVE observation has started.")
            print("The dashboard shows THIS RUN only and begins at 0 on every launch.")
            print("Unchanged focus is summarized as a span rather than stored as repeated polling rows.")
            print("The durable local outbox retries capture events if the API is temporarily unavailable.")
            print("Reload browser_extension/ after upgrades; the dashboard warns if its version is stale.")
            print("Press Ctrl+C to stop.\n")
            collector = subprocess.Popen([
                sys.executable, "-m", "collector.main", "--config", str(CONFIG)
            ], cwd=ROOT, env=env)
            collector.wait()
    except KeyboardInterrupt:
        print("\nStopping Workflow Observer…")
    finally:
        stop_process(collector)
        stop_process(mcp_process)
        stop_process(api)
        print("Stopped. Local live/demo data remain separated in data/.\n")


if __name__ == "__main__":
    main()
