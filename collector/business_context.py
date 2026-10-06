from __future__ import annotations

"""Best-effort local business-object context for the foreground step.

No message/file/page contents are read or persisted. The collector may retain a
selected Outlook subject + sent time, a document path + SHA-256 fingerprint, and
the active browser hostname / allowlisted object reference.
"""

import hashlib
import os
import platform
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from shared.core.browser_utils import is_browser_app
from shared.core.resource_references import resource_reference_from_url
from server.local_reference_lookup import remember_file_reference

_CACHE_LOCK = threading.RLock()
_CACHE: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_FILE_HASH_CACHE: dict[tuple[str, int, int], str] = {}
_PRIVATE = re.compile(r"\b(?:incognito|inprivate|private browsing|private window)\b", re.I)


def _osascript(script: str, timeout: float = 1.0) -> str:
    try:
        result = subprocess.run(
            ["/usr/bin/osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def _mac_ax_document() -> str:
    return _osascript(
        """
tell application "System Events"
  try
    set p to first application process whose frontmost is true
    set w to front window of p
    return value of attribute "AXDocument" of w as text
  on error
    return ""
  end try
end tell
"""
    )


def _file_path_from_document(value: str) -> str:
    raw = str(value or "").strip()
    if raw.startswith("file://"):
        try:
            return unquote(urlsplit(raw).path)
        except Exception:
            return ""
    return raw if raw and os.path.isabs(raw) else ""


def _sha256_file(path: str, stat: os.stat_result) -> str:
    key = (path, int(stat.st_mtime_ns), int(stat.st_size))
    cached = _FILE_HASH_CACHE.get(key)
    if cached:
        return cached
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    value = digest.hexdigest()
    _FILE_HASH_CACHE.clear()
    _FILE_HASH_CACHE[key] = value
    return value


def _file_reference(path: str) -> dict[str, Any] | None:
    try:
        canonical = str(Path(path).expanduser().resolve(strict=True))
        if not Path(canonical).is_file():
            return None
        stat = os.stat(canonical)
        fingerprint = _sha256_file(canonical, stat)
    except Exception:
        return None
    ref = "owg:f:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    remember_file_reference(
        ref,
        path=canonical,
        sha256=fingerprint,
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
    )
    return {
        "file_ref": ref,
        "path": canonical,
        "sha256": fingerprint,
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "contents_stored": False,
    }


def _mac_outlook_selection() -> dict[str, Any] | None:
    raw = _osascript(
        """
tell application "Microsoft Outlook"
  try
    set msgs to selection
    if (count of msgs) is 0 then return ""
    set m to item 1 of msgs
    set s to subject of m as text
    set d to time sent of m as text
    return s & linefeed & d
  on error
    return ""
  end try
end tell
"""
    )
    if not raw:
        return None
    subject, _, sent = raw.partition("\n")
    subject = subject.strip()[:300]
    sent = sent.strip()[:120]
    if not subject:
        return None
    return {
        "kind": "outlook_message",
        "subject": subject,
        "sent_at": sent,
        "body_stored": False,
    }


def _windows_active_object(progid: str):
    try:
        import win32com.client  # type: ignore

        return win32com.client.GetActiveObject(progid)
    except Exception:
        return None


def _windows_outlook_selection() -> dict[str, Any] | None:
    app = _windows_active_object("Outlook.Application")
    if app is None:
        return None
    try:
        selection = app.ActiveExplorer().Selection
        if selection.Count < 1:
            return None
        item = selection.Item(1)
        subject = str(item.Subject or "").strip()[:300]
        sent = str(item.SentOn or "").strip()[:120]
        if not subject:
            return None
        return {
            "kind": "outlook_message",
            "subject": subject,
            "sent_at": sent,
            "body_stored": False,
        }
    except Exception:
        return None


def _windows_office_path(app: str) -> str:
    low = app.casefold()
    checks = []
    if "excel" in low:
        checks.append(("Excel.Application", lambda obj: obj.ActiveWorkbook.FullName))
    if "word" in low:
        checks.append(("Word.Application", lambda obj: obj.ActiveDocument.FullName))
    if "powerpoint" in low:
        checks.append(("PowerPoint.Application", lambda obj: obj.ActivePresentation.FullName))
    for progid, getter in checks:
        obj = _windows_active_object(progid)
        if obj is None:
            continue
        try:
            return str(getter(obj) or "")
        except Exception:
            pass
    return ""


def _mac_browser_url(app: str, title: str) -> str:
    if _PRIVATE.search(title or ""):
        return ""
    low = app.casefold()
    if "safari" in low:
        # Safari's AppleScript dictionary does not expose a private-browsing
        # property. Inspect the front Window menu instead and fail closed unless
        # we can positively identify a normal window. On localized/future Safari
        # versions where neither known menu item is present, native URL capture
        # is disabled rather than risking private-window capture.
        raw = _osascript(
            """
tell application "Safari"
  try
    if (count of windows) is 0 then return ""
    set frontURL to URL of current tab of front window as text
  on error
    return ""
  end try
end tell
tell application "System Events"
  try
    tell application process "Safari"
      set windowMenu to menu "Window" of menu bar 1
      if exists menu item "Move Tab to New Private Window" of windowMenu then
        return "__PRIVATE__"
      end if
      if exists menu item "Move Tab to New Window" of windowMenu then
        return "__NORMAL__" & linefeed & frontURL
      end if
      return "__UNKNOWN__"
    end tell
  on error
    return "__UNKNOWN__"
  end try
end tell
"""
        )
        if raw.startswith("__NORMAL__\n"):
            return raw.partition("\n")[2].strip()
        return ""
    names = {
        "chrome": "Google Chrome",
        "edge": "Microsoft Edge",
        "brave": "Brave Browser",
        "vivaldi": "Vivaldi",
        "opera": "Opera",
        "chromium": "Chromium",
        "arc": "Arc",
    }
    for token, name in names.items():
        if token not in low:
            continue
        return _osascript(
            f"""
tell application "{name}"
  try
    if (count of windows) is 0 then return ""
    if (mode of front window as text) is not "normal" then return ""
    return URL of active tab of front window
  on error
    return ""
  end try
end tell
"""
        )
    if any(token in low for token in ("firefox", "waterfox", "librewolf", "zen browser")):
        value = _mac_ax_document()
        return value if value.startswith(("http://", "https://")) else ""
    return ""


def _windows_browser_url(title: str) -> str:
    if _PRIVATE.search(title or ""):
        return ""
    try:
        import ctypes
        import uiautomation as auto  # type: ignore

        hwnd = ctypes.windll.user32.GetForegroundWindow()
        with auto.UIAutomationInitializerInThread():
            root = auto.ControlFromHandle(hwnd)
            if root is None:
                return ""
            # Check the UIAutomation window/control names as well as the collector
            # title so Chromium's Incognito/InPrivate indicator cannot be missed
            # merely because a title normalization layer changed the window title.
            try:
                if _PRIVATE.search(str(root.Name or "")):
                    return ""
            except Exception:
                return ""
            root_rect = root.BoundingRectangle
            candidates: list[tuple[float, str]] = []
            stack = [(root, 0)]
            while stack:
                control, depth = stack.pop()
                if depth > 7:
                    continue
                try:
                    if (
                        str(control.ControlTypeName or "") == "EditControl"
                        and not bool(control.IsPassword)
                    ):
                        value = str(control.GetValuePattern().Value or "").strip()
                        if value.startswith(("http://", "https://")):
                            rect = control.BoundingRectangle
                            relative = max(
                                0.0, float(rect.top) - float(root_rect.top)
                            )
                            candidates.append((relative, value))
                    for child in control.GetChildren() or []:
                        stack.append((child, depth + 1))
                except Exception:
                    continue
            return min(candidates, key=lambda pair: pair[0])[1] if candidates else ""
    except Exception:
        return ""


def _browser_context(app: str, title: str) -> dict[str, Any]:
    try:
        from server.browser_signal_settings import load_settings

        settings = load_settings()
    except Exception:
        settings = {"business_object_references": False}
    system = platform.system()
    if system == "Darwin":
        raw = _mac_browser_url(app, title)
    elif system == "Windows":
        raw = _windows_browser_url(title)
    else:
        raw = ""
    if not raw:
        return {}
    try:
        parsed = urlsplit(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return {}
        host = parsed.hostname.lower().rstrip(".")
    except Exception:
        return {}
    out: dict[str, Any] = {
        "page": {"hostname": host},
        "privacy": {
            "url_query": False,
            "url_fragment": False,
            "page_contents": False,
        },
        "browser_context_source": "native_active_url",
    }
    if settings.get("business_object_references"):
        reference = resource_reference_from_url(
            raw, include_locator=False, remember_locator=True
        )
        if reference:
            out["resource_reference"] = reference
    return out


def capture_business_context(app: str, title: str) -> dict[str, Any]:
    key = (str(app or ""), str(title or ""))
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached and now - cached[0] < 1.25:
            return dict(cached[1])

    low = key[0].casefold()
    result: dict[str, Any] = {}
    try:
        if is_browser_app(key[0]):
            result = _browser_context(*key)
        elif "outlook" in low:
            result["message_reference"] = (
                _mac_outlook_selection()
                if platform.system() == "Darwin"
                else _windows_outlook_selection()
            )
        if not result.get("message_reference"):
            if platform.system() == "Windows":
                path = _windows_office_path(key[0])
            else:
                path = _file_path_from_document(_mac_ax_document())
            if path:
                reference = _file_reference(path)
                if reference:
                    result["file_reference"] = reference
    except Exception:
        result = {}

    result = {
        key: value
        for key, value in result.items()
        if value not in (None, "", {}, [])
    }
    with _CACHE_LOCK:
        _CACHE.clear()
        _CACHE[key] = (now, result)
    return dict(result)


__all__ = ["capture_business_context"]
