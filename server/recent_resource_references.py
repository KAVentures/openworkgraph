"""Reference-only continuity tail, scoped to existing local AI-history consent.

This is NOT a new stored memory: query canonical events and project only
already observed opaque references. Never expose window text, URL paths,
labels, arbitrary metadata, message contents, or deleted events.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from typing import Any

_RESOURCE = re.compile(r"^owg:r:[0-9a-f]{32}$")
_FILE = re.compile(r"^owg:f:[0-9a-f]{16,64}$")


def _date(value: Any) -> str | None:
    try:
        result = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return result.astimezone(timezone.utc).isoformat() if result.tzinfo else None
    except (TypeError, ValueError, OverflowError):
        return None


def lease_window(
    lease: dict[str, Any], run_started_at: str | None = None,
) -> tuple[str, str | None, list[Any]]:
    """Return (mode, SQL condition, args) for rows the AI-history grant permits.

    The condition is None when nothing may be read. Malformed or unknown access
    modes fail closed and never become all_saved.
    """
    mode = str(lease.get("mode") or "off")
    allowed: list[str] = []
    args: list[Any] = []
    current = _date(run_started_at)
    if current:
        allowed.append("julianday(observed_at) >= julianday(?)")
        args.append(current)
    if mode == "all_saved":
        return mode, "1=1", []
    if mode == "selected_range":
        since, until = _date(lease.get("since")), _date(lease.get("until"))
        if since and until and since < until:
            allowed.append("(julianday(observed_at) >= julianday(?) AND julianday(observed_at) < julianday(?))")
            args.extend((since, until))
    elif mode != "off":
        mode = "off"
    if not allowed:
        return mode, None, []
    return mode, " OR ".join(f"({part})" for part in allowed), args


def sanitized_pointers(metadata_json: Any) -> dict[str, Any]:
    """Project one row's metadata to opaque resource/file pointers only."""
    try:
        meta = json.loads(str(metadata_json or "{}"))
    except (TypeError, ValueError):
        return {}
    if not isinstance(meta, dict):
        return {}
    sanitized: dict[str, Any] = {}
    reference = meta.get("resource_reference")
    if isinstance(reference, dict):
        ref = str(reference.get("resource_ref") or "")
        if _RESOURCE.fullmatch(ref):
            sanitized["resource_reference"] = {"resource_ref": ref}
            for field in ("provider", "resource_kind", "host", "resolution"):
                value = str(reference.get(field) or "")[:120]
                # No path/query/credentials or arbitrary content enters the
                # AI-visible pointer projection.
                if value and re.fullmatch(r"[a-zA-Z0-9._:-]{1,120}", value):
                    sanitized["resource_reference"][field] = value
    file_ref = meta.get("file_reference")
    if isinstance(file_ref, dict) and _FILE.fullmatch(str(file_ref.get("file_ref") or "")):
        sanitized["file_reference"] = {"file_ref": str(file_ref["file_ref"])}
    return sanitized


POINTER_FILTER = (
    "(metadata_json LIKE '%\"resource_reference\"%' "
    "OR metadata_json LIKE '%\"file_reference\"%')"
)


def reference_tail(
    connection: Any, *,
    lease: dict[str, Any],
    run_started_at: str | None = None,
    limit: int = 24,
) -> dict[str, Any]:
    """Retrieve only permitted resource pointers; fail closed on unknown grants."""
    mode, where, args = lease_window(lease, run_started_at)
    if where is None:
        return {"recent_evidence": [], "returned": 0, "saved_history_access": mode,
                "history_access_limited": True, "references_are_observed_only": True}
    bounded = max(1, min(100, int(limit)))
    sql = (
        "SELECT event_id, observed_at, metadata_json FROM events "
        f"WHERE {POINTER_FILTER} AND ({where}) "
        "ORDER BY julianday(observed_at) DESC, id DESC LIMIT ?"
    )
    rows = connection.execute(sql, tuple(args + [bounded])).fetchall()
    evidence: list[dict[str, Any]] = []
    for row in rows:
        data = dict(row)
        sanitized = sanitized_pointers(data.get("metadata_json"))
        if sanitized:
            evidence.append({
                "event_id": str(data.get("event_id") or "")[:120],
                "observed_at": str(data.get("observed_at") or "")[:80],
                "metadata": sanitized,
            })
    return {
        "recent_evidence": evidence,
        "returned": len(evidence),
        "saved_history_access": mode,
        "history_access_limited": mode != "all_saved",
        "references_are_observed_only": True,
    }


__all__ = ["POINTER_FILTER", "lease_window", "reference_tail", "sanitized_pointers"]
