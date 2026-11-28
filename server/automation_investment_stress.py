"""Snapshot-pinned finite investment stress review; no persistence or source calls."""
from shared import phased_investment_stress as stress
from . import automation_investment_case as investment
from . import automation_benefits as benefits


def evaluate(idea_id, payload):
    if (not isinstance(payload, dict) or set(payload) != {"investment", "scenarios", "funding_limit", "confirm"}
            or payload["confirm"] is not True):
        raise benefits.BenefitError("confirm exact investment and common stress assumptions")
    spec, snapshot, selected, _, delta, _, _ = investment._review_basis(idea_id, payload["investment"])
    try:
        result = stress.review(spec, delta, payload["scenarios"], payload["funding_limit"])
    except ValueError as exc:
        raise benefits.BenefitError(str(exc)) from None
    if investment._snapshot(idea_id)["snapshot_sha256"] != snapshot["snapshot_sha256"]:
        raise benefits.BenefitError("retained measurements changed during review; reload")
    return {**result, "idea_id": idea_id, "snapshot_sha256": snapshot["snapshot_sha256"],
            "selected_measurement_ids": sorted(selected), **investment.CLAIMS}
