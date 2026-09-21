from __future__ import annotations

import copy
import io
import zipfile

from server import context_exporter, context_layers, exporter


def _focus(
    ts: str,
    title: str,
    duration: float,
    *,
    event_id: str,
    keys: int = 0,
    engaged: float = 0,
    app: str = "Google Chrome",
):
    return {
        "event_id": event_id,
        "session_id": "s1",
        "observed_at": ts,
        "app": app,
        "window_title": title,
        "event_type": "focus_span",
        "duration_seconds": duration,
        "metadata": {
            "activity": {
                "engaged_seconds": engaged,
                "idle_seconds": max(0.0, duration - engaged),
                "active_input_seconds": min(engaged, 10),
                "keypress_count": keys,
                "click_count": 2,
                "scroll_count": 1,
            }
        },
    }


def _browser(
    ts: str,
    host: str,
    action: str,
    label: str = "",
    *,
    event_id: str,
    title: str,
    path: str = "/",
):
    return {
        "event_id": event_id,
        "session_id": "s1",
        "observed_at": ts,
        "app": "Google Chrome",
        "window_title": title,
        "event_type": f"browser_{action}",
        "duration_seconds": 0,
        "metadata": {
            "action": action,
            "page": {"hostname": host, "pathname": path, "title": title},
            "target": {"tag": "button", "label": label} if label else {},
        },
    }


def test_lovable_sends_are_agent_turns_and_same_context_boundary_can_merge():
    sample = [
        _focus(
            "2026-09-21T14:00:00Z",
            "AI Medical Advisor | Lovable",
            60,
            event_id="focus-1",
            keys=120,
            engaged=48,
        ),
        _browser(
            "2026-09-21T14:00:20Z",
            "lovable.dev",
            "click",
            "Send message",
            event_id="send-1",
            title="AI Medical Advisor | Lovable",
        ),
        _browser(
            "2026-09-21T14:00:26Z",
            "lovable.dev",
            "click",
            "Preview",
            event_id="review-1",
            title="AI Medical Advisor | Lovable",
        ),
        _browser(
            "2026-09-21T14:00:40Z",
            "lovable.dev",
            "click",
            "Send follow-up",
            event_id="send-2",
            title="AI Medical Advisor | Lovable",
        ),
        _browser(
            "2026-09-21T14:00:55Z",
            "lovable.dev",
            "click",
            "Publish",
            event_id="publish-1",
            title="AI Medical Advisor | Lovable",
        ),
    ]

    result = context_layers.candidate_tasks(_raw_events=sample)

    assert len(result["agent_turns"]) == 2
    assert {x["trigger_action"] for x in result["agent_turns"]} == {
        "Send message",
        "Send follow-up",
    }
    assert result["inference"]["raw_evidence_mutated"] is False
    assert result["inference"]["merge_requires_same_context_anchor"] is True
    assert all("evidence_window" in task for task in result["tasks"])
    assert any(
        any(outcome.get("label") == "Publish" for outcome in task.get("outcomes") or [])
        for task in result["tasks"]
    )


def test_v0_version_text_is_not_mistaken_for_vercel_v0_agent():
    sample = [
        _focus(
            "2026-09-21T14:10:00Z",
            "OpenWorkGraph v0.50 - Google Chrome",
            30,
            event_id="focus-version",
            engaged=20,
        ),
        _browser(
            "2026-09-21T14:10:10Z",
            "example.com",
            "click",
            "Run",
            event_id="run-version",
            title="OpenWorkGraph v0.50",
        ),
    ]
    assert context_layers.agent_turns(_raw_events=sample) == []


def test_real_v0_dev_run_is_agent_turn():
    sample = [
        _focus(
            "2026-09-21T14:11:00Z",
            "Dashboard | v0 by Vercel",
            30,
            event_id="focus-v0",
            engaged=20,
        ),
        _browser(
            "2026-09-21T14:11:10Z",
            "v0.dev",
            "click",
            "Run",
            event_id="run-v0",
            title="Dashboard | v0 by Vercel",
        ),
    ]
    turns = context_layers.agent_turns(_raw_events=sample)
    assert len(turns) == 1
    assert turns[0]["evidence_event_id"] == "run-v0"


def test_gmail_send_is_not_an_agent_turn():
    sample = [
        _focus(
            "2026-09-21T14:20:00Z",
            "Inbox - Gmail",
            30,
            event_id="focus-mail",
            keys=80,
            engaged=25,
        ),
        _browser(
            "2026-09-21T14:20:25Z",
            "mail.google.com",
            "click",
            "Send",
            event_id="send-mail",
            title="Inbox - Gmail",
            path="/mail/u/0/#inbox/a",
        ),
    ]
    assert context_layers.agent_turns(_raw_events=sample) == []


def test_context_layers_never_mutate_input_raw_events():
    sample = [
        _focus(
            "2026-09-21T14:30:00Z",
            "AI Medical Advisor | Lovable",
            30,
            event_id="focus-raw",
            keys=12,
            engaged=20,
        ),
        _browser(
            "2026-09-21T14:30:15Z",
            "lovable.dev",
            "click",
            "Send message",
            event_id="send-raw",
            title="AI Medical Advisor | Lovable",
        ),
    ]
    before = copy.deepcopy(sample)

    context_layers.candidate_tasks(_raw_events=sample)
    context_layers.factual_context_timeline(_raw_events=sample)
    context_layers.agent_turns(_raw_events=sample)

    assert sample == before


def test_factual_context_is_index_with_raw_event_references_not_goal_inference():
    sample = [
        _focus(
            "2026-09-21T14:40:00Z",
            "AI Medical Advisor | Lovable",
            30,
            event_id="focus-context",
            keys=30,
            engaged=22,
        ),
        _browser(
            "2026-09-21T14:40:10Z",
            "lovable.dev",
            "click",
            "Send message",
            event_id="send-context",
            title="AI Medical Advisor | Lovable",
        ),
    ]

    rows = context_layers.factual_context_timeline(_raw_events=sample)
    assert len(rows) == 1
    row = rows[0]
    assert row["work_surface"] == "Lovable"
    assert row["window_title"] == "AI Medical Advisor | Lovable"
    assert row["keypress_count"] == 30
    assert "focus-context" in row["evidence_event_ids"]
    assert "send-context" in row["evidence_event_ids"]
    assert row["inference"] == "factual_context_index_v1"
    assert "task" not in row
    assert "goal" not in row


def test_summary_compatibility_hook_is_noop():
    original = {"events": 4, "candidate_tasks": [{"suggested_label": "Existing"}]}
    result = context_layers.apply_to_summary(original, limit=100, since=None)
    assert result is original


def _minimal_payload():
    raw = [
        {
            "event_id": "raw-1",
            "observed_at": "2026-09-21T14:00:00Z",
            "schema_version": "1.0",
            "organization_id": "org",
            "actor_id": "actor",
            "device_id": "device",
            "sensor_id": "sensor",
            "source": "desktop",
            "session_id": "s1",
            "app": "Google Chrome",
            "window_title": "AI Medical Advisor | Lovable",
            "event_type": "focus_span",
            "duration_seconds": 30,
            "metadata": {"activity": {"keypress_count": 12}},
        }
    ]
    return {
        "export": {
            "product": "OpenWorkGraph / Workflow Observer",
            "version": "test",
            "generated_at": "2026-09-21T14:01:00Z",
            "scope": "current",
            "run_started_at": "2026-09-21T14:00:00Z",
            "include_raw_local_evidence": True,
            "privacy_note": "test",
            "interpretation_note": "test",
        },
        "capture_manifest": {"schema_version": "1.0", "evidence": {}},
        "ai_guide_markdown": exporter.AI_DATA_DICTIONARY_MD,
        "starter_prompt_markdown": exporter.AI_STARTER_PROMPT_MD,
        "overview": {"operational": {}, "raw_capture_counts": {"events": 1}},
        "effort_by_surface": [],
        "transitions": [],
        "inferred_tasks": [],
        "agent_turns": [],
        "factual_context_timeline": [],
        "repeated_task_families": [],
        "operational_events": [],
        "operational_semantic_activity": [],
        "raw_local_evidence": raw,
    }


def test_context_csv_zip_preserves_v050_package_and_adds_context_tables():
    payload = _minimal_payload()
    payload["agent_turns"] = [
        {
            "agent_turn_id": "turn-1",
            "submitted_at": "2026-09-21T14:00:10Z",
            "surface": "Lovable",
            "trigger_action": "Send message",
            "evidence_event_id": "raw-1",
        }
    ]
    payload["factual_context_timeline"] = [
        {
            "context_id": "ctx-1",
            "started_at": "2026-09-21T14:00:00Z",
            "work_surface": "Lovable",
            "window_title": "AI Medical Advisor | Lovable",
            "evidence_event_ids": ["raw-1"],
        }
    ]

    base_zip = zipfile.ZipFile(io.BytesIO(exporter.csv_zip_bytes(payload)))
    ctx_zip = zipfile.ZipFile(io.BytesIO(context_exporter.csv_zip_bytes(payload)))

    for name in base_zip.namelist():
        assert name in ctx_zip.namelist()
        assert base_zip.read(name) == ctx_zip.read(name)

    assert {
        "START_HERE.md",
        "AI_GUIDE.md",
        "CAPTURE_MANIFEST.json",
        "agent_turns.csv",
        "factual_context_timeline.csv",
        "task_evidence.csv",
    } <= set(ctx_zip.namelist())


def test_context_xlsx_preserves_v050_ai_and_evidence_sheets():
    payload = _minimal_payload()
    archive = zipfile.ZipFile(io.BytesIO(context_exporter.xlsx_bytes(payload)))
    workbook_xml = archive.read("xl/workbook.xml").decode("utf-8")

    for sheet in (
        "Overview",
        "AI guide",
        "Starter prompt",
        "Capture manifest",
        "Operational events",
        "RAW local evidence",
    ):
        assert f'name="{sheet}"' in workbook_xml
