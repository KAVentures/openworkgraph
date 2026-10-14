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


def reference_tail(
    connection: Any, *,
    lease: dict[str, Any],
    run_started_at: str | None = None,
    limit: int = 24,
) -> dict[str, Any]:
    """Retrieve only permitted resource pointers; fail closed on unknown grants."""
    mode = str(lease.get("mode") or "off")
    allowed: list[str] = []
    args: list[Any] = []
    current = _date(run_started_at)
    if current:
        allowed.append("julianday(observed_at) >= julianday(?)")
        args.append(current)
    if mode == "all_saved":
        allowed = ["1=1"]
        args = []
    elif mode == "selected_range":
        since, until = _date(lease.get("since")), _date(lease.get("until"))
        if since and until and since < until:
            allowed.append("(julianday(observed_at) >= julianday(?) AND julianday(observed_at) < julianday(?))")
            args.extend((since, until))
    elif mode != "off":
        # Malformed/unknown access modes must never become all_saved.
        mode = "off"
    if not allowed:
        return {"recent_evidence": [], "returned": 0, "saved_history_access": mode,
                "history_access_limited": True, "references_are_observed_only": True}
    bounded = max(1, min(100, int(limit)))
    where = " OR ".join(f"({part})" for part in allowed)
    sql = (
        "SELECT event_id, observed_at, metadata_json FROM events "
        "WHERE (metadata_json LIKE '%\"resource_reference\"%' "
        "OR metadata_json LIKE '%\"file_reference\"%') "
        f"AND ({where}) "
        "ORDER BY julianday(observed_at) DESC, id DESC LIMIT ?"
    )
    rows = connection.execute(sql, tuple(args + [bounded])).fetchall()
    evidence: list[dict[str, Any]] = []
    for row in rows:
        try:
            data = dict(row)
            meta = json.loads(str(data.get("metadata_json") or "{}"))
        except (TypeError, ValueError):
            continue
        if not isinstance(meta, dict):
            continue
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


__all__ = ["reference_tail"]
