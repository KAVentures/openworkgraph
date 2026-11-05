import pytest

from server.open_work_bound_run_reconciliation import reconcile_bound_run_case_history
from server.open_work_case_declarations import CaseDeclarationError


REF = "owg:w:" + "a" * 32


def provider(state="paid", checks=1, disputes=0):
    events = [{"kind": "check"}] * checks + [{"kind": "dispute"}] * disputes
    return {
        "work_item_ref": REF, "events": events,
        "provider_check_count": checks, "disputed_check_count": disputes,
        "last_observed_provider_state": state,
    }


def human(status="met", count=1):
    return {
        "work_item_ref": REF, "events": [{"kind": "assessment"}] * count,
        "event_count": count, "latest_human_assessed_status": status,
    }


def test_matching_history_does_not_claim_customer_or_agent_success():
    result = reconcile_bound_run_case_history(provider(), human())
    assert result["review_attention"] == []
    assert result["historical_only_requires_fresh_source_recheck"] is True
    assert result["evidence_scope"] == "case_wide_not_run_attributed"
    assert result["customer_outcome_verified"] is False
    assert result["agent_caused_operation_verified"] is False
    assert result["run_case_identity_independently_verified"] is False


@pytest.mark.parametrize("p,h,reason", [
    (provider(state="unknown", checks=0), human(), "provider_check_missing"),
    (provider(state="unknown", checks=1, disputes=1), human(), "provider_check_disputed"),
    (provider(), human(count=0, status="unknown"), "human_assessment_missing"),
    (provider(state="open"), human(), "human_met_without_historical_paid_receipt"),
    (provider(), human(status="not_met"), "historical_paid_human_not_met"),
    (provider(), human(status="unknown"), "historical_paid_human_unknown"),
])
def test_attention_is_conservative(p, h, reason):
    result = reconcile_bound_run_case_history(p, h)
    assert reason in result["review_attention"]
    assert result["customer_outcome_verified"] is False


def test_mismatched_case_ref_is_refused():
    changed = human()
    changed["work_item_ref"] = "owg:w:" + "b" * 32
    with pytest.raises(CaseDeclarationError):
        reconcile_bound_run_case_history(provider(), changed)


@pytest.mark.parametrize("key,value", [
    ("provider_check_count", True),
    ("provider_check_count", -1),
    ("provider_check_count", 3),
    ("disputed_check_count", 3),
    ("last_observed_provider_state", "made_up"),
])
def test_malformed_provider_summary_refused(key, value):
    p = provider()
    p[key] = value
    with pytest.raises(CaseDeclarationError):
        reconcile_bound_run_case_history(p, human())


def test_malformed_human_history_refused():
    h = human()
    h["event_count"] = 2
    with pytest.raises(CaseDeclarationError):
        reconcile_bound_run_case_history(provider(), h)
