from __future__ import annotations

"""Small Windows tray host for the signed/offline installer.

The tester ZIP keeps its console launcher. The signed installer starts this module
with its embedded pythonw.exe so end users do not need PowerShell or a first-run
runtime download.
"""

import atexit
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Any

from PIL import Image, ImageDraw
import pystray

ROOT = Path(__file__).resolve().parent
_CHILD: subprocess.Popen[Any] | None = None
_ICON: pystray.Icon | None = None
_LOCK = threading.RLock()


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


def _start_child() -> None:
    global _CHILD
    with _LOCK:
        if _CHILD is not None and _CHILD.poll() is None:
            return
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        _CHILD = subprocess.Popen(
            [sys.executable, str(ROOT / "start.py"), "--mode", "observe"],
            cwd=str(ROOT),
            creationflags=flags,
        )
        child = _CHILD

    def watch() -> None:
        try:
            child.wait()
        finally:
            icon = _ICON
            if icon is not None:
                try:
                    icon.stop()
                except Exception:
                    pass

    threading.Thread(target=watch, name="owg-tray-watch", daemon=True).start()


def _restart(_icon=None, _item=None) -> None:
    _stop_child()
    time.sleep(0.4)
    _start_child()


def _quit(icon=None, _item=None) -> None:
    _stop_child()
    target = icon or _ICON
    if target is not None:
        target.stop()


def main() -> int:
    global _ICON
    if os.name != "nt":
        raise SystemExit("windows_tray.py is Windows-only")
    _start_child()
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
