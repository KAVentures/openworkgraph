from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from shared.capture_control import initialize_run, read_state

_STOP = False


def _stop(*_args) -> None:
    global _STOP
    _STOP = True


def _terminate(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=4)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=4)


def run_supervisor(config_path: Path) -> int:
    """Keep the local API/dashboard alive while capture can pause or stop.

    The actual OS sensors run only in `collector.secure_worker` while capture state
    is `recording`. Pausing/stopping terminates that child so Accessibility/Input
    Monitoring hooks are not left running. Resuming/start-new-run launches a fresh
    worker/session. Persist-time control checks in the worker and API provide a
    second boundary against races and queued evidence.
    """
    global _STOP
    _STOP = False
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    initialize_run(os.getenv("WORKFLOW_OBSERVER_RUN_STARTED_AT"))

    worker: subprocess.Popen | None = None
    last_generation = -1
    last_state = ""
    restart_not_before = 0.0

    try:
        while not _STOP:
            control = read_state()
            state = str(control.get("state") or "recording")
            generation = int(control.get("generation") or 1)

            if state == "recording":
                if worker is None or worker.poll() is not None or generation != last_generation:
                    if worker is not None and worker.poll() is None:
                        _terminate(worker)
                    now = time.monotonic()
                    if now >= restart_not_before:
                        worker = subprocess.Popen(
                            [sys.executable, "-m", "collector.secure_worker", "--config", str(config_path)],
                            cwd=Path(__file__).resolve().parents[1],
                            env=os.environ.copy(),
                        )
                        last_generation = generation
                        restart_not_before = now + 1.0
            else:
                if worker is not None and worker.poll() is None:
                    _terminate(worker)
                worker = None

            if state != last_state:
                print(f"OpenWorkGraph capture state: {state}")
                last_state = state
            time.sleep(0.2)
    finally:
        _terminate(worker)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Secure OpenWorkGraph capture supervisor")
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--config", default=str(root / "config.json"))
    args = parser.parse_args()
    raise SystemExit(run_supervisor(Path(args.config).resolve()))


if __name__ == "__main__":
    main()
