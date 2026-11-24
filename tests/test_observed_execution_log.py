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
    metadata = workspace.list_logs()["logs"][0]["observed_execution_snapshot"]
    assert metadata["snapshot_sha256"] == preview["snapshot_sha256"]
    assert metadata["original_event_count"] == 5
    repeated = snapshots.create(spec, expected_sha256=preview["snapshot_sha256"], name="Test")
    assert repeated["existing_snapshot_reused"] is True
    assert repeated["log_id"] == log
    assert len(workspace.list_logs()["logs"]) == 1
    manifest = snapshots.source(log)
    assert manifest["source_history_rechecked"] is False
    assert manifest["scope"]["execution_ids"] == spec["execution_ids"]
    assert manifest["stored_event_count"] == 5
    assert snapshots.recheck(log)["status"] == "projection_matches"
    raw.append(event("another", "browser_click", "2026-10-01T10:00:04Z"))
    rechecked = snapshots.recheck(log)
    assert rechecked["status"] == "projection_changed"
    assert rechecked["stored_log_changed"] is False
    assert rechecked["business_outcomes_verified"] is False
    raw.clear()
    monkeypatch.setattr(snapshots.evidence, "derive_executions", lambda rows: [])
    rechecked = snapshots.recheck(log)
    assert rechecked["status"] == "source_unavailable"
    assert rechecked["current_projection_sha256"] is None
    assert snapshots.source(log)["stored_event_count"] == 5
    from io import BytesIO
    with pytest.raises(workspace.EventLogWorkspaceError, match="preserve their original rows"):
        workspace.append_log(log, BytesIO(b"case,activity,time\\nc1,A,2026-10-01T10:00:00Z\\n"),
                             source_format="csv",
                             mapping={"case_column": "case", "activity_column": "activity", "timestamp_column": "time"})
    workspace.delete_log(log)
    with pytest.raises(workspace.EventLogWorkspaceError):
        snapshots.source(log)
    conn = workspace._connect()
    try:
        assert conn.execute("SELECT count(*) FROM observed_execution_log_sources").fetchone()[0] == 0
    finally:
        conn.close()



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


def test_snapshot_http_requires_local_dashboard_and_storage_confirmation(tmp_path):
    import os
    from pathlib import Path
    import subprocess
    import sys

    code = r"""
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from server.agent_ingest import ingest_agent_payloads
from server import procedural_memory
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    consent = {"X-OWG-Local-Storage-Confirmed": "true"}
    base = "/v1/workflow-evidence/event-log"
    assert client.post(base + "/preview", json={}, headers=bearer).status_code == 403
    assert client.post(base + "/create", json={}, headers={**bearer, **consent}).status_code == 403
    assert client.post(base + "/create", json={}, headers=owner).status_code == 403
    assert client.post(base + "/preview", json={}, headers=owner).status_code == 422
    ingest_agent_payloads([{"agent_name":"Coder","provider":"test","framework":"custom-agent",
        "observation_level":"native_trace","run_id":"r1","session_id":"s1",
        "event_id":f"e{n}","operation":op,"status":"success",
        "observed_at":f"2026-09-01T08:0{n}:00+00:00"}
        for n,op in enumerate(["run_started","tool_call","run_finished"])])
    runs = procedural_memory.derive_executions(procedural_memory.load_recent_evidence(limit=25000))
    run = next(r for r in runs if r["actor_kind"] == "agent")
    selection = {"execution_ids":[run["execution_id"]]}
    response = client.post(base + "/preview", json=selection, headers=owner)
    assert response.status_code == 200, response.text
    preview = response.json()
    body = {"selection":selection,"expected_sha256":preview["snapshot_sha256"],"name":"Agent snapshot"}
    response = client.post(base + "/create",json=body,headers={**owner,**consent})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["case_count"] == 1 and result["event_count"] == 3, result
    assert result["stored_independent_snapshot"] is True
    duplicate = client.post(base + "/create",json=body,headers={**owner,**consent}).json()
    assert duplicate["log_id"] == result["log_id"] and duplicate["existing_snapshot_reused"]
    assert client.get("/v1/event-logs",headers=owner).json()["logs"][0]["log_id"] == result["log_id"]
    assert client.get(base + "/source",params={"log_id":result["log_id"]},headers=bearer).status_code == 403
    assert client.post(base + "/recheck",json={"log_id":result["log_id"]},headers=bearer).status_code == 403
    observation_request = {"log_id":result["log_id"],"execution_id":run["execution_id"],"event_ids":["e1"]}
    assert client.post(base + "/observations",json=observation_request,headers=bearer).status_code == 403
    checked = client.post(base + "/observations",json=observation_request,headers=owner)
    assert checked.status_code == 200, checked.text
    assert checked.json()["events"][0]["status"] == "observation_matches"

    manifest = client.get(base + "/source",params={"log_id":result["log_id"]},headers=owner)
    assert manifest.status_code == 200 and manifest.json()["source_history_rechecked"] is False, manifest.text
    rechecked = client.post(base + "/recheck",json={"log_id":result["log_id"]},headers=owner)
    assert rechecked.status_code == 200 and rechecked.json()["status"] == "projection_matches", rechecked.text

print("ok")
"""
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "srv"),
                "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    root = Path(__file__).resolve().parents[1]
    done = subprocess.run([sys.executable, "-c", code], cwd=root, env=env,
                          capture_output=True, text=True, timeout=180)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), done.stdout + done.stderr


def test_navigation_context_mode_is_explicit_and_pins_real_events(selection):
    raw, spec = selection
    raw.extend([
        event("nav-1", "focus_change", "2026-10-01T10:00:00Z"),
        event("nav-2", "browser_page_view", "2026-10-01T10:00:02Z"),
        event("nav-3", "browser_tab_activated", "2026-10-01T10:00:03Z"),
        event("nav-4", "browser_navigation", "2026-10-01T10:00:04Z", session="s2"),
        event("nav-5", "focus_span", "2026-10-01T10:00:04Z", session="s2"),
    ])
    ordinary = snapshots.preview(spec)
    assert ordinary == snapshots.preview({**spec, "projection": "actions"})
    rows, contextual = snapshots.prepare({**spec, "projection": "actions_and_navigation"})
    assert contextual["event_count"] == ordinary["event_count"] + 4
    assert contextual["snapshot_sha256"] != ordinary["snapshot_sha256"]
    assert contextual["scope"]["projection"] == "actions_and_navigation"
    assert any(row[1].endswith("Foreground changed") for row in rows)
    assert any(row[1].endswith("Navigation observed") for row in rows)
    assert contextual == snapshots.preview(contextual["scope"])
    with pytest.raises(snapshots.SnapshotError, match="Choose actions"):
        snapshots.preview({**spec, "projection": "raw text"})


def test_corrupt_snapshot_manifest_does_not_break_ordinary_event_log_queries(tmp_path, monkeypatch, selection):
    _, spec = selection
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)
    preview = snapshots.preview(spec)
    log = snapshots.create(spec, expected_sha256=preview["snapshot_sha256"], name="corrupt")["log_id"]
    conn = workspace._connect()
    try:
        conn.execute("UPDATE observed_execution_log_sources SET source_report=? WHERE log_id=?", ["{broken", log])
    finally:
        conn.close()
    metadata = workspace.summary(log)["log"]["observed_execution_snapshot"]
    assert metadata["manifest_available"] is False
    assert workspace.summary(log)["events"] == 4
    with pytest.raises(snapshots.SnapshotError, match="manifest is unavailable"):
        snapshots.source(log)
    from io import BytesIO
    with pytest.raises(workspace.EventLogWorkspaceError, match="preserve their original rows"):
        workspace.append_log(log, BytesIO(b"not needed"), source_format="csv")
    workspace.delete_log(log)


def test_observed_snapshot_lineage_reaches_existing_model_discovery(tmp_path, monkeypatch, selection):
    from server import event_log_model_discovery as discovery
    from server import process_discovery_provenance as provenance
    _, spec = selection
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)
    preview = snapshots.preview(spec)
    log = snapshots.create(spec, expected_sha256=preview["snapshot_sha256"], name="lineage")["log_id"]
    model = discovery.discover(log)
    recorded = provenance.record_draft(log, {"expected_model_sha256": model["process_model_sha256"]})
    record = {"process_model": recorded["process_model"],
              "evidence_refs": [recorded["source_snapshot"]["reference"]]}
    origins = provenance.origins(record)
    origin = origins[0]["observed_execution_origin"]
    assert origins[0]["status"] == "current"
    assert origin["snapshot_sha256"] == preview["snapshot_sha256"]
    assert origin["original_case_count"] == 2
    assert origin["retained_manifest_status"] == "matches"
    assert origin["original_history_rechecked"] is False
    assert origin["business_outcomes_verified"] is False
    assert A not in json.dumps(origins) and B not in json.dumps(origins)
    workspace.delete_log(log)
    assert provenance.origins(record)[0]["status"] == "unavailable"


def test_snapshot_ui_identity_and_recheck_model():
    import shutil
    import subprocess
    from pathlib import Path
    if not shutil.which("node"):
        pytest.skip("Node.js unavailable")
    script = r"""
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const context={window:{}};
vm.runInNewContext(fs.readFileSync('dashboard/observed_execution_log.js','utf8'),context);
const m=context.window.__owgObservedExecutionLogModel;
const id='execution:aaaaaaaaaaaaaaaa',logId='log_bbbbbbbbbbbbbbbb',digest='a'.repeat(64);
const selection={execution_ids:[id],since:'2026-11-01T00:00:00Z'};
const report={format:'openworkgraph.observed-execution-log.v1',snapshot_sha256:digest,
  scope:{...selection,since:'2026-11-01T00:00:00.000000+00:00',until:null},
  case_count:1,event_count:2,cases:[{execution_id:id,actor_kind:'human',events:2,timestamp_tie_groups:0}],
  timestamp_tie_groups:0,business_case_identity_verified:false,business_outcomes_verified:false,
  capture_complete:false,sensor_observations_not_deduplicated_actions:true,source_limit_reached:false,
  log_id:logId,stored_independent_snapshot:true,source_history_rechecked:false,stored_event_count:2};
const metadata={manifest_available:true,snapshot_sha256:digest,original_case_count:1,original_event_count:2};
assert(m.matchingPreview(report,selection));
assert(m.matchingPreview(report,{...selection,projection:'actions'}));
assert(!m.matchingPreview(report,{...selection,projection:'actions_and_navigation'}));
assert(m.matchingPreview({...report,scope:{...report.scope,projection:'actions_and_navigation'}},
  {...selection,projection:'actions_and_navigation'}));
assert(m.sourceManifestMatches(report,logId,metadata));
assert(!m.sourceManifestMatches({...report,stored_event_count:3},logId,metadata));
assert(!m.sourceManifestMatches(report,logId,{...metadata,manifest_available:false}));
assert(!m.sourceManifestMatches(report,'log_cccccccccccccccc',metadata));
const check={format:'openworkgraph.observed-execution-log-recheck.v1',log_id:logId,
  original_snapshot_sha256:digest,current_projection_sha256:digest,checked_at:'2026-11-24T06:00:00Z',
  status:'projection_matches',stored_log_changed:false,capture_complete:false,
  business_case_identity_verified:false,business_outcomes_verified:false};
assert(m.recheckMatches(check,logId,digest));
assert(!m.recheckMatches({...check,current_projection_sha256:null},logId,digest));
assert(m.recheckMatches({...check,status:'projection_changed',current_projection_sha256:'b'.repeat(64)},logId,digest));
assert(m.recheckMatches({...check,status:'source_unavailable',current_projection_sha256:null},logId,digest));
assert(!m.recheckMatches({...check,status:'source_unavailable'},logId,digest));
assert(!m.recheckMatches({...check,business_outcomes_verified:true},logId,digest));
console.log('snapshot model checks passed');
"""
    root = Path(__file__).resolve().parents[1]
    done = subprocess.run(["node", "-e", script], cwd=root, capture_output=True, text=True, timeout=20)
    assert done.returncode == 0, done.stderr


def test_pipeline_review_carries_minimized_original_observation_lineage():
    import shutil
    import subprocess
    from pathlib import Path
    if not shutil.which("node"):
        pytest.skip("Node.js unavailable")
    script = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const context={window:{}};
vm.runInNewContext(fs.readFileSync('dashboard/automation_pipeline.js','utf8'),context);
const m=context.window.__owgPipelineModel;
const digest='a'.repeat(64),now='2026-11-24T06:00:00Z';
const journey={idea:{idea_id:'idea',revision:1,state:'approved'},
  process_status:{status:'current',process_reference:{workflow_id:'workflow',revision:1,content_sha256:digest}},
  projects:[{project_id:'project',idea_revision:1,idea_revision_is_current:true,spec_sha256:digest,
    archive_sha256:digest,reported_specifications:[],source_check_exports:{count:0,last_document_sha256:null}}],
  dataset_origins:[{status:'current',source_content_sha256:digest,selection_sha256:digest,
    observed_execution_origin:{snapshot_sha256:digest,retained_manifest_status:'matches',
      original_history_rechecked:false,execution_ids:['SECRET-EXECUTION'],private:'SECRET-TEXT'}}]};
const packet=m.handoffReviewPacket(journey,'project','idea',now);
const origin=packet.dataset_origins[0].observed_execution_origin;
assert.equal(origin.snapshot_sha256,digest);
assert.equal(origin.retained_manifest_status,'matches');
assert.equal(origin.original_history_rechecked,false);
assert(!JSON.stringify(packet).includes('SECRET'));
assert(packet.readiness.blockers.includes('original_observation_history_not_rechecked'));
assert.equal(m.handoffDependencyReview(journey,'project','idea',now).stages[0].status,'locally_current');
journey.dataset_origins[0].observed_execution_origin.retained_manifest_status='unavailable_or_changed';
const changed=m.handoffReviewPacket(journey,'project','idea',now);
assert(changed.readiness.blockers.includes('original_observation_manifest_unavailable'));
assert.equal(m.handoffDependencyReview(journey,'project','idea',now).stages[0].status,'needs_review');
assert.equal(m.compareHandoffReviewPackets(packet,changed).status,'changed');
assert(m.compareHandoffReviewPackets(packet,changed).changes.includes('Dataset and cohort source checks'));
assert.equal(changed.execution_authorized,false);
console.log('observed source lineage handoff checks passed');
"""
    root = Path(__file__).resolve().parents[1]
    done = subprocess.run(["node", "-e", script], cwd=root, capture_output=True, text=True, timeout=20)
    assert done.returncode == 0, done.stderr


def test_exact_case_event_recheck_uses_original_rows(tmp_path, monkeypatch, selection):
    raw, spec = selection
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)
    preview = snapshots.preview(spec)
    log = snapshots.create(spec, expected_sha256=preview["snapshot_sha256"], name="event drilldown")["log_id"]
    case_events = dict(workspace.case_events(log))
    first, second = case_events[A]
    request = {"log_id": log, "execution_id": A,
               "event_ids": [second["event_id"], first["event_id"]]}
    found = snapshots.inspect_observations(request)
    assert [row["event_id"] for row in found["events"]] == request["event_ids"]
    assert all(row["status"] == "observation_matches" for row in found["events"])
    assert found["events"][0]["evidence_ref"] == second["attributes"]["evidence_ref"]
    assert found["events"][0]["original_activity"] == second["activity"]
    assert found["current_projection_sha256"] == preview["snapshot_sha256"]
    assert "PRIVATE WINDOW" not in json.dumps(found)
    assert found["business_outcomes_verified"] is False
    for bad in ({**request, "execution_id": B},
                {**request, "event_ids": [first["event_id"], first["event_id"]]},
                {**request, "event_ids": ["e99999"]},
                {**request, "event_ids": ["raw-id-1"]}):
        with pytest.raises(snapshots.SnapshotError):
            snapshots.inspect_observations(bad)
    # Same reference and execution but changed timestamp.
    raw[0]["observed_at"] = "2026-10-01T10:00:02Z"
    changed = snapshots.inspect_observations(request)
    assert changed["events"][1]["status"] == "observation_changed"
    assert changed["events"][0]["status"] == "observation_matches"
    # A removed observation must not borrow another observation.
    raw.pop(0)
    missing = snapshots.inspect_observations(request)
    assert missing["events"][1]["status"] == "not_in_current_projection"
    assert missing["events"][0]["status"] == "observation_matches"
    raw.clear()
    unavailable = snapshots.inspect_observations(request)
    assert unavailable["current_projection_sha256"] is None
    assert all(row["status"] == "source_unavailable" for row in unavailable["events"])
    assert workspace.summary(log)["events"] == 4
    workspace.delete_log(log)
    with pytest.raises(workspace.EventLogWorkspaceError):
        snapshots.inspect_observations(request)
