"""Deterministic, no-provider-agent tests of the issue #226 benchmark contract."""
import pytest

from scripts.evaluate_record_graph_issue226 import evaluate

REF = "owg:r:" + "a" * 32
WRONG = "owg:r:" + "b" * 32
TAGS = [
    "interleaved", "no_reference", "source_outage", "stale_state",
    "divergent_clocks", "inconsistent_ids", "cross_device",
]


def _cases():
    return [
        {
            "case_id": f"scenario_{i+1:02d}",
            "gold_outcome": "unknown" if tag in {"source_outage", "no_reference"} else "resolved",
            "resource_ref": None if tag == "no_reference" else REF,
            "fold": "holdout" if i >= 4 else "development",
            "stressors": [tag],
        }
        for i, tag in enumerate(TAGS)
    ]


def _runs(*, correct_c=True, owg_ref=REF, fake_logs=False,
          outage_status="unavailable"):
    runs = []
    for case in _cases():
        for rep in (1, 2):
            for arm in ("A", "B", "C"):
                logs = []
                if arm in {"B", "C"} and case["resource_ref"]:
                    logs.append({
                        "origin": "orchestrator_tool_execution",
                        "tool": "source_audit_log_read",
                        "status": outage_status if "source_outage" in case["stressors"] else "success",
                        "source_record_match": True,
                        "source_snapshot_sha256": "a" * 64,
                    })
                if arm == "C" and case["resource_ref"]:
                    logs.append({
                        "origin": "model_self_report" if fake_logs else "orchestrator_tool_execution",
                        "tool": "get_record_graph", "status": "success",
                        "resource_ref": owg_ref,
                    })
                runs.append({
                    "case_id": case["case_id"],
                    "condition": arm,
                    "replicate": rep,
                    "predicted_outcome": (
                        "unresolved" if arm == "C" and not correct_c else case["gold_outcome"]
                    ),
                    "host_tool_logs": logs,
                })
    return runs


def test_matched_a_b_c_valid_only_when_real_matching_owg_calls_are_logged():
    result = evaluate(_cases(), _runs(), minimum_replicates=2)
    assert result["status"] == "evaluable"
    assert result["matched_triplets"] == 14
    assert result["scores"]["C"]["matched_owg_record_calls"] == 12
    assert result["scores"]["B"]["matched_source_log_access_attempts"] == 12
    assert result["scores"]["C"]["accuracy"] == 1
    assert result["superiority_claimed"] is False
    assert result["retrieval_logs_are_collector_provided_not_cryptographically_attested"]


def test_model_claims_owg_usage_but_no_authoritative_tool_receipt():
    r = evaluate(_cases(), _runs(fake_logs=True))
    assert r["status"] == "insufficient_evidence"
    assert r["scores"]["C"]["matched_owg_record_calls"] == 0
    assert r["c_wrong_or_missing_record_retrievals"] == 12


def test_wrong_business_record_reference_is_not_counted_as_retrieval():
    r = evaluate(_cases(), _runs(owg_ref=WRONG))
    assert r["status"] == "insufficient_evidence"
    assert r["c_wrong_or_missing_record_retrievals"] == 12


def test_source_outages_do_not_falsely_fail_matching_or_invent_outcomes():
    r = evaluate(_cases(), _runs(outage_status="error"))
    assert r["status"] == "evaluable"
    assert r["scores"]["C"]["unknown_predictions"] == 4


def test_no_source_log_access_is_explicit_ablated_not_claimed_fair():
    runs = _runs()
    for row in runs:
        if row["condition"] == "C":
            row["host_tool_logs"] = [entry for entry in row["host_tool_logs"]
                                     if entry["tool"] != "source_audit_log_read"]
    r = evaluate(_cases(), runs)
    assert r["status"] == "insufficient_evidence"
    assert r["b_c_source_log_ablation_confounds"] == 12


def test_missing_pairs_cannot_make_agent_seem_better_by_case_selection():
    runs = _runs()
    runs = [row for row in runs if not (
        row["condition"] == "B" and row["replicate"] == 2
        and row["case_id"] == "scenario_01"
    )]
    r = evaluate(_cases(), runs)
    assert r["status"] == "insufficient_evidence"
    assert r["incomplete_pairs"] == [{"case_id": "scenario_01", "replicate": 2}]


def test_duplicate_case_or_run_is_refused():
    with pytest.raises(ValueError):
        evaluate(_cases() + [_cases()[0]], _runs())
    with pytest.raises(ValueError):
        evaluate(_cases(), _runs() + [_runs()[0]])


def test_prediction_and_reference_validation_disallow_invented_record_ids():
    broken = _cases()
    broken[0]["resource_ref"] = "jira/TASK-1"
    with pytest.raises(ValueError):
        evaluate(broken, _runs())
    runs = _runs()
    runs[0]["predicted_outcome"] = "patient-Jane-Doe"
    with pytest.raises(ValueError):
        evaluate(_cases(), runs)


def test_divergent_source_snapshots_make_b_c_comparison_invalid():
    runs = _runs()
    for row in runs:
        if row["condition"] == "C":
            for entry in row["host_tool_logs"]:
                if entry["tool"] == "source_audit_log_read":
                    entry["source_snapshot_sha256"] = "b" * 64
    report = evaluate(_cases(), runs)
    assert report["status"] == "insufficient_evidence"
    assert report["b_c_source_log_ablation_confounds"] >= 1


def test_missing_successful_source_snapshot_is_not_validated_by_tool_name():
    runs = _runs()
    for row in runs:
        for entry in row["host_tool_logs"]:
            if entry["tool"] == "source_audit_log_read":
                entry.pop("source_snapshot_sha256", None)
    report = evaluate(_cases(), runs)
    assert report["status"] == "insufficient_evidence"
