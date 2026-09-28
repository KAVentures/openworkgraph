from __future__ import annotations

import ctypes
import platform as _platform
import subprocess
from dataclasses import dataclass

@dataclass
class ActiveWindow:
    app: str
    title: str


def _macos_active_window() -> ActiveWindow:
    script = r'''
    tell application "System Events"
      set frontApp to first application process whose frontmost is true
      set appName to name of frontApp
      try
        set winTitle to name of front window of frontApp
      on error
        set winTitle to ""
      end try
      return appName & "\n" & winTitle
    end tell
    '''
    try:
        out = subprocess.check_output(["osascript", "-e", script], text=True, stderr=subprocess.DEVNULL, timeout=2)
        parts = out.rstrip("\n").split("\n", 1)
        return ActiveWindow(parts[0] if parts else "Unknown", parts[1] if len(parts) > 1 else "")
    except Exception:
        return ActiveWindow("Unknown", "")


def _windows_active_window() -> ActiveWindow:
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        app = f"pid:{pid.value}"
        try:
            import psutil  # optional
            app = psutil.Process(pid.value).name()
        except Exception:
            pass
        return ActiveWindow(app, title)
    except Exception:
        return ActiveWindow("Unknown", "")


def _linux_active_window() -> ActiveWindow:
    try:
        wid = subprocess.check_output(["xdotool", "getactivewindow"], text=True, timeout=1).strip()
        title = subprocess.check_output(["xdotool", "getwindowname", wid], text=True, timeout=1).strip()
        pid = subprocess.check_output(["xdotool", "getwindowpid", wid], text=True, timeout=1).strip()
        return ActiveWindow(f"pid:{pid}", title)
    except Exception:
        return ActiveWindow("Unknown", "")


def active_window() -> ActiveWindow:
    system = _platform.system()
    if system == "Darwin":
        return _macos_active_window()
    if system == "Windows":
        return _windows_active_window()
    return _linux_active_window()


def _macos_idle_seconds() -> float | None:
    try:
        import Quartz  # type: ignore

        value = Quartz.CGEventSourceSecondsSinceLastEventType(
            Quartz.kCGEventSourceStateCombinedSessionState, Quartz.kCGAnyInputEventType
        )
        return max(0.0, float(value))
    except Exception:
        return None


def _windows_idle_seconds() -> float | None:
    try:
        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        now = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
        return max(0.0, ((now - info.dwTime) & 0xFFFFFFFF) / 1000.0)
    except Exception:
        return None


def system_idle_seconds() -> float | None:
    """Seconds since the last keyboard/mouse input anywhere in the session.

    Read from the OS input clock, so it needs no Input Monitoring permission and
    never sees which keys were pressed. ``None`` when the platform cannot say
    (Linux for now); callers must then not guess that the user is away.
    """
    system = _platform.system()
    if system == "Darwin":
        return _macos_idle_seconds()
    if system == "Windows":
        return _windows_idle_seconds()
    return None


def screen_locked() -> bool | None:
    """True while the session is locked or switched away from the console."""
    if _platform.system() != "Darwin":
        return None
    try:
        import Quartz  # type: ignore

        session = Quartz.CGSessionCopyCurrentDictionary()
        if not session:
            return None
        if bool(session.get("CGSSessionScreenIsLocked", False)):
            return True
        on_console = session.get("kCGSSessionOnConsoleKey")
        return False if on_console is None else not bool(on_console)
    except Exception:
        return None
