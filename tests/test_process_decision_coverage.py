"""R21: exact supplied-case coverage for declared DMN-like decision tables."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from shared.process_model import ProcessModel
from shared.process_rule_checks import RuleCase, check_case_rules
from tests.test_process_decision_tables import graph as purchase_graph
from tests.test_process_decision_dependencies import graph as dependency_graph


def _run(model_value, facts, date="2026-10-26", routes=None):
    model = ProcessModel.model_validate(model_value)
    record = {"workflow_id": "demo", "revision": 4, "content_sha256": "a" * 64,
              "process_model": model.model_dump()}
    cases = [RuleCase(case_id=f"case_{i+1}", facts=value,
                      activity_ids=routes[i] if routes else None)
             for i, value in enumerate(facts)]
    return check_case_rules(record, cases, date)


def _summary(report, table_id=None):
    rows = report["decision_table_test_coverage"]
    return next(row for row in rows if row["table_id"] == table_id) if table_id else rows[0]


def test_unique_table_reports_selected_vs_untested_rows_without_claiming_exhaustive_domain():
    report = _run(purchase_graph(), [{"amount": 10}, {"amount": 120},
                                     {}, {"amount": "not-a-number"}])
    summary = _summary(report)
    assert summary["table_id"] == "purchase"
    assert summary["hit_policy"] == "UNIQUE"
    assert summary["cases_checked"] == summary["evaluated_case_count"] == 4
    assert summary["not_evaluated_case_count"] == 0
    assert summary["status_counts"] == {"unique_match": 2, "unknown": 2}
    assert summary["selected_case_count"] == 2
    assert summary["selected_row_count"] == summary["declared_row_count"] == 2
    assert summary["never_selected_row_ids"] == []
    assert [row["selected_cases"] for row in summary["rows"]] == [1, 1]
    assert [row["matched_cases_even_if_ambiguous"] for row in summary["rows"]] == [1, 1]
    assert report["decision_table_test_coverage_basis"] == "caller_supplied_cases_only_not_exhaustive"
    assert summary["exhaustive_input_domain_coverage_verified"] is False
    assert summary["source_facts_authoritativeness_verified"] is False
    assert summary["execution_authorized"] is False
    assert all("facts" not in str(row).lower() for row in summary["rows"])


def test_never_selected_rows_are_explicit_but_not_asserted_unreachable():
    report = _run(purchase_graph(), [{"amount": 15}, {"amount": 60}])
    summary = _summary(report)
    assert summary["selected_case_count"] == 2
    assert summary["selected_row_count"] == 1
    assert summary["never_selected_row_ids"] == ["high"]
    assert summary["rows"][1]["selected_cases"] == 0
    assert summary["rows"][1]["matched_cases_even_if_ambiguous"] == 0


def test_unique_overlap_unknown_and_gap_are_not_counted_as_selected_branches():
    graph = purchase_graph()
    graph["business_rules"][1]["predicate"]["conditions"][0]["path"] = "/other_amount"
    cases = [{"amount": 50, "other_amount": 150}, {"amount": 50},
             {"amount": 200, "other_amount": 50}]
    report = _run(graph, cases)
    summary = _summary(report)
    assert summary["status_counts"] == {"gap": 1, "overlap": 1, "unknown": 1}
    assert summary["selected_case_count"] == summary["selected_row_count"] == 0
    assert summary["never_selected_row_ids"] == ["low", "high"]
    assert [row["matched_cases_even_if_ambiguous"] for row in summary["rows"]] == [2, 1]
    assert all(item["decision_tables"][0]["selected_target_activity_id"] is None
               for item in report["cases"])


def test_first_hit_policy_distinguishes_row_matches_from_actual_selection():
    graph = purchase_graph()
    graph["decision_tables"][0]["hit_policy"] = "FIRST"
    graph["business_rules"][1]["predicate"]["conditions"][0]["expected"] = 0
    report = _run(graph, [{"amount": 50}, {"amount": 150}, {}])
    summary = _summary(report)
    assert summary["hit_policy"] == "FIRST"
    assert summary["status_counts"] == {"first_match": 2, "unknown": 1}
    assert summary["selected_row_count"] == 2
    assert summary["selected_case_count"] == 2
    assert [row["selected_cases"] for row in summary["rows"]] == [1, 1]
    # Both rows match amount=50, but FIRST selects only the first.
    assert [row["matched_cases_even_if_ambiguous"] for row in summary["rows"]] == [1, 2]
    assert summary["never_selected_row_ids"] == []


def test_route_refusals_and_expired_tables_remain_unselected():
    graph = purchase_graph()
    report = _run(graph, [{"amount": 50}, {"amount": 150}],
                  routes=[["choose", "accept"], ["choose", "not-a-node"]])
    summary = _summary(report)
    assert summary["status_counts"] == {
        "not_evaluated_invalid_route": 1, "unique_match": 1,
    }
    assert summary["selected_case_count"] == 1
    assert summary["not_evaluated_case_count"] == 1
    assert summary["never_selected_row_ids"] == ["high"]
    expired = _summary(_run(graph, [{"amount": 50}], date="2027-01-01"))
    assert expired["status_counts"] == {"expired": 1}
    assert expired["selected_case_count"] == 0
    assert expired["evaluated_case_count"] == 0
    graph["decision_tables"][0]["user_confirmed"] = False
    unconfirmed = _summary(_run(graph, [{"amount": 50}]))
    assert unconfirmed["status_counts"] == {"unconfirmed": 1}
    assert unconfirmed["never_selected_row_ids"] == ["low", "high"]


def test_dependency_results_cover_selected_and_blocked_decisions_independently():
    graph = dependency_graph()
    report = _run(graph, [{"score": 720}, {"score": 650}, {}], date="2026-06-01")
    dependent = _summary(report, "approval_table")
    prerequisite = _summary(report, "credit_table")
    assert dependent["status_counts"] == {"dependency_unresolved": 1, "unique_match": 2}
    assert prerequisite["status_counts"] == {"unique_match": 2, "unknown": 1}
    assert dependent["selected_case_count"] == prerequisite["selected_case_count"] == 2
    assert dependent["selected_row_count"] == prerequisite["selected_row_count"] == 2
    assert dependent["not_evaluated_case_count"] == 1
    assert prerequisite["not_evaluated_case_count"] == 0


def test_legacy_models_without_decision_tables_have_exact_same_response_fields():
    graph = purchase_graph()
    graph.pop("decision_tables")
    result = _run(graph, [{"amount": 10}], date=None)
    assert "decision_table_test_coverage" not in result
    assert "decision_table_test_coverage_basis" not in result
    assert "decision_tables" not in result["cases"][0]
    assert "rule_result_counts" in result


def test_ui_renders_data_as_text_and_no_new_fact_transport():
    ui = Path(__file__).resolve().parents[1] / "dashboard" / "process_catalog.js"
    source = ui.read_text(encoding="utf-8")
    assert "result.decision_table_test_coverage||[]" in source
    assert "coverage is descriptive" in source.lower()
    assert "rowList.appendChild(el('li'" in source
    assert "X-OWG-Case-Facts-Authorized" in source
    assert "Check facts against saved rules" in source
    assert "innerHTML" not in source
