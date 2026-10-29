"""Persistent, counts-only quality and mapping reports for saved event logs (#370 R6).

Every import and every appended batch stores one report next to the log:

- the transform: source format, column mapping, mapping version, and SHA-256
  digests of the uploaded bytes, of the transform definition and of the stored
  content, so the same file with the same mapping reproduces the same digests;
- what was accepted and rejected, with reasons where the reader knows them
  (rejected rows themselves are never kept);
- sample-free diagnostics: duplicate events, events sharing a timestamp within
  a case (their order then falls back to the event identifier), missing
  resources, events without a case, single-event cases, attribute coverage and
  events dated after the import.

A report only describes the rows that were supplied. Whether the source system
holds more history is unknown, and the report says so instead of calling the
log complete.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from . import event_log_workspace as workspace

MAPPING_VERSION = "owg.event-log-mapping.v1"
TRANSFORM_VERSION = "owg.event-log-import.v1"
MAX_ATTRIBUTES_REPORTED = 32
COMPLETENESS_NOTE = ("Counts describe the supplied rows only. Whether the source system holds events that were not "
                     "exported is unknown, so this log is not known to be complete.")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def transform(source_format: str, mapping: dict[str, Any] | None) -> dict[str, Any]:
    """The declared transform. Attribute column order is kept: it is part of what gets stored."""
    mapping = mapping or {}
    declared: dict[str, Any] = {"importer": TRANSFORM_VERSION, "format": source_format,
                                "timestamps": "explicit UTC offset required; stored as UTC"}
    if source_format == "csv":
        declared["mapping"] = {
            "mapping_version": MAPPING_VERSION,
            "case_column": mapping.get("case_column") or "",
            "activity_column": mapping.get("activity_column") or "",
            "timestamp_column": mapping.get("timestamp_column") or "",
            "resource_column": mapping.get("resource_column") or "",
            "attribute_columns": list(dict.fromkeys(mapping.get("attribute_columns") or [])),
            "delimiter": mapping.get("delimiter") or ",",
        }
        if mapping.get("naive_timestamp_offset"):
            # An explicit decision changes what is stored, so it is part of the transform.
            declared["mapping"]["naive_timestamp_offset"] = mapping["naive_timestamp_offset"]
        identity=workspace.source_identity.settings(mapping)
        if identity:
            declared['mapping']['source_identity']={'format':workspace.source_identity.FORMAT,**vars(identity)}
    else:
        declared["mapping"] = {"mapping_version": MAPPING_VERSION, "standard": {
            "xes": "XES concept:name / time:timestamp / org:resource; trace concept:name as case",
            "ocel": "OCEL 2.0 JSON event types, objects and qualified relationships",
            "ocelxml": "OCEL 2.0 XML event types, objects and qualified relationships"}[source_format]}
    return {**declared, "transform_sha256": hashlib.sha256(_canonical(declared).encode()).hexdigest()}


def source_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _scope(prefix: str | None) -> tuple[str, list[Any]]:
    return ("AND starts_with(e.event_id, ?)", [prefix]) if prefix else ("", [])


def _set_digest(conn, sql: str, params: list[Any]) -> str:
    """SHA-256 over the sorted per-row hashes of ``sql`` (one text column), bucketed so it stays bounded."""
    buckets = conn.execute(f"""WITH r AS ({sql})
        SELECT left(h, 2) AS b, count(*), sha256(string_agg(h, '' ORDER BY h)) FROM r GROUP BY b ORDER BY b""",
                           params).fetchall()
    digest = hashlib.sha256()
    for b, n, h in buckets:
        digest.update(f"{b}:{n}:{h};".encode())
    return digest.hexdigest()


def content_digest(conn, log_id: str, prefix: str | None = None) -> str:
    """Order-independent SHA-256 over every stored event and, separately, every event-object link."""
    where, extra = _scope(prefix)
    events = _set_digest(conn, f"""SELECT sha256(concat_ws(chr(31), e.event_id, e.activity,
            strftime(e.ts, '%Y-%m-%dT%H:%M:%S.%f'), e.resource, e.attributes)) AS h
        FROM events e WHERE e.log_id = ? {where}""", [log_id, *extra])
    link_scope = "AND starts_with(l.event_id, ?)" if prefix else ""
    links = _set_digest(conn, f"""SELECT sha256(concat_ws(chr(31), l.event_id, l.object_id, l.qualifier)) AS h
        FROM event_objects l WHERE l.log_id = ? {link_scope}""", [log_id, *extra])
    return hashlib.sha256(f"owg.event-log-content.v1:{events}:{links}".encode()).hexdigest()


def content_digest_v2(conn, log_id: str, prefix: str | None = None) -> str:
    """Delimiter-safe digest over all stored event fields and event-object links.

    This is additive: v1 remains stable for existing saved provenance references.
    JSON arrays encode field boundaries and NULLs unambiguously, unlike v1's
    concat_ws(chr(31), ...) representation of untrusted source strings.
    """
    where, extra = _scope(prefix)
    events = _set_digest(conn, f"""SELECT sha256(to_json([e.event_id, e.activity,
            strftime(e.ts, '%Y-%m-%dT%H:%M:%S.%f'), e.resource, e.attributes])) AS h
        FROM events e WHERE e.log_id = ? {where}""", [log_id, *extra])
    link_scope = "AND starts_with(l.event_id, ?)" if prefix else ""
    links = _set_digest(conn, f"""SELECT sha256(to_json([l.event_id, l.object_id, l.qualifier])) AS h
        FROM event_objects l WHERE l.log_id = ? {link_scope}""", [log_id, *extra])
    return hashlib.sha256(f"owg.event-log-content.v2:{events}:{links}".encode()).hexdigest()


def source_identity_digest(conn, log_id: str, prefix: str | None = None) -> str:
    """Separate key/case-map digest; existing event-content digests stay unchanged."""
    where, extra = _scope(prefix)
    return _set_digest(conn, f'''SELECT sha256(to_json([k.source_namespace,k.source_event_id,
          n.case_namespace,l.object_id])) AS h FROM event_source_keys k
        JOIN events e ON e.log_id=k.log_id AND e.event_id=k.event_id
        JOIN event_source_namespaces n ON n.log_id=k.log_id AND n.source_namespace=k.source_namespace
        JOIN event_objects l ON l.log_id=k.log_id AND l.event_id=k.event_id
        WHERE k.log_id=? {where}''', [log_id, *extra])


def diagnostics(conn, log_id: str, prefix: str | None = None, *,
                reference_at: datetime | None = None,
                reference_basis: str = "current_query") -> dict[str, Any]:
    """Counts-only snapshot; future timestamps are compared to an explicit UTC instant."""
    if reference_basis not in ("current_query", "report_created"):
        raise ValueError("unknown future-event reference basis")
    if reference_at is None:
        reference_at = datetime.now(timezone.utc)
    if reference_at.tzinfo is None:
        raise ValueError("future-event reference must be timezone-aware")
    reference_at = reference_at.astimezone(timezone.utc)
    where, extra = _scope(prefix)
    params = [log_id, *extra]
    conn.execute(f"CREATE OR REPLACE TEMP TABLE qe AS SELECT e.* FROM events e WHERE e.log_id = ? {where}", params)
    events, first, last, no_resource, future = conn.execute(
        "SELECT count(*), min(ts), max(ts), count(*) FILTER (WHERE resource = ''), count(*) FILTER (WHERE ts > ?) FROM qe",
        [reference_at.replace(tzinfo=None)]).fetchone()
    conn.execute("""CREATE OR REPLACE TEMP TABLE qlinks AS SELECT eo.event_id, eo.object_id, eo.qualifier, o.object_type
        FROM event_objects eo JOIN qe USING (event_id)
        JOIN objects o ON o.log_id = eo.log_id AND o.object_id = eo.object_id WHERE eo.log_id = ?""", [log_id])
    unlinked = conn.execute("SELECT count(*) FROM qe WHERE event_id NOT IN (SELECT event_id FROM qlinks)").fetchone()[0]
    # A source event may link to the same object with multiple qualifiers.
    # Case sizes and timestamp ties count distinct events, not relationship rows.
    notions = conn.execute("""SELECT object_type, count(*) AS cases,
          count(*) FILTER (WHERE n = 1) AS single_event_cases FROM (
            SELECT object_type, object_id, count(DISTINCT event_id) AS n
            FROM qlinks GROUP BY object_type, object_id)
        GROUP BY object_type ORDER BY cases DESC, object_type""").fetchall()
    tied_events, tied_cases = conn.execute("""SELECT coalesce(sum(n), 0), count(DISTINCT object_id) FROM (
          SELECT l.object_id, q.ts, count(DISTINCT l.event_id) AS n
          FROM qlinks l JOIN qe q USING (event_id)
          GROUP BY 1, 2 HAVING count(DISTINCT l.event_id) > 1)""").fetchone()
    # Duplicate diagnostics compare stored content and qualified links, not just
    # same-time activity/case coincidence. Event IDs themselves are excluded.
    duplicates = conn.execute("""SELECT coalesce(sum(n - 1), 0) FROM (SELECT count(*) AS n FROM (
          SELECT q.event_id, q.activity, q.ts, q.resource, q.attributes,
                 list(to_json([l.object_id, l.qualifier])::VARCHAR
                      ORDER BY l.object_id, l.qualifier)
                      FILTER (WHERE l.object_id IS NOT NULL) AS links
          FROM qe q LEFT JOIN qlinks l USING (event_id) GROUP BY 1, 2, 3, 4, 5)
        GROUP BY activity, ts, resource, attributes, links HAVING count(*) > 1)""").fetchone()[0]
    # A bounded top-N list must disclose omitted keys; otherwise readers may
    # mistake an incomplete list for all available source attributes. The
    # window count runs over grouped keys before LIMIT, without a second scan.
    attributes = conn.execute(f"""SELECT j.key,
          count(*) FILTER (WHERE j.type <> 'NULL' AND j.value <> '""') AS populated,
          count(*) OVER () AS total_keys
        FROM qe, json_each(qe.attributes) j
        GROUP BY j.key ORDER BY populated DESC, j.key LIMIT {MAX_ATTRIBUTES_REPORTED}""").fetchall()
    attribute_total_keys = int(attributes[0][2]) if attributes else 0
    return {
        "events": events,
        "first_event_at": first.isoformat() + "Z" if first else None,
        "last_event_at": last.isoformat() + "Z" if last else None,
        "case_notions": [{"object_type": t, "cases": c, "single_event_cases": s} for t, c, s in notions],
        "events_without_case": unlinked,
        "events_without_resource": no_resource,
        "duplicate_events": int(duplicates),
        "events_sharing_a_timestamp_in_a_case": int(tied_events),
        "cases_with_shared_timestamps": tied_cases,
        "tied_order": "events with the same time in a case are ordered by event identifier, not by observation",
        # Preserve the legacy field while disclosing its actual comparison basis.
        # In a current snapshot the comparison is at query time, not import time.
        "events_dated_after_import": future,
        "events_dated_after_reference": future,
        "future_event_reference": {"at": reference_at.isoformat().replace("+00:00", "Z"),
                                   "basis": reference_basis},
        "attribute_coverage": [{"attribute": k, "events_with_value": n, "events_without_value": events - n}
                               for k, n, _ in attributes],
        "attribute_coverage_total_keys": attribute_total_keys,
        "attribute_coverage_truncated": attribute_total_keys > len(attributes),
    }


def record(conn, log_id: str, *, batch: int, kind: str, source_format: str, mapping: dict[str, Any] | None,
           upload: Path, rows_accepted: int, rows_rejected: int, rejection_reasons: dict[str, int] | None,
           duplicates_skipped: int | None = None, prefix: str | None = None,
           kept: dict[str, int] | None = None, source_identity: dict[str, Any] | None = None) -> dict[str, Any]:
    """Store the report for one import (batch 0) or appended batch, inside the caller's transaction."""
    created_at = datetime.now(timezone.utc)
    rejection_reasons = dict(rejection_reasons) if rejection_reasons is not None else None
    assumed = (rejection_reasons or {}).pop("timestamps_with_assumed_offset", None)
    report = {
        "format": "openworkgraph.event-log-quality.v1",
        "batch": batch, "kind": kind,
        "created_at": created_at.isoformat().replace("+00:00", "Z"),
        "transform": transform(source_format, mapping),
        "source_sha256": source_digest(upload),
        "content_sha256": content_digest(conn, log_id, prefix),
        "content_sha256_v2": content_digest_v2(conn, log_id, prefix),
        "rows": {"accepted": rows_accepted, "rejected": rows_rejected,
                 "rejected_by_reason": rejection_reasons,
                 "reason_counts_can_overlap": bool(rejection_reasons),
                 "rejected_rows_retained": bool(kept and kept.get("retained")),
                 **({"timestamps_with_assumed_offset": assumed} if assumed is not None else {}),
                 **({"rejected_rows_kept_for_review": kept.get("retained", 0),
                     "rejected_rows_over_review_limit": kept.get("not_retained", 0)} if kept is not None else {}),
                 **({"duplicates_skipped_on_append": duplicates_skipped} if duplicates_skipped is not None else {})},
        "diagnostics": diagnostics(conn, log_id, prefix, reference_at=created_at,
                                   reference_basis="report_created"),
        "completeness": {"historical_completeness": "unknown", "note": COMPLETENESS_NOTE},
    }
    if rejection_reasons is None:
        report["rows"]["rejected_by_reason_note"] = ("This reader reports only the total of rejected entries; "
                                                     "reasons are not broken down.")
    if source_identity is not None:
        report['source_identity']=dict(source_identity)
        report['source_identity_map_sha256']=source_identity_digest(conn,log_id,prefix)
    report.update(workspace.object_relationships.metadata(conn,log_id,include_digest=True))
    conn.execute("INSERT INTO log_quality VALUES (?, ?, ?, ?)",
                 [log_id, batch, created_at.replace(tzinfo=None), _canonical(report)])
    return report


def quality(log_id: str) -> dict[str, Any]:
    """Stored reports, oldest first, and the current whole-log diagnostics and content digest."""
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            info = workspace._check_log(conn, log_id)
            reports = [json.loads(r[0]) for r in conn.execute(
                "SELECT report FROM log_quality WHERE log_id = ? ORDER BY batch", [log_id]).fetchall()]
            batches = [0] + [r[0] for r in conn.execute(
                "SELECT batch FROM log_appends WHERE log_id = ? ORDER BY batch", [log_id]).fetchall()]
            current = diagnostics(conn, log_id)
            digest = content_digest(conn, log_id)
            digest_v2 = content_digest_v2(conn, log_id)
            relationship_metadata=workspace.object_relationships.metadata(conn,log_id,include_digest=True)
            identity_digest = ({'source_identity_map_sha256': source_identity_digest(conn,log_id)}
                               if info.get('source_identity') else {})
        finally:
            conn.close()
    present = {r["batch"] for r in reports}
    # Logs imported before reports existed, or whose report could not be written, have none.
    return {"log": info, "reports": reports, "batches_without_report": [b for b in batches if b not in present],
            "current": {"content_sha256": digest, "content_sha256_v2": digest_v2,
                        "diagnostics": current, **identity_digest, **relationship_metadata,
                        "completeness": {"historical_completeness": "unknown", "note": COMPLETENESS_NOTE}},
            **workspace.DESCRIPTIVE_ONLY}


__all__ = ["quality", "record", "transform", "content_digest", "content_digest_v2", "diagnostics", "MAPPING_VERSION"]
