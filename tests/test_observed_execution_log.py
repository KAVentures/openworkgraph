from __future__ import annotations

import json

import pytest

from server import observed_execution_log as snapshots
from server import event_log_workspace as workspace
from server import event_log_description as descriptions

A = "execution:aaaaaaaaaaaaaaaa"
B = "execution:bbbbbbbbbbbbbbbb"


def run(identifier=A, actor="human"):
    return {"execution_id": identifier, "actor_kind": actor,
            "_session_ids": ["s1" if identifier == A else "s2"],
            "_run_material": "agent|actor|run" if actor == "agent" else "human|task",
            "started_at": "2026-10-01T10:00:00Z", "ended_at": "2026-10-01T10:05:00Z"}


def event(identifier, kind="browser_click", at="2026-10-01T10:00:01Z", session="s1", **extra):
    return {"event_id": identifier, "event_type": kind, "observed_at": at, "session_id": session,
            "app": "Gmail", "window_title": "PRIVATE WINDOW",
            "metadata": {"private": "SECRET TEXT"}, **extra}


@pytest.fixture
def selection(monkeypatch):
    raw = [
        event("raw-id-1"), event("raw-id-2", "browser_form_submit", "2026-10-01T10:00:05Z"),
        event("raw-id-3", "focus_span"), event("raw-id-4", "browser_control_hover_dwell"),
        event("raw-id-5", "clipboard_copy", "2026-10-01T10:00:01Z", session="s2"),
        event("raw-id-6", "clipboard_paste", "2026-10-01T10:00:01Z", session="s2"),
    ]
    monkeypatch.setattr(snapshots.evidence, "_load", lambda **kwargs: raw)
    monkeypatch.setattr(snapshots.evidence, "derive_executions", lambda rows: [run(), run(B)])
    return raw, {"execution_ids": [A, B]}


def test_preview_real_timestamps_and_no_content(selection):
    _, spec = selection
    rows, report = snapshots.prepare(spec)
    assert report["case_count"] == 2
    assert report["event_count"] == 4
    assert report["timestamp_tie_groups"] == 1
    assert report["capture_complete"] is False
    assert report["business_case_identity_verified"] is False
    assert report["sensor_observations_not_deduplicated_actions"] is True
    assert [row[2] for row in rows[:2]] == [
        "2026-10-01T10:00:01.000000+00:00", "2026-10-01T10:00:05.000000+00:00"]
    assert [row[0] for row in rows] == [A, A, B, B]
    dump = json.dumps([rows, report])
    assert "PRIVATE WINDOW" not in dump and "SECRET TEXT" not in dump and "raw-id-" not in dump


def test_create_real_workspace_and_reject_stale_preview(tmp_path, monkeypatch, selection):
    raw, spec = selection
    folder = tmp_path / "data"
    folder.mkdir()
    monkeypatch.setattr(workspace, "DATA_DIR", folder)
    preview = snapshots.preview(spec)
    raw.append(event("new-event", "browser_click", "2026-10-01T10:00:03Z"))
    with pytest.raises(snapshots.SnapshotError, match="changed since preview"):
        snapshots.create(spec, expected_sha256=preview["snapshot_sha256"], name="Test")
    assert workspace.list_logs()["logs"] == []
    preview = snapshots.preview(spec)
    stored = snapshots.create(spec, expected_sha256=preview["snapshot_sha256"], name="Test")
    assert stored["stored_independent_snapshot"] is True
    log = stored["log_id"]
    summary = workspace.summary(log)
    assert summary["cases"] == 2 and summary["events"] == 5
    description = descriptions.description(log)["description"]
    assert "Independent saved copy" in description
    assert preview["snapshot_sha256"] in description


def test_overlap_and_unavailable_case_refused():
    shared = event("duplicate")
    with pytest.raises(snapshots.SnapshotError, match="share retained"):
        snapshots.project([run(), run(B)], {A: [shared], B: [shared]}, {})
    with pytest.raises(snapshots.SnapshotError, match="no retained structural"):
        snapshots.project([run()], {A: [event("focus", "focus_span")]}, {})
    no_session = run()
    no_session["_session_ids"] = []
    with pytest.raises(snapshots.SnapshotError, match="no retained capture session"):
        snapshots.project([no_session], {A: [shared]}, {})


def test_source_limit_and_bad_scope_refused(monkeypatch, selection):
    for bad in ({"execution_ids": []}, {"execution_ids": [A, A]},
                {"execution_ids": ["raw-case"]},
                {"execution_ids": [A], "since": "2026-10-01"},
                {"execution_ids": [A], "since": "2026-10-01T10:00:00.5Z",
                 "until": "2026-10-01T10:00:00Z"},
                {"execution_ids": [A], "unknown": True}):
        with pytest.raises(snapshots.SnapshotError):
            snapshots.preview(bad)
    monkeypatch.setattr(snapshots.evidence, "_load", lambda **kw: [{}] * snapshots.SOURCE_LIMIT)
    with pytest.raises(snapshots.SnapshotError, match="Source limit reached"):
        snapshots.preview({"execution_ids": [A]})


def test_agent_activities_do_not_include_human_events_or_payloads():
    agent = run(A, "agent")
    tool = event("native", "agent_tool_call", source="agent")
    tool["metadata"] = {"operation": "tool_call", "tool": {"name": "SECRET TOOL", "arguments": "SECRET"}}
    rows, report = snapshots.project([agent], {A: [tool, event("human-click")]}, {})
    assert len(rows) == 1
    assert rows[0][1] == "Tool call"
    assert rows[0][-1] == "native_agent_run"
    assert "SECRET" not in json.dumps([rows, report])
