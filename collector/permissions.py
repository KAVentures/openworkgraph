from __future__ import annotations

"""Truthful OS permission state for the desktop sensors.

A pynput listener thread starts even when macOS withholds input events, so
"listener started" is not evidence that OpenWorkGraph can see anything. These
preflight checks ask the OS directly and never trigger a permission prompt.

Values: ``True`` granted, ``False`` denied, ``None`` unknown/not applicable.
"""

import ctypes
import ctypes.util
import platform

# IOKit: kIOHIDRequestTypeListenEvent and IOHIDAccessType values.
_LISTEN_EVENT = 1
_GRANTED, _DENIED = 0, 1


def _macos_accessibility() -> bool | None:
    try:
        import ApplicationServices  # type: ignore

        return bool(ApplicationServices.AXIsProcessTrusted())
    except Exception:
        return None


def _macos_input_monitoring() -> bool | None:
    try:
        path = ctypes.util.find_library("IOKit")
        if not path:
            return None
        iokit = ctypes.cdll.LoadLibrary(path)
        check = iokit.IOHIDCheckAccess  # macOS 10.15+
        check.argtypes = [ctypes.c_uint32]
        check.restype = ctypes.c_uint32
        value = int(check(_LISTEN_EVENT))
    except Exception:
        return None
    if value == _GRANTED:
        return True
    if value == _DENIED:
        return False
    return None  # not determined yet


def sensor_permissions() -> dict[str, bool | None]:
    """Permission state that decides what the desktop sensors can actually see.

    * accessibility: window titles and UI element labels on click (and, on
      older macOS versions, input events).
    * input_monitoring: keyboard activity counts and copy/cut/paste shortcuts.
    """
    if platform.system() != "Darwin":
        return {}
    return {
        "accessibility": _macos_accessibility(),
        "input_monitoring": _macos_input_monitoring(),
    }


def missing(permissions: dict[str, bool | None]) -> list[str]:
    return sorted(name for name, granted in permissions.items() if granted is False)
