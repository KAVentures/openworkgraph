"""R16 portfolio evidence readiness, not realized ROI or an additive savings claim.

Read-only bounded projection of existing local implemented automation ideas and
their owner-declared before/after measurements. Reuses the same per-idea
coverage comparator and exact declared-source overlap detector. Every period
and source claim is treated as user-entered, never independently verified.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import closing
import json
from typing import Any

from . import automation_benefits as benefits
from . import automation_benefit_overlap as overlap
from . import automation_pipeline as pipeline

FORMAT = "openworkgraph.automation-portfolio-benefit-readiness.v1"
MAX_ROWS = pipeline.MAX_IDEAS * benefits.MAX_MEASUREMENTS_PER_IDEA

ACTIONS = {
    "unreadable_evidence": "Repair or remove unreadable measurement records before comparing",
    "missing_baseline": "Record a pre-implementation baseline with declared dates, cases, effort and source",
    "missing_after": "Record a post-implementation measurement with declared dates, cases, effort and source",
    "overlapping_periods": "Resolve overlapping periods within the same idea before pooling exposure",
    "zero_case_period": "Supply nonzero case denominators for the compared periods",
    "cross_idea_overlap_candidate": "Review possible shared cases and duplicated exposure across ideas",
    "missing_source_reference": "Review the provenance of all measurements and add exact declared source references",
    "ready_for_noncausal_comparison": "Review reported before/after effort; independently verify actual business effects",
    "invalid_periods": "Reconcile periods and source completeness before making comparisons",
}


def review() -> dict[str, Any]:
    ideas = pipeline.pipeline("implemented")["ideas"]
    by_id = {idea["idea_id"]: idea for idea in ideas}
    with pipeline._LOCK, benefits._LOCK, closing(pipeline._connect()) as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='idea_measurements'"
        ).fetchone()
        raw = conn.execute(
            """SELECT m.idea_id,m.measurement_id,m.created_at,m.body
               FROM idea_measurements m JOIN ideas i ON i.idea_id=m.idea_id
               WHERE i.state='implemented' ORDER BY m.idea_id,m.measurement_id LIMIT ?""",
            (MAX_ROWS+1,),
        ).fetchall() if exists else []
    truncated = len(raw) > MAX_ROWS
    rows = raw[:MAX_ROWS]
    indexed: dict[str,list[dict]] = defaultdict(list)
    invalid: Counter[str] = Counter()
    missing_refs: Counter[str] = Counter()
    source_kinds: dict[str,set[str]] = defaultdict(set)
    for idea_id, mid, created_at, body in rows:
        if idea_id not in by_id:
            continue
        try:
            parsed = json.loads(body)
            normalized = benefits.Measurement.model_validate(parsed).model_dump(mode="json")
            normalized = benefits._normalize({
                "measurement_id": mid, "recorded_at": created_at, **normalized})
            indexed[idea_id].append(normalized)
            source_kinds[idea_id].add(normalized["source"])
            if not normalized["source_reference"].strip():
                missing_refs[idea_id] += 1
        except (ValueError, TypeError, KeyError, OverflowError):
            invalid[idea_id] += 1

    linked, _, _, overlap_unreadable, _ = overlap._parse(
        [(idea_id, mid, body) for idea_id, mid, _, body in rows])
    candidate_pairs, pairs_truncated = overlap._candidates(linked)
    affected = {id for pair in candidate_pairs
                for id in (pair["first_idea_id"], pair["second_idea_id"])}

    report_rows = []
    for idea in sorted(ideas, key=lambda item: item["idea_id"]):
        idea_id = idea["idea_id"]
        periods = indexed[idea_id]
        baseline_count = sum(p["period"]=="baseline" for p in periods)
        after_count = sum(p["period"]=="after" for p in periods)
        quality = benefits._coverage_adjusted(periods, (idea.get("score") or {}).get(
            "modeled_hours_per_month"))
        if invalid[idea_id]:
            status = "unreadable_evidence"
        elif not baseline_count:
            status = "missing_baseline"
        elif not after_count:
            status = "missing_after"
        elif quality["status"] != "ready":
            status = quality["status"] if quality["status"] in ACTIONS else "invalid_periods"
        elif idea_id in affected or pairs_truncated or truncated or overlap_unreadable:
            status = "cross_idea_overlap_candidate" if idea_id in affected else "invalid_periods"
        elif missing_refs[idea_id]:
            status = "missing_source_reference"
        else:
            status = "ready_for_noncausal_comparison"
        comparison = quality.get("comparison") or {}
        report_rows.append({
            "idea_id": idea_id, "title": idea["title"],
            "implemented_state": "implemented",
            "baseline_periods": baseline_count, "after_periods": after_count,
            "unreadable_measurements": invalid[idea_id],
            "missing_source_references": missing_refs[idea_id],
            "declared_source_kinds": sorted(source_kinds[idea_id]),
            "within_idea_coverage_status": quality["status"],
            "cross_idea_overlap_candidate": idea_id in affected,
            "readiness": status, "next_review_action": ACTIONS[status],
            "reported_effort_change_hours_per_month_at_after_volume":
                comparison.get("hours_per_month_from_per_case_change_at_after_volume")
                if status == "ready_for_noncausal_comparison" else None,
            "causal_effect_verified": False,
            "reported_change_is_savings": False,
        })
    counts = Counter(row["readiness"] for row in report_rows)
    return {
        "format": FORMAT,
        "implemented_ideas_reviewed": len(report_rows),
        "ideas_by_readiness": dict(sorted(counts.items())),
        "ideas": report_rows,
        "candidate_overlap_pairs": len(candidate_pairs),
        "candidate_overlap_pairs_truncated": pairs_truncated,
        "measurements_scanned": len(rows),
        "unreadable_measurements_in_overlap_scan": overlap_unreadable,
        "rows_truncated": truncated,
        "comparison_complete": not (truncated or pairs_truncated or overlap_unreadable or any(invalid.values())),
        "reported_source_identity_verified": False,
        "same_cases_verified": False,
        "causal_effect_verified": False,
        "realized_savings_verified": False,
        "portfolio_savings_summed": False,
        "execution_authorized": False,
        "source_reference_values_returned": False,
        "interpretation": (
            "Portfolio-wide reviewer checklist based on entered local measurements, not "
            "independent receipts or an ROI claim. Per-idea exposure comparison is suppressed "
            "when measurement coverage is invalid, sources are missing or overlapping source "
            "references need review. The absence of an exact overlap candidate does not verify "
            "that cases are distinct, and no savings or currencies are summed."),
    }


__all__ = ["review", "FORMAT", "ACTIONS"]
