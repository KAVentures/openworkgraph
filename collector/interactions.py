from __future__ import annotations

import ctypes
import re
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
    # Filled by the collector callback at interaction time before worker queuing.
    # Contains only already privacy-filtered app/window state, never typed content.
    context: dict[str, Any] | None = None
    # Sampled pointer positions for kind "pointer_trajectory": [ms since first, x, y].
    points: list[list[float]] | None = None
    # For kind "hover_dwell": how long the pointer rested and how the rest ended.
    duration_ms: int | None = None
    ended_by: str | None = None


class HoverTracker:
    """Report a pointer that rested in one place for at least ``threshold_ms`` (#634).

    Movement within ``tolerance`` pixels counts as resting. The rest is reported when
    the pointer moves away (``pointer_left``) or a button is pressed (``activated``);
    whether it rested on an interactive control is decided later from the UI target.
    """

    def __init__(self, *, threshold_ms: int = 750, tolerance: float = 3.0) -> None:
        self.threshold_ms = int(threshold_ms)
        self.tolerance = float(tolerance)
        self._rest: tuple[float, float, float] | None = None  # x, y, since
        self._reported = False

    def _finish(self, now: float, ended_by: str) -> RawInteraction | None:
        rest, self._rest = self._rest, None
        if rest is None or self._reported:
            self._reported = False
            return None
        x, y, since = rest
        duration = int(round((now - since) * 1000 / 50.0)) * 50
        if duration < self.threshold_ms:
            return None
        return RawInteraction(kind="hover_dwell", x=x, y=y, occurred_mono=since,
                              duration_ms=min(duration, 600000), ended_by=ended_by)

    def move(self, x: float, y: float, now: float) -> RawInteraction | None:
        if self._rest is not None:
            rx, ry, _ = self._rest
            if ((x - rx) ** 2 + (y - ry) ** 2) ** 0.5 <= self.tolerance:
                return None
        finished = self._finish(now, "pointer_left")
        self._rest = (float(x), float(y), float(now))
        self._reported = False
        return finished

    def activate(self, now: float) -> RawInteraction | None:
        finished = self._finish(now, "activated")
        return finished


_INTERACTIVE_ROLE = re.compile(r"button|link|menu|tab|check|radio|combo|popup|slider|switch|option|hyperlink", re.I)


def interactive_target(target: dict[str, Any] | None) -> bool:
    """A hover counts only on a non-secure interactive control."""
    if not isinstance(target, dict) or target.get("secure"):
        return False
    return bool(_INTERACTIVE_ROLE.search(str(target.get("role") or "")))


class DragDetector:
    """Turn a mouse press and release into a drag when it moved far and long enough (#634).

    Pure and clock-injected so it is testable without an OS hook. A plain click is
    never reported as a drag; the click itself is still reported by the sensor.
    """

    def __init__(self, *, min_distance: float = 16.0, min_seconds: float = 0.15) -> None:
        self.min_distance = float(min_distance)
        self.min_seconds = float(min_seconds)
        self._start: tuple[float, float, float, str] | None = None

    def press(self, x: float, y: float, button: str, now: float) -> None:
        self._start = (float(x), float(y), float(now), button)

    def release(self, x: float, y: float, now: float) -> RawInteraction | None:
        start, self._start = self._start, None
        if start is None:
            return None
        sx, sy, at, button = start
        dx, dy = float(x) - sx, float(y) - sy
        if (dx * dx + dy * dy) ** 0.5 < self.min_distance or now - at < self.min_seconds:
            return None
        return RawInteraction(kind="drag", x=sx, y=sy, dx=dx, dy=dy, button=button, occurred_mono=at)


class PointerSampler:
    """Throttle pointer moves to ``hz`` samples a second and batch them (#634, Full fidelity)."""

    def __init__(self, *, hz: int = 4, max_points: int = 200, max_seconds: float = 10.0) -> None:
        self.hz = max(1, min(int(hz), 20))
        self.max_points = max_points
        self.max_seconds = max_seconds
        self._points: list[list[float]] = []
        self._first = 0.0
        self._last = -1.0

    def move(self, x: float, y: float, now: float) -> RawInteraction | None:
        if self._last >= 0 and now - self._last < 1.0 / self.hz:
            return None
        if not self._points:
            self._first = now
        self._last = now
        self._points.append([int(round((now - self._first) * 1000)), int(round(x)), int(round(y))])
        if len(self._points) >= self.max_points or now - self._first >= self.max_seconds:
            return self.flush()
        return None

    def flush(self) -> RawInteraction | None:
        points, self._points = self._points, []
        self._last = -1.0
        if not points:
            return None
        return RawInteraction(kind="pointer_trajectory", x=float(points[0][1]), y=float(points[0][2]),
                              occurred_mono=self._first, points=points)


def capture_depth_signals(data_dir) -> dict[str, Any]:
    """The locally chosen capture depth (written by the dashboard); Standard when unreadable."""
    import json
    from pathlib import Path
    try:
        data = json.loads((Path(data_dir) / "capture_depth.json").read_text(encoding="utf-8"))
        signals = data.get("signals") if isinstance(data, dict) else {}
        params = data.get("parameters") if isinstance(data, dict) else {}
        acknowledged = data.get("full_acknowledged_version") == "capture-depth-full-v1"
    except (OSError, ValueError):
        return {"drag_drop": False, "pointer_trajectory": False, "trajectory_hz": 4, "hover_dwell": False,
                "hover_dwell_ms": 750}
    hz = params.get("trajectory_hz") if isinstance(params, dict) else None
    dwell = params.get("hover_dwell_ms") if isinstance(params, dict) else None
    return {"hover_dwell": isinstance(signals, dict) and signals.get("hover_dwell") is True,
            "hover_dwell_ms": dwell if isinstance(dwell, int) and 300 <= dwell <= 5000 else 750,
            "drag_drop": isinstance(signals, dict) and signals.get("drag_drop") is True,
            "pointer_trajectory": isinstance(signals, dict) and signals.get("pointer_trajectory") is True and acknowledged,
            "trajectory_hz": hz if isinstance(hz, int) and 1 <= hz <= 20 else 4}


@dataclass
class RawClipboardAction:
    """Privacy-safe clipboard behavior signal.

    Only the action kind and an optional OS clipboard change token are retained.
    Clipboard contents, selected text and arbitrary key identities are never read.
    """

    kind: str
    occurred_mono: float = 0.0
    clipboard_change_token: int | None = None
    context: dict[str, Any] | None = None


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

    def last_input_mono(self) -> float | None:
        with self._lock:
            return self._events[-1][0] if self._events else None

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

            char = getattr(key, "char", None)
            action = classify_clipboard_shortcut(
                char,
                command_down=self._command_down,
                control_down=self._control_down,
            )
            if action is None:
                return

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

        self._listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        self._listener.daemon = True
        self._listener.start()
        return True

    def stop(self) -> None:
        try:
            if self._listener is not None:
                self._listener.stop()
        except Exception:
            pass


class InteractionSensor:
    def __init__(
        self,
        callback: Callable[[RawInteraction], Any],
        *,
        capture_clicks: bool = True,
        capture_scrolls: bool = True,
        scroll_min_interval_seconds: float = 1.25,
        depth: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self.callback = callback
        self.capture_clicks = capture_clicks
        self.capture_scrolls = capture_scrolls
        # Capture depth (#634) is read live, so changing it needs no restart.
        self.depth = depth or (lambda: {"drag_drop": False, "pointer_trajectory": False, "trajectory_hz": 4})
        self._drag = DragDetector()
        self._sampler = PointerSampler()
        self._hover = HoverTracker()
        self.scroll_min_interval_seconds = max(0.0, float(scroll_min_interval_seconds))
        self._mouse_listener = None
        self._last_scroll_at = 0.0

    def start(self) -> bool:
        try:
            from pynput import mouse
        except Exception:
            return False

        def on_click(x, y, button, pressed):
            now = time.monotonic()
            settings = self.depth()
            if pressed and settings.get("hover_dwell"):
                dwell = self._hover.activate(now)
                if dwell is not None:
                    self.callback(dwell)
            if settings.get("drag_drop"):
                if pressed:
                    self._drag.press(x, y, getattr(button, "name", str(button)), now)
                else:
                    drag = self._drag.release(x, y, now)
                    if drag is not None:
                        self.callback(drag)
            if not pressed or not self.capture_clicks:
                return
            self.callback(
                RawInteraction(
                    kind="click",
                    x=float(x),
                    y=float(y),
                    button=getattr(button, "name", str(button)),
                    occurred_mono=time.monotonic(),
                )
            )

        def on_scroll(x, y, dx, dy):
            if not self.capture_scrolls:
                return
            now = time.monotonic()
            if now - self._last_scroll_at < self.scroll_min_interval_seconds:
                return
            self._last_scroll_at = now
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

        def on_move(x, y):
            settings = self.depth()
            if settings.get("hover_dwell"):
                self._hover.threshold_ms = settings.get("hover_dwell_ms", 750)
                dwell = self._hover.move(x, y, time.monotonic())
                if dwell is not None:
                    self.callback(dwell)
            if not settings.get("pointer_trajectory"):
                self._sampler.flush()  # discard: the signal was turned off
                return
            self._sampler.hz = settings.get("trajectory_hz", 4)
            batch = self._sampler.move(x, y, time.monotonic())
            if batch is not None:
                self.callback(batch)

        self._mouse_listener = mouse.Listener(on_click=on_click, on_scroll=on_scroll, on_move=on_move)
        self._mouse_listener.daemon = True
        self._mouse_listener.start()
        return True

    def stop(self) -> None:
        try:
            if self._mouse_listener is not None:
                self._mouse_listener.stop()
        except Exception:
            pass
