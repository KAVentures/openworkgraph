from __future__ import annotations

import argparse
import json
import os
import signal
import time
import uuid
import queue
import threading
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .platform import active_window
from .accessibility import element_at_position
from .interactions import InteractionSensor, KeyboardActivitySensor, ActivityTracker, RawInteraction
from .privacy import should_exclude, title_for_mode
from .identity import load_or_create_identity
from .outbox import EventOutbox
from browser_utils import is_browser_app, normalized_browser_title
from sensitive_identifiers import sanitize_event_identifiers

ROOT = Path(__file__).resolve().parents[1]
LOCAL_DIR = Path(os.getenv("WORKFLOW_OBSERVER_DATA", ROOT / "data"))
LOCAL_DIR.mkdir(parents=True, exist_ok=True)
S_SCREEN = LOCAL_DIR / "screenshots"
S_SCREEN.mkdir(parents=True, exist_ok=True)
MAX_JSONL_BYTES = 32 * 1024 * 1024
STOP = False


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_config(path: Path) -> dict:
    cfg = {
        "device_id": "",
        "organization_id": "",
        "actor_id": "",
        "backend_url": "http://127.0.0.1:8787",
        "poll_seconds": 2,
        "heartbeat_seconds": 5,
        "change_detection": "application",
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
        "activity_active_window_seconds": 5,
        "engaged_grace_seconds": 60,
    }
    if path.exists():
        cfg.update(json.loads(path.read_text(encoding="utf-8")))
    return cfg


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
                    # The JSONL is a diagnostic/recovery mirror, not the durable
                    # delivery queue. Omit malformed legacy lines rather than keep
                    # potentially sensitive plaintext that cannot be classified.
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
    return {
        "app": "Excluded" if excluded else (w.app or "Unknown"),
        "window_title": "" if excluded else title_for_mode(
            w.title, cfg.get("window_title_mode", "full")
        ),
        "excluded": excluded,
    }


def _change_key(state: dict, cfg: dict):
    app = state["app"]
    if is_browser_app(app, cfg.get("browser_app_patterns")):
        return (app, normalized_browser_title(state["window_title"]))
    if cfg.get("change_detection") == "application_and_title":
        return (app, state["window_title"])
    return (app,)


def _focus_span_event(
    *,
    state: dict,
    started_at: str,
    duration_seconds: float,
    cfg: dict,
    session_id: str,
    screenshot_path: str | None = None,
    activity: dict | None = None,
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
            "activity": activity,
            "privacy": {"key_identities": False, "typed_values": False},
        },
    }


def _interaction_event(
    *,
    raw: RawInteraction,
    cfg: dict,
    session_id: str,
) -> dict:
    w = active_window()
    state = _public_window(w, cfg)
    metadata: dict = {
        "source": "desktop",
        "action": raw.kind,
        "x": round(raw.x, 1),
        "y": round(raw.y, 1),
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

    event_id = str(uuid.uuid4())
    shot = None
    if cfg.get("interaction_screenshots_enabled") and not state["excluded"]:
        shot = screenshot(event_id)

    return {
        "event_id": event_id,
        "observed_at": utcnow(),
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


def _interaction_worker(
    q: "queue.Queue[RawInteraction | None]",
    cfg: dict,
    session_id: str,
    outbox: EventOutbox,
) -> None:
    while True:
        raw = q.get()
        try:
            if raw is None:
                return
            event = _interaction_event(raw=raw, cfg=cfg, session_id=session_id)
            persist_event(event, outbox)
        except Exception:
            pass
        finally:
            q.task_done()


def run(config_path: Path) -> None:
    global STOP
    STOP = False
    cfg = load_config(config_path)
    identity = load_or_create_identity(LOCAL_DIR, cfg)
    cfg.update(identity)
    session_id = str(uuid.uuid4())
    activity_tracker = ActivityTracker()
    _sanitize_existing_jsonl()
    outbox = EventOutbox(LOCAL_DIR / "collector_outbox.db")

    current_state: dict | None = None
    current_key = None
    current_started_wall = ""
    current_started_mono = 0.0
    current_screenshot: str | None = None
    last_heartbeat = 0.0

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    delivery_stop = threading.Event()
    delivery = threading.Thread(
        target=_delivery_worker,
        args=(outbox, cfg["backend_url"], delivery_stop),
        daemon=True,
    )
    delivery.start()

    interaction_q: "queue.Queue[RawInteraction | None]" = queue.Queue(maxsize=5000)
    worker = threading.Thread(
        target=_interaction_worker, args=(interaction_q, cfg, session_id, outbox), daemon=True
    )
    worker.start()

    def enqueue_interaction(raw: RawInteraction) -> None:
        activity_tracker.record(raw.kind, raw.occurred_mono)
        try:
            interaction_q.put(raw, timeout=0.05)
        except queue.Full:
            pass

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
        keyboard_sensor = KeyboardActivitySensor(lambda t: activity_tracker.record("key", t))
        keyboard_started = keyboard_sensor.start()

    print(f"Workflow Observer collector started. session={session_id}")
    print(f"device={cfg['device_id']} sensor={cfg['sensor_id']}")
    print(f"Durable delivery queue: {outbox.count()} pending event(s) at startup.")
    print("Polling detects focus/tab changes; unchanged polls are NOT stored as events.")
    print("Screen interaction capture: " + ("ON (clicks + throttled scrolls)" if sensor_started else "OFF / permission unavailable"))
    print("Keyboard activity: " + ("ON (counts only; key identities/text are discarded)" if keyboard_started else "OFF / permission unavailable"))
    print("Typed text, key identities, and clipboard contents are never stored. Ctrl+C stops collection.")

    while not STOP:
        w = active_window()
        state = _public_window(w, cfg)
        key = _change_key(state, cfg)
        now_mono = time.monotonic()
        now_wall = utcnow()

        if current_state is None:
            current_state = state
            current_key = key
            current_started_wall = now_wall
            current_started_mono = now_mono
            if cfg.get("screenshots_enabled") and not state["excluded"]:
                current_screenshot = screenshot(str(uuid.uuid4()))

        elif key != current_key:
            activity = activity_tracker.summarize(
                current_started_mono, now_mono,
                active_window_seconds=float(cfg.get("activity_active_window_seconds", 5)),
                engaged_grace_seconds=float(cfg.get("engaged_grace_seconds", 60)),
            )
            event = _focus_span_event(
                state=current_state,
                started_at=current_started_wall,
                duration_seconds=now_mono - current_started_mono,
                cfg=cfg,
                session_id=session_id,
                screenshot_path=current_screenshot,
                activity=activity,
            )
            persist_event(event, outbox)

            current_state = state
            current_key = key
            current_started_wall = now_wall
            current_started_mono = now_mono
            current_screenshot = None
            if cfg.get("screenshots_enabled") and not state["excluded"]:
                current_screenshot = screenshot(str(uuid.uuid4()))

        if now_mono - last_heartbeat >= float(cfg.get("heartbeat_seconds", 5)):
            post_heartbeat(
                {
                    "device_id": cfg["device_id"],
                    "sensor_id": cfg["sensor_id"],
                    "organization_id": cfg.get("organization_id", ""),
                    "actor_id": cfg.get("actor_id", ""),
                    "session_id": session_id,
                    "observed_at": now_wall,
                    "app": state["app"],
                    "window_title": state["window_title"],
                    "focus_elapsed_seconds": round(max(0.0, now_mono - current_started_mono), 3),
                    "activity": activity_tracker.summarize(
                        current_started_mono, now_mono,
                        active_window_seconds=float(cfg.get("activity_active_window_seconds", 5)),
                        engaged_grace_seconds=float(cfg.get("engaged_grace_seconds", 60)),
                    ) if current_state is not None else {},
                    "keyboard_sensor": keyboard_started,
                    "outbox_pending": outbox.count(),
                },
                cfg["backend_url"],
            )
            last_heartbeat = now_mono

        time.sleep(max(0.5, float(cfg["poll_seconds"])))

    if sensor is not None:
        sensor.stop()
    if keyboard_sensor is not None:
        keyboard_sensor.stop()
    try:
        interaction_q.put(None, timeout=0.5)
        interaction_q.join()
    except Exception:
        pass

    if current_state is not None:
        end_mono = time.monotonic()
        activity = activity_tracker.summarize(
            current_started_mono, end_mono,
            active_window_seconds=float(cfg.get("activity_active_window_seconds", 5)),
            engaged_grace_seconds=float(cfg.get("engaged_grace_seconds", 60)),
        )
        event = _focus_span_event(
            state=current_state,
            started_at=current_started_wall,
            duration_seconds=end_mono - current_started_mono,
            cfg=cfg,
            session_id=session_id,
            screenshot_path=current_screenshot,
            activity=activity,
        )
        persist_event(event, outbox)

    for _ in range(5):
        if outbox.count() == 0:
            break
        if not deliver_outbox_once(outbox, cfg["backend_url"], limit=200):
            break
    delivery_stop.set()
    delivery.join(timeout=1)
    print(f"Collector stopped. {outbox.count()} event(s) remain queued for next launch.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Local-first workflow observation collector")
    parser.add_argument("--config", default=str(ROOT / "config.json"))
    args = parser.parse_args()
    run(Path(args.config))


if __name__ == "__main__":
    main()
