"""R21 synthetic boundary probes: sampled numeric paths, not actual business facts."""
from __future__ import annotations

from copy import deepcopy

from shared.process_decision_boundary_probes import FORMAT, MAX_PROBES, probe_table, probe_tables
from shared.process_model import ProcessModel
from shared.process_rule_checks import RuleCase, check_case_rules
from tests.test_process_decision_tables import graph as purchase_graph


def model(*, low="lt", high="gte", first=False):
    value = purchase_graph()
    value["business_rules"][0]["predicate"]["conditions"][0]["operator"] = low
    value["business_rules"][1]["predicate"]["conditions"][0]["operator"] = high
    if first:
        value["decision_tables"][0]["hit_policy"] = "FIRST"
    return ProcessModel.model_validate(value)


def inspect(value, date="2026-10-26"):
    return probe_tables(value, as_of_date=date)[0]


def test_numeric_partition_hits_both_sides_and_exact_equality_without_fake_gaps():
    result = inspect(model())
    assert result["format"] == FORMAT
    assert result["table_id"] == "purchase"
    assert result["status"] == "sampled"
    assert "fact_path" not in result
    assert 3 <= result["sampled_numeric_points"] <= MAX_PROBES
    assert sum(result["status_counts"].values()) == result["sampled_numeric_points"]
    assert result["status_counts"].get("unique_match") == result["sampled_numeric_points"]
    assert result["gap_witnesses"] == []
    assert result["overlap_witnesses"] == []
    assert result["source_case_facts_used"] is False
    assert result["actual_business_cases_evaluated"] is False
    assert result["whole_numeric_domain_coverage_verified"] is False
    assert result["execution_authorized"] is False


def test_gap_exactly_on_boundary_has_explicit_hypothetical_witness():
    result = inspect(model(low="lt", high="gt"))
    assert result["status"] == "sampled"
    assert result["status_counts"]["gap"] >= 1
    assert {"synthetic_value": 100, "status": "gap"} in result["gap_witnesses"]
    assert result["status_counts"]["unique_match"] >= 2
    assert result["unsampled_numeric_regions"] == 0


def test_unique_overlap_exactly_on_boundary_refuses_to_select_branch():
    result = inspect(model(low="lte", high="gte"))
    assert result["status_counts"]["overlap"] >= 1
    assert {"synthetic_value": 100, "status": "overlap"} in result["overlap_witnesses"]
    assert result["first_precedence_competition_count"] == 0


def test_first_reports_masked_competition_separate_from_status_counts():
    result = inspect(model(low="lte", high="gte", first=True))
    assert result["hit_policy"] == "FIRST"
    assert result["status_counts"]["first_match"] == result["sampled_numeric_points"]
    assert result["status_counts"].get("overlap", 0) == 0
    assert result["first_precedence_competition_count"] >= 1
    assert {"synthetic_value": 100, "status": "first_match"} in result["first_precedence_competition_witnesses"]
    assert sum(result["status_counts"].values()) == result["sampled_numeric_points"]


def test_unconfirmed_date_inactive_and_dependency_do_not_claim_analysis():
    m = model()
    assert inspect(m, date=None)["reason"] == "explicit_effective_date_required"
    assert inspect(m, date="2025-12-31")["reason"] == "table_not_yet_effective"
    assert inspect(m, date="2027-01-01")["reason"] == "table_expired"
    m.decision_tables[0].user_confirmed = False
    assert inspect(m)["reason"] == "unconfirmed"
    assert inspect(m)["sampled_numeric_points"] == 0
    m = model()
    # A real multi-table graph would be validated before this pure helper
    # runs; the explicit requirement itself is sufficient to refuse probes.
    from shared.process_decision_tables import RequiredDecision
    m.decision_tables[0].required_decisions = [
        RequiredDecision(table_id="prior", fact_path="/derived_choice")
    ]
    assert inspect(m)["reason"] == "decision_dependencies_need_real_case_context"


def test_mixed_paths_composite_rules_non_numeric_and_unsupported_pointer_abstain():
    other = purchase_graph()
    other["business_rules"][1]["predicate"]["conditions"][0]["path"] = "/other"
    assert inspect(ProcessModel.model_validate(other))["reason"] == "requires_same_numeric_fact_path"
    compound = purchase_graph()
    compound["business_rules"][0]["predicate"]["conditions"].append(
        {"path": "/approved", "operator": "eq", "expected": True}
    )
    assert inspect(ProcessModel.model_validate(compound))["reason"] == "requires_one_numeric_comparison_per_row"
    text = purchase_graph()
    text["business_rules"][0]["predicate"]["conditions"][0].update(operator="eq", expected=100)
    assert inspect(ProcessModel.model_validate(text))["reason"] == "requires_one_numeric_comparison_per_row"
    array = purchase_graph()
    for rule in array["business_rules"]:
        rule["predicate"]["conditions"][0]["path"] = "/list/0"
    assert inspect(ProcessModel.model_validate(array))["reason"] == "numeric_path_has_unsupported_shape"


def test_nested_object_path_checks_are_internally_generated_without_case_values():
    m = purchase_graph()
    for rule in m["business_rules"]:
        rule["predicate"]["conditions"][0]["path"] = "/customer/amount"
    report = inspect(ProcessModel.model_validate(m))
    assert report["status"] == "sampled"
    assert report["fact_path"] == "/customer/amount"
    assert report["gap_witnesses"] == []
    assert report["status_counts"]["unique_match"] == report["sampled_numeric_points"]


def test_existing_authorized_case_report_adds_separate_probes_and_preserves_actual_cases():
    m = model()
    record = {"workflow_id": "purchase_process", "revision": 4, "content_sha256": "a" * 64,
              "process_model": m.model_dump()}
    output = check_case_rules(record, [RuleCase(case_id="case_1", facts={"amount": 50})],
                              "2026-10-26")
    assert output["case_count"] == 1
    assert output["cases"][0]["decision_tables"][0]["status"] == "unique_match"
    assert output["decision_table_boundary_probes"][0]["status"] == "sampled"
    assert output["decision_table_boundary_probes_basis"] == "generated_synthetic_numeric_threshold_points_only"
    assert output["decision_table_test_coverage_basis"] == "caller_supplied_cases_only_not_exhaustive"
    assert output["facts_retained"] is False
    assert output["execution_permissions_granted"] is False
    assert "case_1" not in str(output["decision_table_boundary_probes"])


def test_table_free_models_keep_their_exact_previous_response_fields():
    v = purchase_graph()
    v.pop("decision_tables")
    m = ProcessModel.model_validate(v)
    result = check_case_rules(
        {"workflow_id": "no_tables", "revision": 1, "content_sha256": "a" * 64,
         "process_model": m.model_dump()}, [RuleCase(case_id="case_1", facts={"amount": 5})], None
    )
    assert "decision_table_boundary_probes" not in result
    assert "decision_table_boundary_probes_basis" not in result
    assert "decision_table_test_coverage" not in result
