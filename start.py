from __future__ import annotations

import argparse
import os
import platform
import shutil
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
    now = datetime.now(timezone.utc)
    # Demo rows intentionally describe a recent synthetic work period. Starting
    # the demo run two hours earlier keeps those rows inside scope=current.
    run_start = now - timedelta(hours=2) if mode == "demo" else now
    env["WORKFLOW_OBSERVER_RUN_STARTED_AT"] = run_start.isoformat()
    return env


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
        print("Windows: foreground app/title plus keyboard/click/scroll capture are supported. Native desktop UI-control labels are currently richer on macOS; browser semantics work on Windows through the extension.")
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
    try:
        if not wait_for_api():
            raise RuntimeError("The local dashboard could not start.")

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
        stop_process(api)
        print("Stopped. Local live/demo data remain separated in data/.\n")


if __name__ == "__main__":
    main()
