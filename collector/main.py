from __future__ import annotations

import argparse
import json
import os
import signal
import time
import uuid
import queue
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from .platform import active_window, screen_locked, system_idle_seconds
from .accessibility import element_at_position
from .interactions import (
    InteractionSensor,
    KeyboardActivitySensor,
    ActivityTracker,
    RawInteraction,
    RawClipboardAction,
    clipboard_change_token,
)
from .privacy import should_exclude, title_for_mode
from .identity import load_or_create_identity
from .outbox import EventOutbox
from .boundaries import document_key, is_material_change
from .business_context import capture_business_context
from .instance_lock import EXIT_ALREADY_RUNNING, CollectorLock
from .permissions import missing as missing_permissions, sensor_permissions
from shared.core.browser_utils import is_browser_app, normalized_browser_title
from shared.core.sensitive_identifiers import sanitize_event_identifiers

ROOT = Path(__file__).resolve().parents[1]
LOCAL_DIR = Path(os.getenv("WORKFLOW_OBSERVER_DATA", ROOT / "data"))
LOCAL_DIR.mkdir(parents=True, exist_ok=True)
S_SCREEN = LOCAL_DIR / "screenshots"
S_SCREEN.mkdir(parents=True, exist_ok=True)
MAX_JSONL_BYTES = 32 * 1024 * 1024
STOP = False


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _occurred_at(occurred_mono: float) -> str:
    """Wall time of an input that happened at ``occurred_mono``.

    Interactions are persisted by a worker thread; stamping them when processed
    would shift them by however long the queue took.
    """
    if not occurred_mono:
        return utcnow()
    lag = max(0.0, time.monotonic() - float(occurred_mono))
    return (datetime.now(timezone.utc) - timedelta(seconds=lag)).isoformat()


def load_config(path: Path) -> dict:
    cfg = {
        "device_id": "",
        "organization_id": "",
        "actor_id": "",
        "backend_url": "http://127.0.0.1:8787",
        "poll_seconds": 2,
        "heartbeat_seconds": 5,
        "focus_checkpoint_seconds": 120,
        "capture_gap_seconds": 30,
        # "application_and_document": app changes plus debounced, noise-normalized
        # document changes within the same app. "application_only": app changes
        # only (titles of long spans go stale). "application_and_title": every raw
        # title change. "application" was the shipped default copied into every
        # config.json, so it follows the default rather than pinning old behavior.
        "change_detection": "application_and_document",
        "document_debounce_polls": 2,
        # A span ends after this long without any keyboard/mouse input anywhere,
        # or when the screen locks; the time until input returns is an away span.
        "away_detection_enabled": True,
        "away_after_seconds": 300,
        "screenshot_interval_seconds": 20,
        "screenshots_enabled": False,
        "upload_screenshots": False,
        "excluded_apps": ["1Password", "Bitwarden", "KeePass", "Keychain Access"],
        "excluded_title_patterns": ["password", "private", "incognito", "bank"],
        "window_title_mode": "full",
        "screen_interactions_enabled": True,
        "capture_clicks": True,
        "capture_scrolls": True,
        "scroll_min_interval_seconds": 1.25,
        "interaction_screenshots_enabled": False,
        "capture_ui_labels": True,
        "keyboard_activity_enabled": True,
        "clipboard_behavior_enabled": True,
        "clipboard_link_max_seconds": 7200,
        # Notice clipboard writes that no copy/cut shortcut explains (menu,
        # right-click, drag, apps) from the OS change counter; contents never read.
        "clipboard_write_detection_enabled": True,
        "activity_active_window_seconds": 5,
        "engaged_grace_seconds": 60,
    }
    if path.exists():
        cfg.update(json.loads(path.read_text(encoding="utf-8")))
    return cfg



_LIVE_EXCLUSION_KEYS = ("excluded_apps", "excluded_title_patterns")


def refresh_live_exclusions(config_path: Path, cfg: dict, cache: dict) -> bool:
    """Refresh only the privacy exclusions that are safe to change while running.

    The collector intentionally keeps the rest of its configuration stable for a
    run.  The Privacy tab, however, promises that newly excluded apps/title
    patterns take effect before new evidence is persisted.  A cheap stat check is
    performed on each foreground poll and interaction snapshot; the file is read
    only when it changed.
    """
    try:
        stat = config_path.stat()
        stamp = (int(stat.st_mtime_ns), int(stat.st_size))
    except Exception:
        return False
    if cache.get("stamp") == stamp:
        return False
    try:
        value = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        # Keep the last known-good exclusions.  Do not cache a malformed file so
        # a subsequent repaired write is picked up immediately.
        return False
    if not isinstance(value, dict):
        return False
    for key in _LIVE_EXCLUSION_KEYS:
        raw = value.get(key)
        if isinstance(raw, list):
            cfg[key] = list(raw)
    cache["stamp"] = stamp
    return True


def _live_public_window(config_path: Path, cfg: dict, cache: dict, window=None) -> dict:
    refresh_live_exclusions(config_path, cfg, cache)
    return _public_window(window if window is not None else active_window(), cfg)


def screenshot(event_id: str) -> str | None:
    try:
        import mss
        from PIL import Image
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            shot = sct.grab(monitor)
            image = Image.frombytes("RGB", shot.size, shot.rgb)
            path = S_SCREEN / f"{event_id}.jpg"
            image.thumbnail((1600, 1000))
            image.save(path, format="JPEG", quality=70, optimize=True)
            return str(path)
    except Exception:
        return None


def _sanitize_existing_jsonl() -> None:
    """Rewrite legacy local evidence through the current storage sanitizer."""
    path = LOCAL_DIR / "events.jsonl"
    if not path.exists() or not path.is_file():
        return
    tmp = path.with_name(path.name + ".tmp")
    try:
        with path.open("r", encoding="utf-8", errors="replace") as src, tmp.open("w", encoding="utf-8") as dst:
            for line in src:
                try:
                    event = json.loads(line)
                    safe = sanitize_event_identifiers(event)
                    dst.write(json.dumps(safe, ensure_ascii=False) + "\n")
                except Exception:
                    continue
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


def append_local(event: dict) -> None:
    path = LOCAL_DIR / "events.jsonl"
    if path.exists() and path.stat().st_size >= MAX_JSONL_BYTES:
        previous = LOCAL_DIR / "events.jsonl.1"
        try:
            previous.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            path.replace(previous)
        except Exception:
            pass
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def persist_event(event: dict, outbox: EventOutbox) -> None:
    """Sanitize once, then write the same safe evidence to every local sink."""
    safe = sanitize_event_identifiers(event)
    append_local(safe)
    outbox.enqueue(safe)


def deliver_outbox_once(outbox: EventOutbox, backend_url: str, *, limit: int = 100) -> int:
    batch = outbox.pending(limit)
    if not batch:
        return 0
    ids = [str(e.get("event_id") or "") for e in batch]
    try:
        httpx.post(
            f"{backend_url.rstrip('/')}/v1/events",
            json={"events": batch},
            timeout=5,
        ).raise_for_status()
        outbox.acknowledge(ids)
        return len(batch)
    except Exception as exc:
        outbox.mark_failed(ids, str(exc))
        return 0


def _delivery_worker(outbox: EventOutbox, backend_url: str, stop: threading.Event) -> None:
    delay = 0.25
    while not stop.is_set():
        delivered = deliver_outbox_once(outbox, backend_url)
        if delivered:
            delay = 0.1
            continue
        stop.wait(delay)
        delay = min(3.0, delay * 1.5)


def post_heartbeat(status: dict, backend_url: str) -> None:
    try:
        httpx.post(
            f"{backend_url.rstrip('/')}/v1/heartbeat",
            json=status,
            timeout=2,
        ).raise_for_status()
    except Exception:
        pass


def _stop(*_args):
    global STOP
    STOP = True


def _identity_fields(cfg: dict, *, source: str = "desktop") -> dict:
    return {
        "schema_version": "1.0",
        "organization_id": str(cfg.get("organization_id") or ""),
        "actor_id": str(cfg.get("actor_id") or ""),
        "device_id": str(cfg.get("device_id") or ""),
        "sensor_id": str(cfg.get("sensor_id") or ""),
        "source": source,
    }


def _public_window(w, cfg: dict) -> dict:
    excluded = should_exclude(
        w.app,
        w.title,
        cfg["excluded_apps"],
        cfg["excluded_title_patterns"],
    )
    mode = cfg.get("window_title_mode", "full")
    return {
        "app": "Excluded" if excluded else (w.app or "Unknown"),
        "window_title": "" if excluded else title_for_mode(w.title, mode),
        "excluded": excluded,
        # In-memory only (event builders copy app/window_title/excluded, never
        # this): a noise-normalized document identity for same-app boundaries.
        # Empty when titles are excluded or the user chose not to record them.
        "document_key": "" if excluded or mode == "none" else document_key(w.title),
        # Additive local business context. Excluded windows never invoke native
        # app/browser automation, and no email/file/page contents are read.
        "business_context": {} if excluded else capture_business_context(w.app or "", w.title or ""),
    }


def _change_key(state: dict, cfg: dict):
    """Key whose change ends a span immediately (application, browser page, raw title)."""
    app = state["app"]
    if is_browser_app(app, cfg.get("browser_app_patterns")):
        return (app, normalized_browser_title(state["window_title"]))
    if cfg.get("change_detection") == "application_and_title":
        return (app, state["window_title"])
    return (app,)


def _document_tracking(state: dict, cfg: dict) -> bool:
    return (
        cfg.get("change_detection", "application_and_document") in {"application_and_document", "application"}
        and not is_browser_app(state["app"], cfg.get("browser_app_patterns"))
        and not state.get("excluded")
    )


def _focus_span_event(
    *,
    state: dict,
    started_at: str,
    duration_seconds: float,
    cfg: dict,
    session_id: str,
    screenshot_path: str | None = None,
    activity: dict | None = None,
    boundary_reason: str = "focus_change",
) -> dict:
    activity = dict(activity or {})
    return {
        "event_id": str(uuid.uuid4()),
        "observed_at": started_at,
        **_identity_fields(cfg),
        "session_id": session_id,
        "app": state["app"],
        "window_title": state["window_title"],
        "event_type": "focus_span",
        "duration_seconds": max(0.0, float(duration_seconds)),
        "screenshot_path": screenshot_path if screenshot_path and not cfg.get("upload_screenshots") else None,
        "metadata": {
            "source": "desktop",
            "excluded": state["excluded"],
            "screenshot_captured_locally": bool(screenshot_path),
            "change_detection": cfg.get("change_detection", "application"),
            "focus_boundary": boundary_reason,
            "activity": activity,
            "privacy": {"key_identities": False, "typed_values": False},
            **(state.get("business_context") or {}),
        },
    }


def _capture_gap_event(*, started_at: str, duration_seconds: float, cfg: dict, session_id: str) -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "observed_at": started_at,
        **_identity_fields(cfg),
        "session_id": session_id,
        "app": "Capture gap",
        "window_title": "",
        "event_type": "capture_gap",
        "duration_seconds": max(0.0, float(duration_seconds)),
        "screenshot_path": None,
        "metadata": {
            "source": "desktop",
            "reason": "poll_gap_or_suspend",
            "interpretation": "No foreground application is asserted for this interval.",
            "privacy": {"key_identities": False, "typed_values": False},
        },
    }


def _away_span_event(
    *,
    started_at: str,
    duration_seconds: float,
    cfg: dict,
    session_id: str,
    reason: str,
    idle_source: str,
    boundary_reason: str,
) -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "observed_at": started_at,
        **_identity_fields(cfg),
        "session_id": session_id,
        "app": "Away",
        "window_title": "",
        "event_type": "away_span",
        "duration_seconds": max(0.0, float(duration_seconds)),
        "screenshot_path": None,
        "metadata": {
            "source": "desktop",
            "reason": reason,
            "away_after_seconds": float(cfg.get("away_after_seconds", 300)),
            "idle_source": idle_source,
            "focus_boundary": boundary_reason,
            "interpretation": (
                "No keyboard or mouse input for at least away_after_seconds, or the screen was locked. "
                "The focus span before it keeps the first away_after_seconds, which covers reading "
                "and thinking without input. Never shared with an organization Gateway."
            ),
            "privacy": {"key_identities": False, "typed_values": False},
        },
    }


# Counters that mean OpenWorkGraph missed or could not see something. Only these
# (and permission changes) produce capture_health evidence; routine counters stay
# in the heartbeat and in the event's diagnostics block.
DEGRADATION_KEYS = (
    "interaction_worker_errors",
    "interaction_queue_dropped",
    "clipboard_queue_dropped",
    "active_window_unavailable",
)
HEALTH_REPEAT_SECONDS = 600.0


def _capture_health_event(
    *,
    capture_health: dict[str, int],
    cfg: dict,
    session_id: str,
    diagnostics: dict[str, int] | None = None,
    permissions: dict[str, bool | None] | None = None,
) -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "observed_at": utcnow(),
        **_identity_fields(cfg),
        "session_id": session_id,
        "app": "OpenWorkGraph",
        "window_title": "",
        "event_type": "capture_health",
        "duration_seconds": 0.0,
        "screenshot_path": None,
        "metadata": {
            "source": "collector",
            "capture_health": dict(capture_health),
            "diagnostics": dict(diagnostics or {}),
            "permissions": dict(permissions or {}),
            "missing_permissions": missing_permissions(dict(permissions or {})),
            "interpretation": "Nonzero counters or missing permissions indicate known capture degradation; zero counters are not proof that no external capture limitation existed.",
            "privacy": {"key_identities": False, "typed_values": False, "clipboard_contents": False},
        },
    }


def _interaction_event(*, raw: RawInteraction, cfg: dict, session_id: str) -> dict:
    state = dict(raw.context) if isinstance(raw.context, dict) else _public_window(active_window(), cfg)
    metadata: dict = {
        "source": "desktop",
        "action": raw.kind,
        "x": round(raw.x, 1),
        "y": round(raw.y, 1),
        "context_observed_at_interaction": bool(raw.context),
    }
    if raw.button:
        metadata["button"] = raw.button
    if raw.dx is not None:
        metadata["dx"] = raw.dx
    if raw.dy is not None:
        metadata["dy"] = raw.dy

    if not state["excluded"] and cfg.get("capture_ui_labels", True):
        target = element_at_position(raw.x, raw.y)
        if target:
            metadata["target"] = target
            metadata["target_observation_phase"] = "post_interaction_best_effort"

    event_id = str(uuid.uuid4())
    shot = None
    if cfg.get("interaction_screenshots_enabled") and not state["excluded"]:
        shot = screenshot(event_id)

    return {
        "event_id": event_id,
        "observed_at": _occurred_at(raw.occurred_mono),
        **_identity_fields(cfg),
        "session_id": session_id,
        "app": state["app"],
        "window_title": state["window_title"],
        "event_type": f"screen_{raw.kind}",
        "duration_seconds": 0.0,
        "screenshot_path": shot if shot and not cfg.get("upload_screenshots") else None,
        "metadata": metadata | {
            "excluded": state["excluded"],
            "screenshot_captured_locally": bool(shot),
        },
    }


def _clipboard_event(
    *,
    raw: RawClipboardAction,
    cfg: dict,
    session_id: str,
    event_id: str,
    transfer_id: str | None,
    linked_copy_event_id: str | None = None,
    link_age_seconds: float | None = None,
) -> dict:
    state = dict(raw.context) if isinstance(raw.context, dict) else _public_window(active_window(), cfg)
    metadata: dict = {
        "source": "desktop",
        "action": raw.kind,
        "evidence_channel": "os_clipboard_sequence" if raw.kind == "write" else "keyboard_shortcut",
        "context_observed_at_interaction": bool(raw.context),
        "clipboard_contents_captured": False,
        "clipboard_source_observed": bool(linked_copy_event_id) if raw.kind == "paste" else True,
        "privacy": {
            "key_identities": False,
            "typed_values": False,
            "clipboard_contents": False,
        },
        "excluded": state["excluded"],
    }
    if raw.kind == "write":
        metadata["interpretation"] = (
            "Something was written to the clipboard without a copy/cut shortcut (menu, right-click, "
            "drag, an app or a script). Only that fact is recorded; contents are never read. Not a paste."
        )
    if raw.clipboard_change_token is not None:
        metadata["clipboard_change_token"] = int(raw.clipboard_change_token)
    if transfer_id:
        metadata["clipboard_transfer_id"] = transfer_id
    if linked_copy_event_id:
        metadata["linked_copy_event_id"] = linked_copy_event_id
    if link_age_seconds is not None:
        metadata["clipboard_link_age_seconds"] = round(max(0.0, float(link_age_seconds)), 3)

    return {
        "event_id": event_id,
        "observed_at": _occurred_at(raw.occurred_mono),
        **_identity_fields(cfg),
        "session_id": session_id,
        "app": state["app"],
        "window_title": state["window_title"],
        "event_type": f"clipboard_{raw.kind}",
        "duration_seconds": 0.0,
        "screenshot_path": None,
        "metadata": metadata,
    }


def _interaction_worker(
    q: "queue.Queue[RawInteraction | RawClipboardAction | None]",
    cfg: dict,
    session_id: str,
    outbox: EventOutbox,
    capture_health: dict[str, int],
) -> None:
    last_clipboard_source: dict | None = None
    link_max_seconds = max(0.0, float(cfg.get("clipboard_link_max_seconds", 7200)))

    while True:
        raw = q.get()
        try:
            if raw is None:
                return
            if isinstance(raw, RawClipboardAction):
                event_id = str(uuid.uuid4())
                transfer_id: str | None = None
                linked_copy_event_id: str | None = None
                link_age_seconds: float | None = None

                if raw.kind in {"copy", "cut", "write"}:
                    transfer_id = str(uuid.uuid4())
                    last_clipboard_source = {
                        "event_id": event_id,
                        "transfer_id": transfer_id,
                        "occurred_mono": raw.occurred_mono,
                    }
                elif raw.kind == "paste" and last_clipboard_source is not None:
                    age = max(0.0, raw.occurred_mono - float(last_clipboard_source["occurred_mono"]))
                    if age <= link_max_seconds:
                        transfer_id = str(last_clipboard_source["transfer_id"])
                        linked_copy_event_id = str(last_clipboard_source["event_id"])
                        link_age_seconds = age

                event = _clipboard_event(
                    raw=raw,
                    cfg=cfg,
                    session_id=session_id,
                    event_id=event_id,
                    transfer_id=transfer_id,
                    linked_copy_event_id=linked_copy_event_id,
                    link_age_seconds=link_age_seconds,
                )
            else:
                event = _interaction_event(raw=raw, cfg=cfg, session_id=session_id)
            persist_event(event, outbox)
        except Exception:
            capture_health["interaction_worker_errors"] = capture_health.get("interaction_worker_errors", 0) + 1
        finally:
            q.task_done()


def run(config_path: Path) -> int:
    global STOP
    STOP = False
    lock = CollectorLock(LOCAL_DIR)
    if not lock.acquire():
        print(
            f"Another OpenWorkGraph collector is already recording {LOCAL_DIR}; "
            "this one is not starting, so nothing is recorded twice."
        )
        return EXIT_ALREADY_RUNNING
    try:
        _run_locked(config_path)
    finally:
        lock.release()
    return 0


def _run_locked(config_path: Path) -> None:
    global STOP
    cfg = load_config(config_path)
    identity = load_or_create_identity(LOCAL_DIR, cfg)
    cfg.update(identity)
    session_id = str(uuid.uuid4())
    activity_tracker = ActivityTracker()
    capture_health = {key: 0 for key in DEGRADATION_KEYS}
    diagnostics = {
        "capture_gap_count": 0,
        "focus_checkpoint_count": 0,
        "document_boundary_count": 0,
        "away_count": 0,
    }
    permissions = sensor_permissions()
    last_permission_check = time.monotonic()
    last_health_evidence: dict[str, int] = dict(capture_health)
    last_health_permissions: list[str] = []
    last_health_evidence_at = 0.0
    _sanitize_existing_jsonl()
    outbox = EventOutbox(LOCAL_DIR / "collector_outbox.db")

    current_state: dict | None = None
    current_key = None
    current_doc = ""
    current_started_wall = ""
    current_started_mono = 0.0
    current_screenshot: str | None = None
    # A same-app title change becomes a boundary only after it persists.
    pending_doc = ""
    pending_count = 0
    pending_wall = ""
    pending_mono = 0.0
    pending_state: dict | None = None
    # Away: no input for away_after_seconds, or the screen is locked.
    away = False
    away_started_wall = ""
    away_started_mono = 0.0
    away_reason = ""
    away_idle_source = ""
    last_heartbeat = 0.0
    last_poll_mono = 0.0
    last_poll_wall_epoch = 0.0
    run_started_mono = time.monotonic()
    # Mutable stamp shared by the foreground loop and interaction callbacks.
    # Only exclusion lists are hot-reloaded; all other capture settings remain
    # stable for the run.
    live_exclusion_state: dict = {}

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    delivery_stop = threading.Event()
    delivery = threading.Thread(
        target=_delivery_worker,
        args=(outbox, cfg["backend_url"], delivery_stop),
        daemon=True,
    )
    delivery.start()

    interaction_q: "queue.Queue[RawInteraction | RawClipboardAction | None]" = queue.Queue(maxsize=5000)
    worker = threading.Thread(
        target=_interaction_worker,
        args=(interaction_q, cfg, session_id, outbox, capture_health),
        daemon=True,
    )
    worker.start()

    def snapshot_context() -> dict | None:
        try:
            return _live_public_window(config_path, cfg, live_exclusion_state)
        except Exception:
            return None

    def enqueue_interaction(raw: RawInteraction) -> None:
        activity_tracker.record(raw.kind, raw.occurred_mono)
        raw.context = snapshot_context()
        try:
            interaction_q.put(raw, timeout=0.05)
        except queue.Full:
            capture_health["interaction_queue_dropped"] += 1

    # A copy/cut shortcut changes the clipboard counter shortly afterwards; that
    # change is the shortcut's, not a separate write.
    clipboard_shortcut_until = [0.0]

    def enqueue_clipboard(raw: RawClipboardAction) -> None:
        if raw.kind in {"copy", "cut"}:
            clipboard_shortcut_until[0] = raw.occurred_mono + max(3.0, 2 * float(cfg.get("poll_seconds", 2)))
        raw.context = snapshot_context()
        try:
            interaction_q.put(raw, timeout=0.05)
        except queue.Full:
            capture_health["clipboard_queue_dropped"] += 1

    sensor = None
    sensor_started = False
    keyboard_sensor = None
    keyboard_started = False
    if cfg.get("screen_interactions_enabled", True):
        sensor = InteractionSensor(
            enqueue_interaction,
            capture_clicks=cfg.get("capture_clicks", True),
            capture_scrolls=cfg.get("capture_scrolls", True),
            scroll_min_interval_seconds=cfg.get("scroll_min_interval_seconds", 1.25),
        )
        sensor_started = sensor.start()

    if cfg.get("keyboard_activity_enabled", True):
        keyboard_sensor = KeyboardActivitySensor(
            lambda t: activity_tracker.record("key", t),
            clipboard_callback=enqueue_clipboard,
            capture_clipboard_shortcuts=cfg.get("clipboard_behavior_enabled", True),
        )
        keyboard_started = keyboard_sensor.start()

    watch_clipboard = bool(cfg.get("clipboard_behavior_enabled", True) and cfg.get("clipboard_write_detection_enabled", True))
    last_clipboard_token = clipboard_change_token() if watch_clipboard else None

    # What each sensor needs on macOS. A started listener is not proof: macOS
    # withholds events from an untrusted process while the thread still runs.
    mouse_needs = ("accessibility",)
    keyboard_needs = ("accessibility", "input_monitoring")

    def receiving(started: bool, needs: tuple[str, ...]) -> bool:
        return bool(started and all(permissions.get(n) is not False for n in needs))

    def keyboard_receiving() -> bool:
        return receiving(keyboard_started, keyboard_needs)

    def sensor_line(label: str, started: bool, needs: tuple[str, ...], on_text: str) -> str:
        if not started:
            return f"{label}: OFF / unavailable"
        denied = [n.replace("_", " ") for n in needs if permissions.get(n) is False]
        if denied:
            return f"{label}: BLOCKED (macOS {' and '.join(denied)} permission not granted; restart after granting)"
        if permissions and any(permissions.get(n) is None for n in needs):
            return f"{label}: {on_text} (permission not verified)"
        return f"{label}: {on_text}"

    print(f"Workflow Observer collector started. session={session_id}")
    print(f"device={cfg['device_id']} sensor={cfg['sensor_id']}")
    print(f"Durable delivery queue: {outbox.count()} pending event(s) at startup.")
    print("Polling detects focus/tab changes; long unchanged focus is checkpointed into durable spans rather than repeated polling rows.")
    print(f"Focus spans checkpoint every {max(30, float(cfg.get('focus_checkpoint_seconds', 120))):g}s so crashes cannot erase arbitrarily long work periods.")
    print(sensor_line("Screen interaction capture", sensor_started, mouse_needs, "ON (clicks + throttled scrolls)"))
    print(sensor_line("Keyboard activity", keyboard_started, keyboard_needs, "ON (counts only; key identities/text are discarded)"))
    clipboard_started = bool(keyboard_started and cfg.get("clipboard_behavior_enabled", True))
    print(sensor_line("Clipboard behavior", clipboard_started, keyboard_needs, "ON (copy/cut/paste shortcuts only; contents never read)"))
    if permissions:
        print(sensor_line("Window titles and UI labels", True, ("accessibility",), "ON"))
    print("Typed text, ordinary key identities, and clipboard contents are never stored. Ctrl+C stops collection.")

    def idle_seconds(now_mono: float) -> tuple[float | None, str]:
        value = system_idle_seconds()
        if value is not None:
            return value, "os_input_clock"
        # Fall back to OpenWorkGraph's own sensors, but only when they can
        # actually see input; otherwise never guess that the user is away.
        if not (keyboard_receiving() or receiving(sensor_started, mouse_needs)):
            return None, "unavailable"
        last = activity_tracker.last_input_mono()
        return max(0.0, now_mono - max(last or 0.0, run_started_mono)), "collector_input_sensors"

    def emit_focus(end_mono: float, reason: str) -> None:
        activity = activity_tracker.summarize(
            current_started_mono, end_mono,
            active_window_seconds=float(cfg.get("activity_active_window_seconds", 5)),
            engaged_grace_seconds=float(cfg.get("engaged_grace_seconds", 60)),
        )
        persist_event(_focus_span_event(
            state=current_state,
            started_at=current_started_wall,
            duration_seconds=end_mono - current_started_mono,
            cfg=cfg,
            session_id=session_id,
            screenshot_path=current_screenshot,
            activity=activity,
            boundary_reason=reason,
        ), outbox)

    def emit_away(end_mono: float, reason: str) -> None:
        persist_event(_away_span_event(
            started_at=away_started_wall,
            duration_seconds=end_mono - away_started_mono,
            cfg=cfg,
            session_id=session_id,
            reason=away_reason,
            idle_source=away_idle_source,
            boundary_reason=reason,
        ), outbox)

    def clear_pending() -> None:
        nonlocal pending_doc, pending_count, pending_wall, pending_mono, pending_state
        pending_doc, pending_count, pending_wall, pending_mono, pending_state = "", 0, "", 0.0, None

    def open_span(state: dict, key, wall: str, mono: float) -> None:
        nonlocal current_state, current_key, current_doc, current_started_wall, current_started_mono, current_screenshot
        current_state = state
        current_key = key
        current_doc = state.get("document_key", "") if _document_tracking(state, cfg) else ""
        current_started_wall = wall
        current_started_mono = mono
        current_screenshot = None
        if cfg.get("screenshots_enabled") and not state["excluded"]:
            current_screenshot = screenshot(str(uuid.uuid4()))
        clear_pending()

    def close_span() -> None:
        nonlocal current_state, current_key, current_doc, current_started_wall, current_started_mono, current_screenshot
        current_state, current_key, current_doc = None, None, ""
        current_started_wall, current_started_mono, current_screenshot = "", 0.0, None
        clear_pending()

    while not STOP:
        now_mono = time.monotonic()
        now_dt = datetime.now(timezone.utc)
        now_wall = now_dt.isoformat()
        now_wall_epoch = now_dt.timestamp()
        expected_poll = max(0.5, float(cfg.get("poll_seconds", 2)))
        # Privacy exclusions are the only capture settings that hot-reload. Do
        # this before any new foreground state can trigger UI-label lookup or a
        # screenshot for a newly excluded app/title.
        refresh_live_exclusions(config_path, cfg, live_exclusion_state)

        # Detect suspend/lock/stall generically by an unexpectedly large polling
        # gap. Never attribute the unobserved interval to the previously focused app.
        gap_threshold = max(10.0, float(cfg.get("capture_gap_seconds", 30)))
        if last_poll_mono > 0:
            mono_gap = max(0.0, now_mono - last_poll_mono)
            wall_gap = max(0.0, now_wall_epoch - last_poll_wall_epoch)
            if max(mono_gap, wall_gap) > gap_threshold:
                observed_end_mono = min(now_mono, last_poll_mono + expected_poll)
                if current_state is not None and observed_end_mono > current_started_mono:
                    emit_focus(observed_end_mono, "capture_gap")
                if away and observed_end_mono > away_started_mono:
                    emit_away(observed_end_mono, "capture_gap")
                away = False
                gap_duration = max(0.0, wall_gap - expected_poll)
                if gap_duration > 0:
                    gap_start = datetime.fromtimestamp(
                        last_poll_wall_epoch + expected_poll,
                        timezone.utc,
                    ).isoformat()
                    persist_event(_capture_gap_event(
                        started_at=gap_start,
                        duration_seconds=gap_duration,
                        cfg=cfg,
                        session_id=session_id,
                    ), outbox)
                    diagnostics["capture_gap_count"] += 1
                close_span()

        checkpoint_seconds = max(30.0, float(cfg.get("focus_checkpoint_seconds", 120)))

        # ---- away / back ---------------------------------------------------------------
        if cfg.get("away_detection_enabled", True):
            away_after = max(60.0, float(cfg.get("away_after_seconds", 300)))
            locked = screen_locked()
            idle, idle_source = idle_seconds(now_mono)
            away_now = bool(locked) or (idle is not None and idle >= away_after)
            if away_now and not away:
                if current_state is not None:
                    emit_focus(now_mono, "away")
                close_span()
                away = True
                away_started_wall, away_started_mono = now_wall, now_mono
                away_reason = "screen_locked" if locked else "no_input"
                away_idle_source = idle_source
                diagnostics["away_count"] += 1
            elif away and not away_now:
                emit_away(now_mono, "input_resumed")
                away = False
            elif away and now_mono - away_started_mono >= checkpoint_seconds:
                emit_away(now_mono, "periodic_checkpoint")
                away_started_wall, away_started_mono = now_wall, now_mono

        w = active_window()
        if (w.app or "Unknown") == "Unknown":
            capture_health["active_window_unavailable"] += 1
        state = _public_window(w, cfg)
        key = _change_key(state, cfg)

        if away:
            pass
        elif current_state is None:
            open_span(state, key, now_wall, now_mono)
        elif key != current_key:
            emit_focus(now_mono, "focus_change")
            open_span(state, key, now_wall, now_mono)
        else:
            candidate = state.get("document_key", "") if _document_tracking(state, cfg) else ""
            if _document_tracking(state, cfg) and is_material_change(current_doc, candidate):
                if candidate == pending_doc:
                    pending_count += 1
                else:
                    pending_doc, pending_count = candidate, 1
                    pending_wall, pending_mono, pending_state = now_wall, now_mono, state
                needed = max(1, int(cfg.get("document_debounce_polls", 2)))
                # Discovery studies intentionally retain brief document glances:
                # with the normal 2 s poll, one confirmed poll is ~2 s instead of
                # the ordinary ~4 s two-poll debounce.
                try:
                    from shared.discovery_scope import read_state as _read_discovery_state
                    if _read_discovery_state().get("status") == "active":
                        needed = 1
                except Exception:
                    pass
                if pending_count >= needed and pending_state is not None:
                    # The new document began when it first appeared, not when confirmed.
                    boundary_mono = max(pending_mono, current_started_mono)
                    boundary_wall = pending_wall if pending_mono >= current_started_mono else current_started_wall
                    first_state = pending_state
                    if boundary_mono > current_started_mono:
                        emit_focus(boundary_mono, "document_change")
                    # else: the new title appeared exactly when the current span
                    # began (a checkpoint), so relabel it rather than emit an
                    # empty span for the old document.
                    diagnostics["document_boundary_count"] += 1
                    open_span(first_state, key, boundary_wall, boundary_mono)
            else:
                clear_pending()

            if current_state is not None and now_mono - current_started_mono >= checkpoint_seconds:
                emit_focus(now_mono, "periodic_checkpoint")
                diagnostics["focus_checkpoint_count"] += 1
                pending = (pending_doc, pending_count, pending_wall, pending_mono, pending_state)
                open_span(current_state, current_key, now_wall, now_mono)
                pending_doc, pending_count, pending_wall, pending_mono, pending_state = pending

        if watch_clipboard:
            token = clipboard_change_token()
            if token is not None and last_clipboard_token is not None and token != last_clipboard_token:
                if away:
                    pass  # nobody at the computer: an app or sync wrote it, not the person
                elif now_mono > clipboard_shortcut_until[0]:
                    enqueue_clipboard(RawClipboardAction(kind="write", occurred_mono=now_mono, clipboard_change_token=token))
                else:
                    clipboard_shortcut_until[0] = 0.0
            if token is not None:
                last_clipboard_token = token

        if now_mono - last_permission_check >= 60:
            permissions = sensor_permissions()
            last_permission_check = now_mono

        if now_mono - last_heartbeat >= float(cfg.get("heartbeat_seconds", 5)):
            heartbeat_activity = activity_tracker.summarize(
                current_started_mono, now_mono,
                active_window_seconds=float(cfg.get("activity_active_window_seconds", 5)),
                engaged_grace_seconds=float(cfg.get("engaged_grace_seconds", 60)),
            ) if current_state is not None else {}
            # Nest inside the established `activity` field so older local APIs keep
            # accepting the heartbeat while new dashboards can inspect health.
            heartbeat_activity["capture_health"] = dict(capture_health)
            heartbeat_activity["diagnostics"] = dict(diagnostics)
            heartbeat_activity["permissions"] = dict(permissions)
            heartbeat_activity["away"] = away
            post_heartbeat(
                {
                    "device_id": cfg["device_id"],
                    "sensor_id": cfg["sensor_id"],
                    "organization_id": cfg.get("organization_id", ""),
                    "actor_id": cfg.get("actor_id", ""),
                    "session_id": session_id,
                    "observed_at": now_wall,
                    "app": "Away" if away else state["app"],
                    "window_title": "" if away else state["window_title"],
                    "focus_elapsed_seconds": round(max(0.0, now_mono - (away_started_mono if away else current_started_mono)), 3),
                    "activity": heartbeat_activity,
                    "keyboard_sensor": keyboard_receiving(),
                    "outbox_pending": outbox.count(),
                },
                cfg["backend_url"],
            )
            # Persist sparse quality evidence only for real degradation: a new kind
            # of problem or a permission change right away, repeats at most every
            # HEALTH_REPEAT_SECONDS. Routine counters never trigger it.
            missing_now = missing_permissions(permissions)
            new_kind = any(capture_health[k] and not last_health_evidence.get(k) for k in DEGRADATION_KEYS)
            grew = capture_health != last_health_evidence
            if missing_now != last_health_permissions or new_kind or (
                grew and now_mono - last_health_evidence_at >= HEALTH_REPEAT_SECONDS
            ):
                persist_event(_capture_health_event(
                    capture_health=capture_health,
                    cfg=cfg,
                    session_id=session_id,
                    diagnostics=diagnostics,
                    permissions=permissions,
                ), outbox)
                last_health_evidence = dict(capture_health)
                last_health_permissions = missing_now
                last_health_evidence_at = now_mono
            last_heartbeat = now_mono

        last_poll_mono = now_mono
        last_poll_wall_epoch = now_wall_epoch
        time.sleep(expected_poll)

    if sensor is not None:
        sensor.stop()
    if keyboard_sensor is not None:
        keyboard_sensor.stop()
    try:
        interaction_q.put(None, timeout=0.5)
        interaction_q.join()
    except Exception:
        capture_health["interaction_worker_errors"] += 1

    end_mono = time.monotonic()
    if current_state is not None:
        emit_focus(end_mono, "shutdown")
    if away:
        emit_away(end_mono, "shutdown")

    if capture_health != last_health_evidence:
        persist_event(_capture_health_event(
            capture_health=capture_health,
            cfg=cfg,
            session_id=session_id,
            diagnostics=diagnostics,
            permissions=permissions,
        ), outbox)

    for _ in range(5):
        if outbox.count() == 0:
            break
        if not deliver_outbox_once(outbox, cfg["backend_url"], limit=200):
            break
    delivery_stop.set()
    delivery.join(timeout=1)
    print(f"Collector stopped. {outbox.count()} event(s) remain queued for next launch. capture_health={capture_health}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Local-first workflow observation collector")
    parser.add_argument("--config", default=str(ROOT / "config.json"))
    args = parser.parse_args()
    raise SystemExit(run(Path(args.config)))

if __name__ == "__main__":
    main()
