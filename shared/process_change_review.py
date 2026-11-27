"""Integrated change-review evidence across structural, policy and case checks."""
from __future__ import annotations

from . import process_policy_impact as impact
from . import process_policy_regression as regression
from . import process_token_assurance as tokens
from .process_model import ProcessModel

FORMAT = "openworkgraph.integrated-process-change-review.v1"


def _structural(packet):
    report = tokens.analyze_saved(packet)
    keys = ("reference", "complete", "incomplete_reason", "states_discovered",
            "firings_examined", "completion_reachable", "every_reachable_marking_can_complete",
            "known_deadlock_count", "deadlock_count_exact", "noncompletable_marking_count",
            "shortest_completion", "deadlock_examples")
    return {key: report[key] for key in keys}


def _affected_tables(model, side):
    # Include downstream decisions as well as explicit dependency-linked tables.
    # A downstream graph path is a potential impact, not observed execution.
    activities = {row["activity_id"] for row in side["downstream_activity_witnesses"]}
    explicit = {row["table_id"] for row in side["decision_dependency_witnesses"]}
    return explicit | {t.id for t in model.decision_tables if t.decision_activity_id in activities}


def review(before, after, payload):
    report = regression.compare(before, after, payload)
    delta = impact.compare(before, after)
    old, new = (ProcessModel.model_validate(p["process_model"]) for p in (before, after))
    old_structural, new_structural = (_structural(p) for p in (before, after))
    old_affected = _affected_tables(old, delta["before_impact"])
    new_affected = _affected_tables(new, delta["after_impact"])
    expected = {row["table_id"]: row for row in report["per_table"]}
    coverage = []
    for ident in sorted(old_affected | new_affected):
        tally = expected.get(ident, {})
        count = tally.get("reviewed", 0)
        coverage.append({"table_id": ident,
                         "affected_before": ident in old_affected,
                         "affected_after": ident in new_affected,
                         "reviewed_case_count": count,
                         "unreviewed_case_count": report["case_count"] - count,
                         "after_matches": tally.get("after_matches", 0),
                         "after_mismatches": count - tally.get("after_matches", 0)})
    missing = sum(row["unreviewed_case_count"] for row in coverage)
    findings = []
    def finding(code, state, detail):
        findings.append({"code": code, "status": state, "detail": detail})
    if new_structural["known_deadlock_count"] or new_structural["completion_reachable"] is False or new_structural["every_reachable_marking_can_complete"] is False:
        finding("after_structural_completion", "finding",
                "At least one structural completion problem is established within the marking analysis.")
    elif not new_structural["complete"]:
        finding("after_structural_completion", "unknown",
                "The bounded marking exploration is incomplete; absence of a witness is not a pass.")
    elif new_structural["every_reachable_marking_can_complete"] is True:
        finding("after_structural_completion", "checked",
                "Every explored marking can complete structurally; business rules are not evaluated here.")
    else:
        finding("after_structural_completion", "unknown", "Structural completion was not established.")
    if report["after_matches"] != report["reviewed_expectations"]:
        finding("reviewed_after_expectations", "finding", "One or more reviewed after expectations did not match.")
    else:
        finding("reviewed_after_expectations", "checked", "All supplied reviewed after expectations matched.")
    finding("affected_decision_case_coverage",
            "unknown" if missing else "checked" if coverage else "not_applicable",
            "Potentially affected decisions lack reviewed expectations in some supplied cases." if missing else
            "Every potentially affected decision has an expectation in each supplied case." if coverage else
            "No potentially affected decision tables were identified by the policy impact analysis.")
    finding("unreviewed_changed_behavior",
            "finding" if report["unreviewed_changed_table_case_pairs"] else "checked",
            "Changed decision behavior was observed without a reviewed expectation." if report["unreviewed_changed_table_case_pairs"] else
            "No unreviewed changed decision behavior was observed in the supplied cases.")
    if delta["after_impact"]["unreferenced_changed_rule_ids"] or delta["before_impact"]["unreferenced_changed_rule_ids"]:
        finding("unreferenced_rule_changes", "unknown",
                "Changed rules exist outside the mapped graph/table checks and require separate review.")
    if delta["manual_review_changes"] or delta["outcome_expectation_changes"]:
        finding("manual_or_outcome_changes", "unknown",
                "Manual review or source-outcome expectations changed and are not established by this assessment.")
    return {**report, "change_review": {
        "format": FORMAT, "before": report["before"], "after": report["after"],
        "structural_before": old_structural, "structural_after": new_structural,
        "potential_policy_impact": delta,
        "affected_decision_coverage": coverage,
        "unreviewed_affected_table_case_pairs": missing,
        "findings": findings,
        "assessment": ("review_findings" if any(f["status"] == "finding" for f in findings)
                       else "incomplete_review" if any(f["status"] == "unknown" for f in findings)
                       else "no_listed_findings_in_bounded_checks"),
        "independent_approval": False, "execution_authorized": False,
        "policy_compliance_verified": False, "business_outcomes_verified": False,
        "interpretation": ("Combines bounded structural reachability, declared policy impact and finite reviewed-case behavior. "
                           "A checked finding is not a release gate, complete policy test, independent approval, "
                           "source verification or permission to execute.")}}
