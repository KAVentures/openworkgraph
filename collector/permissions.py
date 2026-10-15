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


def request_accessibility_permission() -> bool | None:
    """Ask the actual macOS TCC service; no Apple Events required."""
    if platform.system() != "Darwin":
        return None
    try:
        import ApplicationServices  # type: ignore
        return bool(ApplicationServices.AXIsProcessTrustedWithOptions({
            "AXTrustedCheckOptionPrompt": True,
        }))
    except Exception:
        return _macos_accessibility()


def request_input_monitoring_permission() -> bool | None:
    if platform.system() != "Darwin":
        return None
    try:
        path = ctypes.util.find_library("IOKit")
        if not path:
            return None
        iokit = ctypes.cdll.LoadLibrary(path)
        request = iokit.IOHIDRequestAccess
        request.argtypes = [ctypes.c_uint32]
        request.restype = ctypes.c_uint32
        request(_LISTEN_EVENT)
    except Exception:
        return None
    return _macos_input_monitoring()


def main() -> int:
    """Small command used by the signed Mac launcher for a real OS prompt."""
    import json
    import sys
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    if action == "request-accessibility":
        request_accessibility_permission()
    elif action == "request-input-monitoring":
        request_input_monitoring_permission()
    elif action != "status":
        return 2
    print(json.dumps(sensor_permissions(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
