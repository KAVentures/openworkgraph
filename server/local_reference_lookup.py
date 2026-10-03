from __future__ import annotations

"""Local-only resolver dictionary for observed business and file references."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Any

_LOCK = threading.RLock()
_MAX_ITEMS = 5000


def _data_dir() -> Path:
    root = Path(__file__).resolve().parents[1]
    path = Path(os.getenv("WORKFLOW_OBSERVER_DATA", root / "data"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _path() -> Path:
    return _data_dir() / ".local_reference_lookup.json"


def _read() -> dict[str, Any]:
    try:
        value = json.loads(_path().read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _write(value: dict[str, Any]) -> None:
    path = _path()
    fd, tmp = tempfile.mkstemp(prefix=".owg-ref-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(tmp, 0o600)
        except Exception:
            pass
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except Exception:
            pass


def _trim(rows: dict[str, Any]) -> None:
    if len(rows) <= _MAX_ITEMS:
        return
    ordered = sorted(rows.items(), key=lambda item: str(item[1].get("updated_at") or ""))
    for key, _ in ordered[: len(rows) - _MAX_ITEMS]:
        rows.pop(key, None)


def remember_resource_reference(reference: dict[str, Any], locator: str) -> None:
    token = str(reference.get("resource_ref") or "")
    locator = str(locator or "").strip()
    if not token.startswith("owg:r:") or not locator:
        return
    with _LOCK:
        data = _read()
        refs = data.setdefault("resources", {})
        refs[token] = {
            "provider": str(reference.get("provider") or ""),
            "resource_kind": str(reference.get("resource_kind") or ""),
            "host": str(reference.get("host") or ""),
            "resolver_locator": locator[:320],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        _trim(refs)
        _write(data)


def remember_file_reference(reference: str, *, path: str, sha256: str, size: int, mtime_ns: int) -> None:
    if not str(reference).startswith("owg:f:") or not path or not sha256:
        return
    with _LOCK:
        data = _read()
        refs = data.setdefault("files", {})
        refs[str(reference)] = {
            "path": str(path),
            "sha256": str(sha256).lower(),
            "size": max(0, int(size)),
            "mtime_ns": max(0, int(mtime_ns)),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        _trim(refs)
        _write(data)


def resolve_resource_reference(reference: str) -> dict[str, Any] | None:
    value = _read().get("resources", {}).get(str(reference))
    return dict(value) if isinstance(value, dict) else None


def resolve_file_reference(reference: str) -> dict[str, Any] | None:
    value = _read().get("files", {}).get(str(reference))
    return dict(value) if isinstance(value, dict) else None


def expand_resource_references(value: Any) -> Any:
    if isinstance(value, list):
        return [expand_resource_references(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: expand_resource_references(child) for key, child in value.items()}
    token = str(result.get("resource_ref") or "")
    if token.startswith("owg:r:") and "resolver_locator" not in result:
        resolved = resolve_resource_reference(token)
        if resolved:
            result["resolver_locator"] = resolved.get("resolver_locator")
            result["resolution"] = "local_full_detail"
    return result


__all__ = [
    "remember_resource_reference",
    "remember_file_reference",
    "resolve_resource_reference",
    "resolve_file_reference",
    "expand_resource_references",
]
