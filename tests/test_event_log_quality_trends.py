"""R6 batch-over-batch quality trends: synthetic counts, stored logs and local REST."""
from __future__ import annotations

from io import BytesIO
import json
import re
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_quality as quality
from server import event_log_quality_trends as trends
from server import event_log_workspace as workspace

ROOT = Path(__file__).resolve().parents[1]
MAPPING = {"case_column": "case", "activity_column": "activity",
           "timestamp_column": "time", "resource_column": "who"}
FIRST = ("case,activity,time,who\n"
         "c1,Open,2024-01-01T08:00:00Z,alice\n"
         "c1,Close,2024-01-01T09:00:00Z,alice\n")
SECOND = ("case,activity,time,who\n"
          "c1,Open,2024-01-01T08:00:00Z,alice\n"  # already retained
          "c2,Open,2024-01-02T08:00:00Z,\n"       # no resource
          "c2,Close,2024-01-02T09:00:00Z,bob\n"
          "c3,Open,not-a-time,bob\n")            # rejected


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _make():
    return workspace.import_log(BytesIO(FIRST.encode()), name="batch trends",
                                source_format="csv", mapping=MAPPING)["log_id"]


def _append(log, text=SECOND, mapping=MAPPING):
    return workspace.append_log(log, BytesIO(text.encode()), source_format="csv", mapping=mapping)


def test_pure_trends_only_compare_adjacent_identical_transforms():
    def report(batch, digest, *, accepted, rejected, skipped, missing):
        return {"batch": batch, "kind": "append" if batch else "import",
                "created_at": f"2024-01-0{batch + 1}T12:00:00Z",
                "transform": {"transform_sha256": digest},
                "rows": {"accepted": accepted, "rejected": rejected,
                         **({"duplicates_skipped_on_append": skipped} if batch else {})},
                "diagnostics": {"events": accepted, "events_without_resource": missing,
                                "events_sharing_a_timestamp_in_a_case": 0,
                                "events_dated_after_reference": 0}}
    a, b = "a" * 64, "b" * 64
    base = report(0, a, accepted=8, rejected=2, skipped=0, missing=2)
    next_one = report(1, a, accepted=5, rejected=3, skipped=2, missing=2)
    changed = report(2, b, accepted=10, rejected=0, skipped=0, missing=0)
    result = trends.build([changed, next_one, base])
    assert [x["batch"] for x in result["batches"]] == [0, 1, 2]
    assert result["format"] == trends.FORMAT
    first, second, third = result["batches"]
    assert first["comparison"]["status"] == "first_report"
    assert first["metrics"]["rejected_input_rows"] == {"count": 2, "denominator": 10, "rate": 0.2}
    assert second["metrics"]["rejected_input_rows"] == {"count": 3, "denominator": 10, "rate": 0.3}
    assert second["metrics"]["duplicates_skipped_on_append"]["rate"] == 0.2
    assert second["metrics"]["events_without_resource"]["rate"] == 0.4
    assert second["comparison"]["status"] == "same_transform"
    assert second["comparison"]["delta_percentage_points"]["rejected_input_rows"] == 10.0
    assert second["comparison"]["delta_percentage_points"]["events_without_resource"] == 15.0
    assert second["comparison"]["delta_percentage_points"]["future_dated_events"] is None
    assert third["comparison"]["status"] == "transform_changed"
    assert all(x is None for x in third["comparison"]["delta_percentage_points"].values())
    missing = trends.build([base, changed], [1])
    assert missing["batches_without_report"] == [1]
    assert missing["batches"][1]["comparison"]["status"] == "missing_intermediate_report"
    assert all(x is None for x in missing["batches"][1]["comparison"]["delta_percentage_points"].values())


def test_missing_legacy_values_never_become_zero_or_fake_comparisons():
    reports = [{"batch": 0, "rows": {"accepted": 0, "rejected": 0},
                "diagnostics": {"events": 0, "events_without_resource": 0}},
               {"batch": 1, "rows": {"accepted": 3, "rejected": 1},
                "diagnostics": {"events": 3, "events_without_resource": 1}}]
    out = trends.build(reports)
    assert out["batches"][0]["metrics"]["rejected_input_rows"]["rate"] is None
    assert out["batches"][1]["metrics"]["rejected_input_rows"]["rate"] is None  # skipped count unknown
    assert out["batches"][1]["comparison"]["status"] == "unknown_transform"
    assert out["batches"][1]["metrics"]["events_without_resource"]["rate"] == round(1 / 3, 6)
    assert trends.build([], [0, 1])["batches"] == []
    assert trends.build([], [0, 1])["batches_without_report"] == [0, 1]
    assert trends.build([{"batch": -1}, {"batch": True}, {"batch": "1"}])["batches"] == []


def test_import_append_quality_trends_are_visible_and_counts_only():
    log = _make()
    appended = _append(log)
    assert appended["added_events"] == 2
    assert appended["duplicate_events_skipped"] == 1
    assert appended["rejected_events"] == 1
    result = quality.quality(log)
    assert "reports" in result and "current" in result and "batch_trends" in result
    batches = result["batch_trends"]["batches"]
    assert len(batches) == 2
    initial, later = batches
    assert initial["metrics"]["rejected_input_rows"] == {"count": 0, "denominator": 2, "rate": 0.0}
    assert later["metrics"]["rejected_input_rows"] == {"count": 1, "denominator": 4, "rate": 0.25}
    assert later["metrics"]["duplicates_skipped_on_append"] == {"count": 1, "denominator": 4, "rate": 0.25}
    assert later["metrics"]["events_without_resource"] == {"count": 1, "denominator": 2, "rate": 0.5}
    assert later["comparison"]["status"] == "same_transform"
    assert later["comparison"]["delta_percentage_points"]["events_without_resource"] == 50.0
    assert later["comparison"]["delta_percentage_points"]["rejected_input_rows"] == 25.0
    assert result["batch_trends"]["interpretation"]["source_completeness_verified"] is False
    serialized = json.dumps(result["batch_trends"])
    assert "alice" not in serialized and "bob" not in serialized
    # Whole tokens only: hex digests in the report can contain "c1" or "c2" by chance.
    assert not re.search(r"\bc[12]\b", serialized)


def test_changed_mapping_and_missing_batch_report_refuse_deltas():
    log = _make()
    _append(log)
    # A changed resource mapping changes the transform, not the stored source labels.
    third = ("case,activity,time,who\n"
             "c3,Open,2024-01-03T08:00:00Z,alice\n")
    _append(log, third, {**MAPPING, "resource_column": ""})
    result = quality.quality(log)["batch_trends"]
    assert [x["batch"] for x in result["batches"]] == [0, 1, 2]
    assert result["batches"][2]["comparison"]["status"] == "transform_changed"
    assert all(v is None for v in result["batches"][2]["comparison"]["delta_percentage_points"].values())

    conn = workspace._connect()
    try:
        conn.execute("DELETE FROM log_quality WHERE log_id = ? AND batch = 1", [log])
    finally:
        conn.close()
    missing = quality.quality(log)["batch_trends"]
    assert missing["batches_without_report"] == [1]
    assert [x["batch"] for x in missing["batches"]] == [0, 2]
    assert missing["batches"][1]["comparison"]["status"] == "missing_intermediate_report"


def test_authenticated_http_quality_exposes_trends_without_new_privileges(tmp_path):
    code = r"""
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
with TestClient(app) as client:
    asset = client.get("/event-log-quality-trends.js")
    assert asset.status_code == 200, asset.text
    assert "application/javascript" in asset.headers["content-type"]
    assert "owgQualityTrendsRun" in asset.text
    assert asset.headers["cache-control"] == "no-store"
    assert '<script src="/event-log-quality-trends.js"></script>' in client.get("/").text
    headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session(),
               "x-owg-local-storage-confirmed": "true"}
    doc = b"case,activity,time,who\na,Open,2024-01-01T00:00:00Z,team\n"
    made = client.post("/v1/event-logs/import?format=csv&name=quality&case_column=case&activity_column=activity"
                       "&timestamp_column=time&resource_column=who", content=doc, headers=headers)
    assert made.status_code == 200, made.text
    path = "/v1/event-logs/" + made.json()["log_id"] + "/quality"
    assert client.get(path).status_code in (401, 403)
    assert client.get(path, headers={"Authorization": "Bearer " + local_auth.ensure_api_token()}).status_code in (401, 403)
    response = client.get(path, headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    result = response.json()
    assert result["batch_trends"]["format"] == "openworkgraph.event-log-quality-trends.v1"
    assert result["batch_trends"]["batches"][0]["metrics"]["rejected_input_rows"]["denominator"] == 1
    assert "reports" in result and "current" in result
"""
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
                "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


def test_review_queue_flags_recorded_issues_without_calling_missing_data_clean():
    digest = "a" * 64
    def report(batch, *, accepted, rejected, missing, events):
        return {"batch": batch, "kind": "append" if batch else "import",
                "transform": {"transform_sha256": digest},
                "rows": {"accepted": accepted, "rejected": rejected,
                         "duplicates_skipped_on_append": 0},
                "diagnostics": {"events": events, "events_without_resource": missing,
                                "events_sharing_a_timestamp_in_a_case": 0,
                                "events_dated_after_reference": 0}}
    result = trends.build([
        report(0, accepted=2, rejected=0, missing=0, events=2),
        report(1, accepted=2, rejected=1, missing=1, events=2),
        report(3, accepted=0, rejected=0, missing=0, events=0),
    ], [2])
    queue = result["review_queue"]
    assert [r["batch"] for r in queue] == [0, 1, 2, 3]
    assert queue[0]["status"] == "no_recorded_issue"
    assert queue[0]["flags"] == []
    assert queue[1]["status"] == "recorded_issue"
    assert queue[1]["flags"] == ["rejected_input_rows", "events_without_resource"]
    assert queue[1]["unknown_fields"] == []
    assert queue[2] == {"batch": 2, "status": "missing_report",
                        "flags": [], "unknown_fields": ["quality_report"]}
    assert queue[3]["status"] == "incomplete_evidence"
    assert "events_without_resource" in queue[3]["unknown_fields"]
    assert "comparison_missing_intermediate_report" in queue[3]["unknown_fields"]
    assert result["interpretation"]["review_queue_is_descriptive_not_quarantine"] is True
    assert trends.build([], [0])["review_queue"][0]["status"] == "missing_report"
    assert "alice" not in json.dumps(queue)


def test_review_queue_unknown_legacy_fields_and_transform_are_not_clean():
    report = {"batch": 0, "rows": {"accepted": 2, "rejected": 0},
              "diagnostics": {"events": 2, "events_without_resource": 0}}
    item = trends.build([report])["review_queue"][0]
    assert item["status"] == "incomplete_evidence"
    assert "transform_unverified" in item["unknown_fields"]
    assert "events_sharing_timestamp" in item["unknown_fields"]
    assert item["flags"] == []
