"""Independent synthetic acceptance and report-gate regressions."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from scripts import enterprise_ingestion_acceptance as acceptance


def make_export(path: Path, cases: int, mutate=None):
    rows = []
    for case in range(cases):
        for step in range(4):
            event, case_id, activity, stamp, resource, region = acceptance.synthetic_row(case, step)
            rows.append([acceptance.PROVIDERS[step // 2], event, acceptance.CASE_NAMESPACE,
                         case_id, activity, stamp, resource, json.dumps({"region": region})])
    if mutate is not None:
        mutate(rows)
    with path.open("w", encoding="utf-8", newline="") as out:
        writer = csv.writer(out)
        writer.writerow(acceptance.EXPORT_HEADER)
        writer.writerows(reversed(rows))


def test_fixture_uses_literal_collisions_across_separate_namespaces(tmp_path):
    front, back = tmp_path / "front.csv", tmp_path / "back.csv"
    for path, provider in zip((front, back), acceptance.PROVIDERS):
        acceptance.write_fixture(path, 3, provider)
    with front.open(newline="") as src:
        first = list(csv.DictReader(src))
    with back.open(newline="") as src:
        second = list(csv.DictReader(src))
    assert len(first) == len(second) == 6
    assert {r["event"] for r in first} == {r["event"] for r in second}
    assert {r["activity"] for r in first} == {"Open", "Review", "Escalate"}
    assert {r["activity"] for r in second} == {"Approve", "Close"}
    assert acceptance.mapping(acceptance.PROVIDERS[0])["case_namespace"] == (
        acceptance.mapping(acceptance.PROVIDERS[1])["case_namespace"])


def test_exact_audit_is_order_independent(tmp_path):
    path = tmp_path / "export.csv"
    make_export(path, 3)
    assert acceptance.audit_export(path, 3) == dict.fromkeys(acceptance.PROVIDERS, 6)


@pytest.mark.parametrize(("mutation", "reason"), [
    (lambda rows: rows.pop(), "export_missing_event"),
    (lambda rows: rows.append(rows[0]), "export_duplicate"),
    (lambda rows: rows[0].__setitem__(0, "other_provider"), "export_namespace"),
    (lambda rows: rows[0].__setitem__(1, "e0:01"), "export_id_bounds"),
    (lambda rows: rows[0].__setitem__(2, "wrong_case_namespace"), "export_payload"),
    (lambda rows: rows[0].__setitem__(3, "c1"), "export_payload"),
    (lambda rows: rows[0].__setitem__(4, "Approve"), "export_payload"),
    (lambda rows: rows[0].__setitem__(5, "2026-01-01T00:00:00"), "export_timestamp"),
    (lambda rows: rows[0].__setitem__(6, "other_role"), "export_payload"),
    (lambda rows: rows[0].__setitem__(7, '{"region":"south"}'), "export_attributes"),
])
def test_export_audit_refuses_corrupt_or_incomplete_populations(tmp_path, mutation, reason):
    path = tmp_path / "export.csv"
    make_export(path, 2, mutation)
    with pytest.raises(acceptance.AcceptanceFailure, match=reason):
        acceptance.audit_export(path, 2)


@pytest.mark.parametrize("value", [0, -1, True, 1.5, acceptance.MAX_CASES + 1])
def test_workload_bounds(value):
    with pytest.raises(ValueError):
        acceptance.validate_cases(value)


def test_report_budgets_are_opt_in_and_fail_independently():
    stages = [{"name": "initial_import", "seconds": 2, "rows_per_second": 10,
               "peak_sampled_rss_bytes": 100 * 1024**2},
              {"name": "audit", "seconds": 1, "rows_per_second": 100,
               "peak_sampled_rss_bytes": 200 * 1024**2}]
    assert acceptance.evaluate_budgets(stages) == []
    assert acceptance.evaluate_budgets(stages, max_seconds=3, max_rss_mib=200,
                                       min_import_rate=10) == []
    assert acceptance.evaluate_budgets(stages, max_seconds=2, max_rss_mib=199,
                                       min_import_rate=11) == [
        "total_measured_seconds", "sampled_parent_rss", "initial_import_rate"]
    for bad in (0, -1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            acceptance.evaluate_budgets(stages, max_seconds=bad)


def test_missing_initial_import_cannot_pass_rate_gate():
    assert acceptance.evaluate_budgets([], min_import_rate=1) == ["initial_import_rate"]


def test_small_real_workspace_hard_exit_retry_and_roundtrip(tmp_path, monkeypatch):
    from server import event_log_workspace as workspace
    live = tmp_path / "not-the-test-workspace"
    live.mkdir()
    sentinel = live / "do-not-touch"
    sentinel.write_text("sentinel")
    monkeypatch.setattr(workspace, "DATA_DIR", live)
    report = acceptance.run_acceptance(3, crash_timeout=90)
    assert report["status"] == "passed", report
    assert workspace.DATA_DIR == live
    assert list(live.iterdir()) == [sentinel] and sentinel.read_text() == "sentinel"
    assert report["synthetic_only"] is True
    assert report["live_provider_acceptance"] is False
    assert report["distributed_concurrency_tested"] is False
    assert report["cancellation_tested"] is False
    assert report["outcome"]["exact_events"] == 12
    assert report["outcome"]["exact_cases"] == 3
    assert report["outcome"]["append_history_batches"] == 3
    assert report["outcome"]["source_events"] == dict.fromkeys(acceptance.PROVIDERS, 6)
    assert [s["name"] for s in report["stages"]] == [
        "fixture_generation", "initial_import", "hard_exit_and_reopen", "concurrent_retry",
        "historical_retry", "conflict_rollback", "mine_exact_population", "identity_export",
        "independent_export_audit", "export_roundtrip"]
    assert all(s["status"] == "passed" and s["seconds"] >= 0 for s in report["stages"])
    json.dumps(report, allow_nan=False)
