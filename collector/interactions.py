from __future__ import annotations

import ctypes
import platform
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Any


@dataclass
class RawInteraction:
    kind: str
    x: float
    y: float
    button: str | None = None
    dx: float | None = None
    dy: float | None = None
    occurred_mono: float = 0.0


@dataclass
class RawClipboardAction:
    """Privacy-safe clipboard behavior signal.

    Only the action kind and an optional OS clipboard change token are retained.
    Clipboard contents, selected text and arbitrary key identities are never read.
    """

    kind: str
    occurred_mono: float = 0.0
    clipboard_change_token: int | None = None


def clipboard_change_token() -> int | None:
    """Return an OS clipboard sequence/change counter without reading contents."""

    system = platform.system()
    if system == "Darwin":
        try:
            from AppKit import NSPasteboard

            return int(NSPasteboard.generalPasteboard().changeCount())
        except Exception:
            return None
    if system == "Windows":
        try:
            return int(ctypes.windll.user32.GetClipboardSequenceNumber())
        except Exception:
            return None
    return None


def classify_clipboard_shortcut(
    key_char: str | None,
    *,
    command_down: bool,
    control_down: bool,
    platform_name: str | None = None,
) -> str | None:
    """Classify only copy/cut/paste shortcuts; ignore every other key identity.

    macOS uses Command+C/X/V. Windows uses Control+C/X/V. The helper is pure so
    behavior can be regression-tested without installing an OS keyboard hook.
    """

    char = str(key_char or "").lower()
    if char not in {"c", "x", "v"}:
        return None
    system = platform_name or platform.system()
    modifier_down = command_down if system == "Darwin" else control_down
    if not modifier_down:
        return None
    return {"c": "copy", "x": "cut", "v": "paste"}[char]


class ActivityTracker:
    """In-memory input timing tracker with no key identities or typed values.

    Only coarse activity kinds (key/click/scroll) and monotonic timestamps are kept
    in memory. Focus-span summaries persist aggregate counts/timing only.
    """

    def __init__(self, *, max_age_seconds: float = 6 * 3600) -> None:
        self._events: deque[tuple[float, str]] = deque()
        self._lock = threading.Lock()
        self._max_age = max(300.0, float(max_age_seconds))

    def record(self, kind: str, occurred_mono: float | None = None) -> None:
        now = float(occurred_mono if occurred_mono is not None else time.monotonic())
        with self._lock:
            self._events.append((now, str(kind)))
            cutoff = now - self._max_age
            while self._events and self._events[0][0] < cutoff:
                self._events.popleft()

    def summarize(
        self,
        start_mono: float,
        end_mono: float,
        *,
        active_window_seconds: float = 5.0,
        engaged_grace_seconds: float = 60.0,
    ) -> dict[str, float | int]:
        start = float(start_mono)
        end = max(start, float(end_mono))
        duration = max(0.0, end - start)
        with self._lock:
            events = [(t, k) for (t, k) in self._events if start <= t <= end]

        counts = {"key": 0, "click": 0, "scroll": 0}
        for _t, kind in events:
            if kind in counts:
                counts[kind] += 1

        def union_seconds(window: float) -> float:
            if not events or duration <= 0:
                return 0.0
            intervals: list[tuple[float, float]] = []
            for t, _kind in events:
                a = max(start, t)
                b = min(end, t + max(0.0, window))
                if b > a:
                    intervals.append((a, b))
            if not intervals:
                return 0.0
            intervals.sort()
            total = 0.0
            cur_a, cur_b = intervals[0]
            for a, b in intervals[1:]:
                if a <= cur_b:
                    cur_b = max(cur_b, b)
                else:
                    total += cur_b - cur_a
                    cur_a, cur_b = a, b
            total += cur_b - cur_a
            return min(duration, total)

        active = union_seconds(active_window_seconds)
        engaged = union_seconds(engaged_grace_seconds)
        idle = max(0.0, duration - engaged)
        return {
            "keypress_count": counts["key"],
            "click_count": counts["click"],
            "scroll_count": counts["scroll"],
            "input_events": sum(counts.values()),
            "active_input_seconds": round(active, 3),
            "engaged_seconds": round(engaged, 3),
            "idle_seconds": round(idle, 3),
        }


class KeyboardActivitySensor:
    """Global keyboard activity counter with optional safe clipboard shortcuts.

    Ordinary key identities are discarded immediately. When clipboard behavior
    capture is enabled, the sensor recognizes only Command/Control+C/X/V and emits
    copy/cut/paste actions without reading clipboard contents or selected text.
    """

    def __init__(
        self,
        callback: Callable[[float], Any],
        *,
        clipboard_callback: Callable[[RawClipboardAction], Any] | None = None,
        capture_clipboard_shortcuts: bool = False,
    ) -> None:
        self.callback = callback
        self.clipboard_callback = clipboard_callback
        self.capture_clipboard_shortcuts = bool(capture_clipboard_shortcuts)
        self._listener = None
        self._command_down = False
        self._control_down = False
        self._last_clipboard_action: tuple[str, float] | None = None

    def start(self) -> bool:
        try:
            from pynput import keyboard
        except Exception:
            return False

        command_keys = {
            value
            for value in (
                getattr(keyboard.Key, "cmd", None),
                getattr(keyboard.Key, "cmd_l", None),
                getattr(keyboard.Key, "cmd_r", None),
            )
            if value is not None
        }
        control_keys = {
            value
            for value in (
                getattr(keyboard.Key, "ctrl", None),
                getattr(keyboard.Key, "ctrl_l", None),
                getattr(keyboard.Key, "ctrl_r", None),
            )
            if value is not None
        }

        def on_press(key):
            now = time.monotonic()
            try:
                self.callback(now)
            except Exception:
                pass

            if not self.capture_clipboard_shortcuts or self.clipboard_callback is None:
                return

            try:
                if key in command_keys:
                    self._command_down = True
                    return
                if key in control_keys:
                    self._control_down = True
                    return
            except Exception:
                pass

            # Read only KeyCode.char for C/X/V classification, then discard it.
            char = getattr(key, "char", None)
            action = classify_clipboard_shortcut(
                char,
                command_down=self._command_down,
                control_down=self._control_down,
            )
            if action is None:
                return

            # pynput can repeat a held key. Avoid duplicate behavior rows while
            # preserving legitimately repeated copy/paste operations.
            if self._last_clipboard_action is not None:
                last_action, last_at = self._last_clipboard_action
                if action == last_action and now - last_at < 0.2:
                    return
            self._last_clipboard_action = (action, now)
            try:
                self.clipboard_callback(
                    RawClipboardAction(
                        kind=action,
                        occurred_mono=now,
                        clipboard_change_token=clipboard_change_token(),
                    )
                )
            except Exception:
                pass

        def on_release(key):
            if not self.capture_clipboard_shortcuts:
                return
            try:
                if key in command_keys:
                    self._command_down = False
                if key in control_keys:
                    self._control_down = False
            except Exception:
                pass

        try:
            self._listener = keyboard.Listener(on_press=on_press, on_release=on_release)
            self._listener.start()
            return True
        except Exception:
            self._listener = None
            return False

    def stop(self) -> None:
        try:
            if self._listener is not None:
                self._listener.stop()
        except Exception:
            pass


class InteractionSensor:
    """Global mouse interaction sensor.

    Mouse movement is ignored. Scroll events are throttled so ordinary scrolling
    does not create thousands of rows.
    """

    def __init__(
        self,
        callback: Callable[[RawInteraction], Any],
        *,
        capture_clicks: bool = True,
        capture_scrolls: bool = True,
        scroll_min_interval_seconds: float = 1.25,
    ) -> None:
        self.callback = callback
        self.capture_clicks = capture_clicks
        self.capture_scrolls = capture_scrolls
        self.scroll_min_interval_seconds = max(0.25, float(scroll_min_interval_seconds))
        self._listener = None
        self._last_scroll = 0.0

    def start(self) -> bool:
        try:
            from pynput import mouse
        except Exception:
            return False

        def on_click(x, y, button, pressed, *extra):
            if not self.capture_clicks or not pressed:
                return
            injected = bool(extra[0]) if extra else False
            if injected:
                return
            try:
                name = getattr(button, "name", None) or str(button).split(".")[-1]
                self.callback(
                    RawInteraction(
                        kind="click",
                        x=float(x),
                        y=float(y),
                        button=str(name),
                        occurred_mono=time.monotonic(),
                    )
                )
            except Exception:
                pass

        def on_scroll(x, y, dx, dy, *extra):
            if not self.capture_scrolls:
                return
            injected = bool(extra[0]) if extra else False
            if injected:
                return
            now = time.monotonic()
            if now - self._last_scroll < self.scroll_min_interval_seconds:
                return
            self._last_scroll = now
            try:
                self.callback(
                    RawInteraction(
                        kind="scroll",
                        x=float(x),
                        y=float(y),
                        dx=float(dx),
                        dy=float(dy),
                        occurred_mono=now,
                    )
                )
            except Exception:
                pass

        try:
            self._listener = mouse.Listener(on_click=on_click, on_scroll=on_scroll)
            self._listener.start()
            return True
        except Exception:
            self._listener = None
            return False

    def stop(self) -> None:
        try:
            if self._listener is not None:
                self._listener.stop()
        except Exception:
            pass
