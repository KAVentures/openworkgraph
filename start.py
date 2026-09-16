from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config.json"
EXAMPLE = ROOT / "config.example.json"
DASHBOARD = "http://127.0.0.1:8787"


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
    # Demo and real observations MUST NEVER share a database. Keeping the
    # stores separate also makes it obvious to a tester whether live capture
    # is actually producing events.
    data_dir = ROOT / "data" / ("demo" if mode == "demo" else "live")
    data_dir.mkdir(parents=True, exist_ok=True)
    env["WORKFLOW_OBSERVER_DATA"] = str(data_dir)
    env["WORKFLOW_OBSERVER_MODE"] = mode
    env["WORKFLOW_OBSERVER_RUN_STARTED_AT"] = datetime.now(timezone.utc).isoformat()
    return env


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["demo", "observe"], default="observe")
    args = parser.parse_args()
    ensure_config()
    env = mode_environment(args.mode)

    print("\nWorkflow Observer")
    print("=================")
    print("Your data stays on this computer in this prototype. v25 keeps rich local evidence plus a separate privacy-safe operational layer for tasks/MCP.")
    print("Keyboard activity is counted for effort/timing, but key identities and typed text are never stored. Click/scroll interactions are enabled; screenshots are OFF by default.")
    print("Optional browser semantic sensor files are installed in browser_extension/ for deeper page actions.")
    if args.mode == "observe":
        print("LIVE mode uses its own clean database; demo data is excluded.")
    else:
        print("DEMO mode uses a separate synthetic-data database.")

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
            print("The counter increases only when focus materially changes; unchanged polling is not stored.")
            print("On macOS, approve Accessibility/Input Monitoring permission if requested. Keyboard capture stores counts only, never key identities/text.")
            print("Optional: install browser_extension/ to capture navigation, control clicks and form submissions without field values.")
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
