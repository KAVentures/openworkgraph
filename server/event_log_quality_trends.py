"""Counts-only, comparable batch-quality trends for locally stored event logs.

This derives a reviewable series from immutable import/append quality reports.
It never reads source events, reveals sample values, or infers that an export
contains every event in the source system. It is additive to the authenticated
local quality endpoint, not a new publication or execution surface.
"""
from __future__ import annotations

from typing import Any

FORMAT = "openworkgraph.event-log-quality-trends.v1"
METRICS = (
    "rejected_input_rows",
    "duplicates_skipped_on_append",
    "events_without_resource",
    "events_sharing_timestamp",
    "future_dated_events",
)
# Each report compares future timestamps with its own creation time. A delta
# between different creation instants is not a comparable quality trend.
NONCOMPARABLE_TIME_METRICS = frozenset({"future_dated_events"})


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _rate(numerator: int | None, denominator: int | None) -> dict[str, Any]:
    valid = (numerator is not None and denominator is not None and denominator > 0
             and numerator <= denominator)
    return {
        "count": numerator,
        "denominator": denominator,
        "rate": round(numerator / denominator, 6) if valid else None,
    }


def _metrics(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = report.get("rows") if isinstance(report.get("rows"), dict) else {}
    diagnostics = report.get("diagnostics") if isinstance(report.get("diagnostics"), dict) else {}
    accepted, rejected = _count(rows.get("accepted")), _count(rows.get("rejected"))
    batch = _count(report.get("batch"))
    skipped = (0 if batch == 0 else _count(rows.get("duplicates_skipped_on_append")))
    input_rows = (accepted + rejected + skipped
                  if accepted is not None and rejected is not None and skipped is not None else None)
    events = _count(diagnostics.get("events"))
    future = diagnostics.get("events_dated_after_reference",
                             diagnostics.get("events_dated_after_import"))
    return {
        "rejected_input_rows": _rate(rejected, input_rows),
        "duplicates_skipped_on_append": _rate(skipped, input_rows),
        "events_without_resource": _rate(_count(diagnostics.get("events_without_resource")), events),
        "events_sharing_timestamp": _rate(
            _count(diagnostics.get("events_sharing_a_timestamp_in_a_case")), events),
        "future_dated_events": _rate(_count(future), events),
    }


def build(reports: list[dict[str, Any]], missing_batches: list[int] | None = None) -> dict[str, Any]:
    """Compute per-batch rates and safe deltas from stored, sample-free reports.

    The input denominator includes retained events, rejected input rows and
    already-present events skipped during append. Retained-event diagnostics
    use their own batch event count. Missing/legacy fields stay unknown.
    """
    missing = sorted(set(b for b in (missing_batches or []) if _count(b) is not None))
    series: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None
    ordered = sorted((r for r in reports if isinstance(r, dict) and _count(r.get("batch")) is not None),
                     key=lambda r: r["batch"])
    for report in ordered:
        batch = report["batch"]
        transform = report.get("transform") if isinstance(report.get("transform"), dict) else {}
        digest = transform.get("transform_sha256")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            digest = None
        metrics = _metrics(report)
        comparison: dict[str, Any] = {
            "previous_batch": previous["batch"] if previous is not None else None,
            "status": "first_report",
            "delta_percentage_points": {key: None for key in METRICS},
        }
        if previous is not None:
            if batch != previous["batch"] + 1:
                comparison["status"] = "missing_intermediate_report"
            elif digest is None or previous["transform_sha256"] is None:
                comparison["status"] = "unknown_transform"
            elif digest != previous["transform_sha256"]:
                comparison["status"] = "transform_changed"
            else:
                comparison["status"] = "same_transform"
                for key in METRICS:
                    if key in NONCOMPARABLE_TIME_METRICS:
                        continue
                    now = metrics[key]["rate"]
                    before = previous["metrics"][key]["rate"]
                    if now is not None and before is not None:
                        comparison["delta_percentage_points"][key] = round((now - before) * 100, 4)
        entry = {
            "batch": batch,
            "kind": report.get("kind") if isinstance(report.get("kind"), str) else None,
            "created_at": report.get("created_at") if isinstance(report.get("created_at"), str) else None,
            "transform_sha256": digest,
            "metrics": metrics,
            "comparison": comparison,
        }
        series.append(entry)
        previous = entry
    return {
        "format": FORMAT,
        "batches": series,
        "batches_without_report": missing,
        "interpretation": {
            "descriptive_only": True,
            "source_completeness_verified": False,
            "comparisons_require_adjacent_batches_and_identical_transform": True,
            "future_dated_rate_is_not_compared_across_different_report_times": True,
            "retained_event_diagnostics_are_within_batch_only": True,
            "rejected_reason_counts_may_overlap": True,
            "rates_are_not_individual_worker_performance": True,
            "no_business_action_authorized": True,
        },
    }


__all__ = ["build", "FORMAT", "METRICS"]
