"""Opt-in full screen capture: every monitor, native resolution, optional OCR.

Off by default. When the user sets ``screen_capture_mode`` to ``"full"`` the
recorder captures every connected monitor at native resolution when a focus
span starts, on each click/scroll and every ``screenshot_interval_seconds``
while a span stays open. With ``screen_text_ocr_enabled`` it also runs on-device
text recognition (Tesseract, when installed) and stores the recognized text in
a local sidecar file.

Everything stays on this device: images, text and the per-capture manifest are
written under the local screenshots folder and are never uploaded by this
module. The user's never-record lists still apply: excluded apps and titles are
never captured, and pausing the recorder stops capture.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MODES = ("off", "full")
FULL_QUALITY = 90
MIN_INTERVAL_SECONDS = 5.0


def mode(cfg: dict[str, Any] | None) -> str:
    value = (cfg or {}).get("screen_capture_mode", "off")
    return value if value in MODES else "off"


def full_mode(cfg: dict[str, Any] | None) -> bool:
    return mode(cfg) == "full"


def interval_seconds(cfg: dict[str, Any] | None) -> float:
    try:
        value = float((cfg or {}).get("screenshot_interval_seconds", 20))
    except (TypeError, ValueError):
        value = 20.0
    return max(MIN_INTERVAL_SECONDS, value)


def _ocr(image: Any) -> tuple[str, str]:
    """Return (status, text). Missing OCR support is reported, never faked."""
    try:
        import pytesseract  # type: ignore[import-not-found]
    except Exception:
        return "unavailable", ""
    try:
        return "recognized", str(pytesseract.image_to_string(image) or "").strip()
    except Exception:
        return "failed", ""


def capture_all_monitors(
    event_id: str, directory: Path, *, ocr: bool = False, grabber: Any = None,
) -> dict[str, Any] | None:
    """Capture every monitor; return the manifest, or None when nothing was captured.

    ``grabber`` is an ``mss``-compatible object for tests; normally ``mss.mss()``.
    """
    try:
        from PIL import Image

        if grabber is None:
            import mss

            grabber = mss.mss()
        with grabber as sct:
            # monitors[0] is the union of all screens; 1..n are the real monitors.
            monitors = list(sct.monitors[1:]) or list(sct.monitors[:1])
            images: list[dict[str, Any]] = []
            texts: list[str] = []
            ocr_status = "disabled"
            for index, monitor in enumerate(monitors, start=1):
                shot = sct.grab(monitor)
                image = Image.frombytes("RGB", shot.size, shot.rgb)
                path = directory / f"{event_id}-m{index}.jpg"
                image.save(path, format="JPEG", quality=FULL_QUALITY, optimize=True)
                images.append({"path": str(path), "monitor": index,
                               "width": int(shot.size[0]), "height": int(shot.size[1])})
                if ocr:
                    ocr_status, text = _ocr(image)
                    if text:
                        texts.append(f"[monitor {index}]\n{text}")
    except Exception:
        return None
    if not images:
        return None
    manifest: dict[str, Any] = {
        "mode": "full",
        "event_id": event_id,
        "monitor_count": len(images),
        "images": images,
        "ocr_status": ocr_status,
        "text_path": None,
        "text_characters": 0,
        "stored": "local_only",
    }
    if texts:
        text_path = directory / f"{event_id}.txt"
        body = "\n\n".join(texts)
        text_path.write_text(body, encoding="utf-8")
        manifest["text_path"] = str(text_path)
        manifest["text_characters"] = len(body)
    (directory / f"{event_id}.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def event_summary(manifest: dict[str, Any] | None) -> dict[str, Any]:
    """Counts for event metadata; paths and recognized text stay in the local manifest."""
    if not manifest:
        return {}
    return {"screen_capture": {
        "mode": "full",
        "capture_id": manifest["event_id"],
        "monitor_count": manifest["monitor_count"],
        "ocr_status": manifest["ocr_status"],
        "text_captured": bool(manifest["text_characters"]),
        "stored": "local_only",
    }}


__all__ = ["MODES", "capture_all_monitors", "event_summary", "full_mode", "interval_seconds", "mode"]
