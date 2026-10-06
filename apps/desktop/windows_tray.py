from __future__ import annotations

"""Small Windows tray host for the signed/offline installer.

The tester ZIP keeps its console launcher. The signed installer starts this module
with its embedded pythonw.exe so end users do not need PowerShell or a first-run
runtime download.
"""

import atexit
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from typing import Any

from collections import deque

from PIL import Image, ImageDraw
import pystray

PAYLOAD_ROOT = Path(__file__).resolve().parent
ROOT = PAYLOAD_ROOT
_CHILD: subprocess.Popen[Any] | None = None
_ICON: pystray.Icon | None = None
_LOCK = threading.RLock()
_SHUTTING_DOWN = threading.Event()
_RESTART_TIMES: deque[float] = deque()
_RESTART_WINDOW_SECONDS = 5 * 60
_MAX_CRASH_RESTARTS = 5
_RESTART_DELAYS = (1.0, 2.0, 4.0, 8.0, 15.0)


def _machine_runtime_root() -> Path:
    base = Path(os.getenv("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    return base / "OpenWorkGraph" / "Runtime"


def _prepare_machine_runtime() -> Path:
    """Mirror immutable enterprise payload into a user-writable runtime.

    Machine-wide installs live under Program Files, but OpenWorkGraph's config,
    browser pairing bundle and evidence are per user. Keep the embedded Python
    sealed in the machine installation while running source from LocalAppData,
    matching the existing macOS sealed-runtime/user-state design.
    """
    target = _machine_runtime_root()
    target.mkdir(parents=True, exist_ok=True)
    preserve = {"data", "config.json"}
    for entry in list(target.iterdir()):
        if entry.name in preserve:
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            try:
                entry.unlink()
            except FileNotFoundError:
                pass
    for source in PAYLOAD_ROOT.iterdir():
        if source.name in {".runtime", "data", "config.json", ".venv", ".pytest_cache", "__pycache__"}:
            continue
        destination = target / source.name
        if source.is_dir():
            shutil.copytree(source, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(source, destination)
    return target


def _configure_runtime_root() -> Path:
    global ROOT
    if "--machine" in sys.argv[1:]:
        ROOT = _prepare_machine_runtime()
    else:
        ROOT = PAYLOAD_ROOT
    return ROOT


def _image() -> Image.Image:
    image = Image.new("RGBA", (64, 64), (28, 36, 32, 255))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((11, 11, 53, 53), radius=12, fill=(99, 193, 137, 255))
    draw.rectangle((21, 22, 29, 42), fill=(255, 255, 255, 255))
    draw.rectangle((35, 22, 43, 42), fill=(255, 255, 255, 255))
    return image


def _stop_child() -> None:
    global _CHILD
    with _LOCK:
        child = _CHILD
        _CHILD = None
    if child is None or child.poll() is not None:
        return
    try:
        subprocess.run(
            ["taskkill.exe", "/PID", str(child.pid), "/T", "/F"],
            capture_output=True,
            timeout=8,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        try:
            child.terminate()
        except Exception:
            pass


def _restart_delay_after_crash(now: float | None = None) -> float | None:
    current = time.monotonic() if now is None else now
    cutoff = current - _RESTART_WINDOW_SECONDS
    with _LOCK:
        while _RESTART_TIMES and _RESTART_TIMES[0] < cutoff:
            _RESTART_TIMES.popleft()
        _RESTART_TIMES.append(current)
        if len(_RESTART_TIMES) > _MAX_CRASH_RESTARTS:
            return None
        return _RESTART_DELAYS[min(len(_RESTART_TIMES) - 1, len(_RESTART_DELAYS) - 1)]


def _start_child(*, open_dashboard: bool) -> None:
    global _CHILD
    if _SHUTTING_DOWN.is_set():
        return

    with _LOCK:
        if _CHILD is not None and _CHILD.poll() is None:
            return
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        command = [sys.executable, str(ROOT / "start.py"), "--mode", "observe"]
        if not open_dashboard:
            command.append("--no-open-dashboard")
        _CHILD = subprocess.Popen(
            command,
            cwd=str(ROOT),
            creationflags=flags,
        )
        child = _CHILD

    def watch() -> None:
        global _CHILD
        child.wait()

        # _stop_child clears _CHILD before terminating. If this process is no
        # longer the current child, its exit was intentional (Quit/Restart) or
        # it has already been superseded and must never resurrect itself.
        with _LOCK:
            if _SHUTTING_DOWN.is_set() or _CHILD is not child:
                return

        delay = _restart_delay_after_crash()
        if delay is None:
            with _LOCK:
                if _CHILD is child:
                    _CHILD = None
            icon = _ICON
            if icon is not None:
                try:
                    icon.notify(
                        "OpenWorkGraph stopped repeatedly. Automatic restart is paused; use Restart from the tray menu.",
                        "OpenWorkGraph",
                    )
                except Exception:
                    pass
            return

        with _LOCK:
            if _CHILD is child:
                _CHILD = None

        if _SHUTTING_DOWN.wait(delay):
            return
        _start_child(open_dashboard=False)

    threading.Thread(target=watch, name="owg-tray-watch", daemon=True).start()


def _restart(_icon=None, _item=None) -> None:
    with _LOCK:
        _RESTART_TIMES.clear()
    _stop_child()
    time.sleep(0.4)
    _start_child(open_dashboard=True)


def _quit(icon=None, _item=None) -> None:
    _SHUTTING_DOWN.set()
    _stop_child()
    target = icon or _ICON
    if target is not None:
        target.stop()


def main() -> int:
    global _ICON
    if os.name != "nt":
        raise SystemExit("windows_tray.py is Windows-only")
    _SHUTTING_DOWN.clear()
    _configure_runtime_root()
    launched_in_background = "--background" in sys.argv[1:]
    _start_child(open_dashboard=not launched_in_background)
    menu = pystray.Menu(
        pystray.MenuItem(
            "Restart and open dashboard",
            _restart,
            default=True,
        ),
        pystray.MenuItem("Quit OpenWorkGraph", _quit),
    )
    _ICON = pystray.Icon(
        "OpenWorkGraph",
        _image(),
        "OpenWorkGraph",
        menu,
    )
    _ICON.run()
    return 0


atexit.register(_stop_child)


if __name__ == "__main__":
    raise SystemExit(main())
