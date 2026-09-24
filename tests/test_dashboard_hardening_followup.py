from __future__ import annotations

import io
import json
import zipfile


def test_malformed_target_and_page_shapes_do_not_crash_summary_or_rewrite_storage():
    from server import analytics, db

    db.init_db()
    raw_id = "legacy-string-target-raw"
    normalized_id = "legacy-string-target-normalized"
    malformed = json.dumps(
        {
            "action": "click",
            "target": "legacy-string-target",
            "page": "legacy-string-page",
        }
    )

    with db.connect() as conn:
        conn.execute("DELETE FROM events WHERE event_id = ?", (raw_id,))
        conn.execute("DELETE FROM normalized_events WHERE event_id = ?", (normalized_id,))
        conn.execute(
            """
            INSERT INTO events(
              event_id, observed_at, device_id, session_id, app, window_title,
              event_type, duration_seconds, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                raw_id,
                "2099-02-01T12:00:00+00:00",
                "legacy-device",
                "legacy-session",
                "Legacy App",
                "Legacy App",
                "screen_click",
                0,
                malformed,
            ),
        )
        conn.execute(
            """
            INSERT INTO normalized_events(
              event_id, observed_at, device_id, session_id, app, window_title,
              event_type, duration_seconds, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized_id,
                "2099-02-01T12:00:01+00:00",
                "legacy-device",
                "legacy-session",
                "Legacy App",
                "Legacy App",
                "screen_click",
                0,
                malformed,
            ),
        )

    analytics.clear_summary_cache()
    raw = analytics.summary(limit=10000, since=None, operational=False)
    operational = analytics.summary(limit=10000, since=None, operational=True)
    assert raw["screen_interactions"] >= 1
    assert operational["screen_interactions"] >= 1

    decoded_raw = next(row for row in db.rows("SELECT * FROM events WHERE event_id = ?", (raw_id,)))
    assert decoded_raw["metadata"]["target"] == {}
    assert decoded_raw["metadata"]["page"] == {}

    # The compatibility guard is a read-time projection, not a mutation of the
    # canonical raw row.
    with db.connect() as conn:
        stored = conn.execute("SELECT metadata_json FROM events WHERE event_id = ?", (raw_id,)).fetchone()
    assert stored is not None
    assert json.loads(stored["metadata_json"])["target"] == "legacy-string-target"
    assert json.loads(stored["metadata_json"])["page"] == "legacy-string-page"


def _minimal_export_payload() -> dict:
    return {
        "export": {"privacy_note": "test"},
        "capture_manifest": {},
        "starter_prompt_markdown": "",
        "ai_guide_markdown": "",
        "effort_by_surface": [],
        "transitions": [],
        "inferred_tasks": [],
        "repeated_task_families": [],
        "operational_events": [],
        "operational_semantic_activity": [],
    }


def test_empty_base_csv_exports_keep_real_header_rows_without_fake_data():
    from server import exporter

    data = exporter.csv_zip_bytes(_minimal_export_payload())
    with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
        expected = {
            "effort_by_surface.csv": "surface,container_app,events,active_seconds,engaged_seconds,idle_seconds,active_input_seconds,keypress_count,click_count,scroll_count",
            "transitions.csv": "from,to,count",
            "inferred_tasks.csv": "interpretation_status,started_at,ended_at,task_label,task_family,confidence,primary_surface,surfaces,elapsed_seconds,foreground_seconds,engaged_seconds,idle_seconds,active_input_seconds,keypress_count,click_count,scroll_count,boundary_reason,boundary_confidence,effort_estimated,semantic_actions",
            "repeated_task_families.csv": "interpretation_status,task_label,task_family,observed_count,surfaces,surface_variant_count,total_engaged_seconds,median_engaged_seconds,p90_engaged_seconds,median_elapsed_seconds,keypress_count,click_count,completion_boundary_count,confidence",
            "operational_events.csv": "observed_at,schema_version,organization_id,actor_id,device_id,sensor_id,source,session_id,app_or_surface,window_title,event_type,action,page_host,page_path,target_label,target_role,duration_seconds,metadata_json",
            "semantic_activity.csv": "observed_at,session_id,app,window_title,event_type,label,page,target,source",
        }
        for filename, header in expected.items():
            text = zf.read(filename).decode("utf-8-sig")
            assert text.strip() == header


def test_empty_context_csv_helper_writes_schema_header_only():
    from server.context_exporter import _csv_bytes

    text = _csv_bytes([], ["first", "second"]).decode("utf-8-sig")
    assert text.strip() == "first,second"
    assert len(text.splitlines()) == 1
