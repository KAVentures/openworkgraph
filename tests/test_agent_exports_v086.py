from __future__ import annotations

import csv
import io
import json
import zipfile

from server.agent_export import AGENT_RUN_EXPORT_FIELDS, agent_run_export_rows
from server.exporter import csv_zip_bytes, xlsx_bytes
from shared.agent_evidence import agent_event_to_evidence


def _event(operation: str, second: int, **extra):
    payload = {
        "event_id": f"export-event-{second}",
        "observed_at": f"2026-09-26T10:00:0{second}+00:00",
        "agent_name": "Export Agent",
        "provider": "test-provider",
        "framework": "custom-framework",
        "operation": operation,
        "status": "running" if operation == "run_started" else "success",
        "observation_level": "native_trace",
        "run_id": "native-run-export-v086",
        "trace_id": "native-trace-export-v086",
        "workflow_id": "workflow-export-v086",
        "duration_seconds": 0.0,
    }
    payload.update(extra)
    return agent_event_to_evidence(payload)


def _agent_events():
    return [
        _event("run_started", 0),
        _event(
            "tool_call",
            1,
            span_id="native-tool-span-v086",
            tool_name="repository_search",
            tool_category="search",
            duration_seconds=0.25,
        ),
        _event("run_finished", 2),
    ]


def _export_payload(agent_row: dict) -> dict:
    return {
        "export": {
            "privacy_note": "normalized export",
            "version": "0.86.0",
            "generated_at": "2026-09-26T10:01:00+00:00",
            "scope": "all",
            "run_started_at": None,
            "include_raw_local_evidence": False,
            "evidence_tables_truncated": False,
            "interpretation_note": "derived views require evidence-aware interpretation",
        },
        "capture_manifest": {
            "evidence": {
                "agent_execution_runs_exported": 1,
                "agent_execution_native_ids_exported": False,
                "agent_execution_content_payloads_exported": False,
            }
        },
        "ai_guide_markdown": "# AI guide\n",
        "starter_prompt_markdown": "# Starter prompt\n",
        "overview": {"operational": {}},
        "effort_by_surface": [],
        "transitions": [],
        "inferred_tasks": [],
        "repeated_task_families": [],
        "agent_runs": [agent_row],
        "operational_events": [],
        "operational_semantic_activity": [],
    }


def test_agent_run_export_is_one_row_per_run_and_hides_native_ids():
    [row] = agent_run_export_rows(_agent_events())

    assert tuple(row.keys()) == AGENT_RUN_EXPORT_FIELDS
    assert str(row["execution_id"]).startswith("execution:")
    assert row["agent_name"] == "Export Agent"
    assert row["provider"] == "test-provider"
    assert row["framework"] == "custom-framework"
    assert row["outcome_status"] == "success"
    assert row["complete_boundary_observed"] is True
    assert row["event_count_total"] == 3
    assert row["tool_call_count"] == 1
    assert row["model_call_count"] == 0
    assert row["hidden_reasoning_observed"] is False
    assert row["derived"] is True
    assert row["authoritative"] is False
    assert row["coverage_absence_means"] == "not_observed_not_proof_of_nonoccurrence"

    serialized = json.dumps(row, ensure_ascii=False)
    for native_value in (
        "native-run-export-v086",
        "native-trace-export-v086",
        "native-tool-span-v086",
    ):
        assert native_value not in serialized

    # Check field names structurally instead of substring-searching the serialized
    # representation: privacy-safe coverage names such as `span_identity` are
    # intentionally allowed and do not expose a native `span_id`.
    forbidden_fields = {
        "run_id",
        "trace_id",
        "span_id",
        "tool_arguments",
        "tool_results",
        "chain_of_thought",
    }
    assert forbidden_fields.isdisjoint(row.keys())


def test_csv_zip_contains_dedicated_agent_runs_table_with_stable_headers():
    [row] = agent_run_export_rows(_agent_events())
    payload = _export_payload(row)

    with zipfile.ZipFile(io.BytesIO(csv_zip_bytes(payload))) as archive:
        names = set(archive.namelist())
        assert "agent_runs.csv" in names
        raw = archive.read("agent_runs.csv").decode("utf-8-sig")
        parsed = list(csv.DictReader(io.StringIO(raw)))

    assert len(parsed) == 1
    assert tuple(parsed[0].keys()) == AGENT_RUN_EXPORT_FIELDS
    assert parsed[0]["agent_name"] == "Export Agent"
    assert parsed[0]["tool_call_count"] == "1"
    for forbidden in ("native-run-export-v086", "native-trace-export-v086", "native-tool-span-v086"):
        assert forbidden not in raw


def test_xlsx_contains_dedicated_agent_runs_sheet_without_native_ids():
    [row] = agent_run_export_rows(_agent_events())
    payload = _export_payload(row)

    workbook = xlsx_bytes(payload)
    with zipfile.ZipFile(io.BytesIO(workbook)) as archive:
        workbook_xml = archive.read("xl/workbook.xml").decode("utf-8")
        assert 'name="Agent runs"' in workbook_xml
        searchable_xml = "\n".join(
            archive.read(name).decode("utf-8", errors="ignore")
            for name in archive.namelist()
            if name.endswith(".xml")
        )

    assert "Export Agent" in searchable_xml
    for forbidden in ("native-run-export-v086", "native-trace-export-v086", "native-tool-span-v086"):
        assert forbidden not in searchable_xml
