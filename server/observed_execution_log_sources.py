"""Lightweight metadata for retained observed-execution snapshots.

Ordinary event-log queries must not load task reconstruction or raw history.
A malformed manifest is visibly unavailable; the saved dataset remains
analysable and never becomes an appendable ordinary log by accident.
"""
from __future__ import annotations

import json
import re
from typing import Any

FORMAT = "openworkgraph.observed-execution-log.v1"
SCHEMA = """CREATE TABLE IF NOT EXISTS observed_execution_log_sources (
    log_id VARCHAR PRIMARY KEY,
    snapshot_sha256 VARCHAR NOT NULL,
    source_report VARCHAR NOT NULL
);"""
_SHA = re.compile(r"[a-f0-9]{64}")


def read_manifest(conn, log_id: str) -> tuple[bool, dict[str, Any] | None]:
    row = conn.execute(
        "SELECT snapshot_sha256, source_report FROM observed_execution_log_sources WHERE log_id = ?",
        [log_id],
    ).fetchone()
    if row is None:
        return False, None
    try:
        report = json.loads(row[1])
        if not isinstance(report, dict) or report.get("format") != FORMAT:
            return True, None
        if not isinstance(row[0], str) or not _SHA.fullmatch(row[0]) or report.get("snapshot_sha256") != row[0]:
            return True, None
        counts = [report.get(key) for key in ("case_count", "event_count", "timestamp_tie_groups")]
        if any(type(value) is not int or value < 0 for value in counts):
            return True, None
        if not 1 <= counts[0] <= 12 or not 1 <= counts[1] <= 10000 or counts[2] > counts[1]:
            return True, None
        scope = report.get("scope")
        if (not isinstance(scope, dict) or set(scope) - {"execution_ids", "since", "until", "projection"}
                or not isinstance(scope.get("execution_ids"), list)
                or scope.get("projection", "actions") not in {"actions", "actions_and_navigation"}):
            return True, None
        if any(scope.get(key) is not None and not isinstance(scope[key], str) for key in ("since", "until")):
            return True, None
        cases = report.get("cases")
        if not isinstance(cases, list) or len(cases) != counts[0] or len(scope["execution_ids"]) != counts[0]:
            return True, None
        if any(not isinstance(case, dict) for case in cases):
            return True, None
        ids = scope["execution_ids"]
        if (any(not isinstance(value, str) or not re.fullmatch(r"execution:[a-f0-9]{16}", value) for value in ids)
                or len(set(ids)) != len(ids)
                or [case.get("execution_id") for case in cases] != ids):
            return True, None
        if any(type(case.get("events")) is not int or case["events"] < 1
               or type(case.get("timestamp_tie_groups")) is not int or case["timestamp_tie_groups"] < 0
               or case.get("actor_kind") not in {"human", "agent"} for case in cases):
            return True, None
        if sum(case["events"] for case in cases) != counts[1] or sum(case["timestamp_tie_groups"] for case in cases) != counts[2]:
            return True, None
        if (report.get("capture_complete") is not False or report.get("business_case_identity_verified") is not False
                or report.get("business_outcomes_verified") is not False
                or report.get("source_limit_reached") is not False
                or report.get("sensor_observations_not_deduplicated_actions") is not True):
            return True, None
        return True, report
    except (ValueError, TypeError, KeyError, RecursionError):
        return True, None


def metadata(conn, log_id: str) -> dict[str, Any]:
    exists, report = read_manifest(conn, log_id)
    if not exists:
        return {}
    result = {
        "manifest_available": report is not None,
        "business_case_identity_verified": False,
        "capture_complete": False,
        "independent_retention": True,
    }
    if report is not None:
        result.update(snapshot_sha256=report["snapshot_sha256"],
                      original_case_count=report["case_count"],
                      original_event_count=report["event_count"],
                      timestamp_tie_groups=report["timestamp_tie_groups"])
    return {"observed_execution_snapshot": result}
