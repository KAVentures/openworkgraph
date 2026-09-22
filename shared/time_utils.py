from __future__ import annotations

from datetime import datetime, timezone


def normalize_timestamp(value: str) -> str:
    """Parse an ISO-8601 timestamp and return a canonical UTC string.

    OpenWorkGraph accepts offsets such as ``+02:00`` and ``Z`` at API boundaries,
    but stores/compares timestamps in one UTC representation so lexical ordering
    is chronological. Naive timestamps are rejected because their instant is
    ambiguous.
    """
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("timestamp is required")
    candidate = raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw
    try:
        parsed = datetime.fromisoformat(candidate)
    except Exception as exc:
        raise ValueError("timestamp must be valid ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone offset or Z")
    utc = parsed.astimezone(timezone.utc)
    # Fixed-width microseconds make text ordering safe in SQLite/PostgreSQL TEXT.
    return utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def normalize_optional_timestamp(value: str | None) -> str | None:
    if value is None or not str(value).strip():
        return None
    return normalize_timestamp(str(value))
