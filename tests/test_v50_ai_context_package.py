from __future__ import annotations

import importlib
import io
import json
import zipfile


def _event(event_id: str, ts: str, *, app: str = "Google Chrome", title: str = "OpenWorkGraph - Google Chrome") -> dict:
    return {
        "event_id": event_id,
        "observed_at": ts,
        "schema_version": "1.0",
        "organization_id": "",
        "actor_id": "",
        "device_id": "d1",
        "sensor_id": "desktop:test",
        "source": "desktop",
        "session_id": "s1",
        "app": app,
        "window_title": title,
        "event_type": "focus_span",
        "duration_seconds": 10.0,
        "screenshot_path": None,
        "metadata": {
            "source": "desktop",
            "excluded": False,
            "activity": {
                "engaged_seconds": 8,
                "idle_seconds": 2,
                "keypress_count": 3,
                "click_count": 2,
                "scroll_count": 1,
            },
        },
    }


def test_ai_context_is_additive_and_self_describing(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    monkeypatch.setenv("WORKFLOW_OBSERVER_RUN_STARTED_AT", "2026-09-21T12:00:00+00:00")

    import server.db as db
    import server.analytics as analytics
    import server.exporter as exporter

    importlib.reload(db)
    importlib.reload(analytics)
    importlib.reload(exporter)
    db.init_db()
    db.insert_events([_event("desktop-1", "2026-09-21T12:00:01+00:00")])

    payload = exporter.build_export_payload(scope="current", include_raw=True)

    # Existing evidence/data surfaces remain present.
    assert "operational_events" in payload
    assert "operational_semantic_activity" in payload
    assert "inferred_tasks" in payload
    assert "raw_local_evidence" in payload

    # New AI material is additive and sourced from the canonical bundled files.
    assert "workflow-evidence layer" in payload["ai_guide_markdown"]
    assert "Treat raw observations as evidence" in payload["starter_prompt_markdown"]
    interpretation_note = payload["export"]["interpretation_note"]
    assert "derived views" in interpretation_note
    assert "observation coverage" in interpretation_note

    manifest = payload["capture_manifest"]
    assert manifest["evidence"]["raw_local_evidence_included"] is True
    assert manifest["evidence"]["browser_extension"] == "not_observed_in_export"
    assert manifest["evidence"]["browser_extension_semantic_event_count"] == 0
    assert manifest["evidence"]["desktop_browser_context_may_still_be_present"] is True
    assert manifest["interpretation"]["inferred_tasks_role"].startswith("heuristic")

    # JSON remains valid and contains the new self-description without dropping evidence.
    encoded = json.loads(exporter.json_bytes(payload))
    assert encoded["raw_local_evidence"]
    assert encoded["capture_manifest"]["evidence"]["typed_text_captured"] is False


def test_csvzip_is_an_ai_context_package_without_removing_legacy_files(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    monkeypatch.setenv("WORKFLOW_OBSERVER_RUN_STARTED_AT", "2026-09-21T12:00:00+00:00")

    import server.db as db
    import server.analytics as analytics
    import server.exporter as exporter

    importlib.reload(db)
    importlib.reload(analytics)
    importlib.reload(exporter)
    db.init_db()
    db.insert_events([_event("desktop-2", "2026-09-21T12:00:02+00:00")])
    payload = exporter.build_export_payload(scope="current", include_raw=True)

    with zipfile.ZipFile(io.BytesIO(exporter.csv_zip_bytes(payload))) as zf:
        names = set(zf.namelist())
        # New context-package material.
        assert {"START_HERE.md", "AI_GUIDE.md", "CAPTURE_MANIFEST.json"} <= names
        manifest = json.loads(zf.read("CAPTURE_MANIFEST.json"))
        assert manifest["evidence"]["browser_extension"] == "not_observed_in_export"

        # Backward-compatible files remain.
        assert "README_FOR_AI.md" in names
        assert "operational_events.csv" in names
        assert "inferred_tasks.csv" in names
        assert "raw_local_evidence.csv" in names

        task_csv = zf.read("inferred_tasks.csv").decode("utf-8-sig")
        if task_csv.strip():
            assert "interpretation_status" in task_csv
            assert "heuristic_not_ground_truth" in task_csv


def test_xlsx_contains_ai_guide_prompt_manifest_and_existing_evidence_sheets(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    monkeypatch.setenv("WORKFLOW_OBSERVER_RUN_STARTED_AT", "2026-09-21T12:00:00+00:00")

    import server.db as db
    import server.analytics as analytics
    import server.exporter as exporter

    importlib.reload(db)
    importlib.reload(analytics)
    importlib.reload(exporter)
    db.init_db()
    db.insert_events([_event("desktop-3", "2026-09-21T12:00:03+00:00")])
    payload = exporter.build_export_payload(scope="current", include_raw=True)

    workbook = exporter.xlsx_bytes(payload)
    assert workbook[:2] == b"PK"
    with zipfile.ZipFile(io.BytesIO(workbook)) as zf:
        workbook_xml = zf.read("xl/workbook.xml").decode("utf-8")
        for sheet in (
            "Overview",
            "AI guide",
            "Starter prompt",
            "Capture manifest",
            "Operational events",
            "RAW local evidence",
        ):
            assert f'name="{sheet}"' in workbook_xml
