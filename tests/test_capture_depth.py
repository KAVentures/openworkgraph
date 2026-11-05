"""Capture-depth profiles, ingest enforcement, coverage manifest and friction candidates (#634)."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def depth(tmp_path, monkeypatch):
    from server import capture_depth
    monkeypatch.setattr(capture_depth, "DATA_DIR", tmp_path)
    return capture_depth


def test_standard_is_the_default_and_profiles_set_signals(depth):
    assert depth.load()["profile"] == "standard" and not any(depth.load()["signals"].values())
    enhanced = depth.save({"profile": "enhanced"})
    assert enhanced["profile"] == "enhanced" and enhanced["signals"]["pointer_trajectory"] is False
    assert enhanced["changes_sharing"] is False and "work_text_capture" in enhanced["independent_settings"]
    custom = depth.save({"signals": {"friction_candidates": False}})
    assert custom["profile"] == "custom" and custom["signals"]["hover_dwell"] is True


def test_pointer_trajectories_need_their_own_acknowledgement(depth):
    with pytest.raises(depth.CaptureDepthError, match="acknowledge"):
        depth.save({"profile": "full"})
    full = depth.save({"profile": "full", "acknowledgement_version": depth.FULL_ACK_VERSION})
    assert full["profile"] == "full" and full["signals"]["pointer_trajectory"]
    assert depth.save({"profile": "standard"})["profile"] == "standard"
    assert depth.save({"profile": "full"})["profile"] == "full"  # acknowledged once


@pytest.mark.parametrize("bad", [{"profile": "max"}, {"signals": {"keystrokes": True}}, {"signals": {"hover_dwell": 1}},
                                 {"parameters": {"hover_dwell_ms": 50}}, {"parameters": {"trajectory_hz": 100}},
                                 {"sharing": True}])
def test_invalid_settings_are_refused(depth, bad):
    with pytest.raises(depth.CaptureDepthError):
        depth.save(bad)


def test_disabled_signals_are_not_allowed_and_metadata_is_bounded(depth):
    assert not depth.action_allowed("control_hover_dwell") and depth.action_allowed("click")
    depth.save({"profile": "full", "acknowledgement_version": depth.FULL_ACK_VERSION})
    assert depth.action_allowed("pointer_trajectory")
    points = [[i * 250, 0.5, 0.25] for i in range(10)]
    clean = depth.bound_metadata("pointer_trajectory", {"points": points, "sample_hz": 4, "viewport_bucket": "1300x700",
                                                        "typed": "secret"})
    assert clean["points"] == points and "typed" not in clean and clean["coordinates"] == "viewport_fraction"
    assert depth.bound_metadata("pointer_trajectory", {"points": [[0, 2, 0]]}) is None
    assert depth.bound_metadata("pointer_trajectory", {"points": [[0, 0, 0]] * 201}) is None
    drag = depth.bound_metadata("drag_drop", {"payload_kinds": ["files", "text", "EVIL"], "completion": "complete",
                                              "data": "file contents", "source_target": {"label": "Invoice row", "value": "x"}})
    assert drag["payload_kinds"] == ["files", "text"] and "data" not in drag and drag["source_target"] == {"label": "Invoice row"}
    assert depth.bound_metadata("control_hover_dwell", {"duration_ms": "long"}) is None


def test_ingest_enforces_capture_depth_and_drops_excluded_pages(tmp_path):
    # Browser-route authentication is exercised elsewhere; call the ingest function directly here.
    code = r'''
from server import capture_depth, main
from server.main import BrowserEvent
from server.db import rows, init_db
init_db()
def event(action, metadata, host="work.example"):
    return BrowserEvent(observed_at="2026-11-05T10:00:00Z", action=action,
        page={"origin": "https://" + host, "hostname": host, "pathname": "/orders", "title": "Orders"},
        target={"tag": "button", "label": "Save"}, metadata=metadata)
hover = {"duration_ms": 900, "ended_by": "pointer_left", "threshold_ms": 750}
assert main.browser_event(event("control_hover_dwell", hover))["skipped"] == "capture_depth_signal_off"
assert main.browser_event(event("click", {}))["inserted"] == 1
capture_depth.save({"profile": "enhanced"})
assert main.browser_event(event("control_hover_dwell", hover))["inserted"] == 1
assert main.browser_event(event("control_hover_dwell", {"duration_ms": -1}))["skipped"] == "invalid_high_fidelity_event"
assert main.browser_event(event("pointer_trajectory", {"points": [[0, 0.1, 0.1]]}))["skipped"] == "capture_depth_signal_off"
from server import capture_exclusions
capture_exclusions.update({"hosts": ["bank.example"]})
assert main.browser_event(event("control_hover_dwell", hover, host="bank.example"))["skipped"] == "excluded_page"
stored = [r for r in rows("SELECT * FROM events") if r["event_type"] == "browser_control_hover_dwell"]
assert len(stored) == 1 and stored[0]["metadata"]["duration_ms"] == 900
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


def _e(i, event_type, seconds, *, label="Save", role="", page="/orders", ended_by=None, eid=None):
    meta = {"target": {"tag": "button", "role": role, "label": label}, "page": {"hostname": "app.example", "pathname": page},
            "tab_id": 1}
    if ended_by:
        meta["ended_by"] = ended_by
    return {"event_id": eid or f"e{i}", "event_type": event_type, "session_id": "s",
            "observed_at": (datetime(2026, 11, 5, 10, tzinfo=timezone.utc) + timedelta(seconds=seconds)).isoformat(),
            "metadata": meta}


def test_friction_candidates_are_observed_patterns_only():
    from server.interaction_evidence import friction_candidates
    events = [_e(1, "browser_click", 0), _e(2, "browser_click", 0.5), _e(3, "browser_click", 1.2),
              _e(4, "browser_page_view", 20, page="/orders"), _e(5, "browser_page_view", 22, page="/settings"),
              _e(6, "browser_page_view", 26, page="/orders"),
              _e(7, "browser_control_hover_dwell", 40, role="menuitem", label="Export", ended_by="pointer_left"),
              _e(8, "browser_control_hover_dwell", 60, role="menuitem", label="Print", ended_by="activated")]
    kinds = [c["kind"] for c in friction_candidates(events)]
    assert kinds.count("rapid_repeated_activation") == 1 and "quick_backtrack" in kinds
    assert kinds.count("hover_menu_not_followed") == 1 and "slow_completion" in kinds
    assert friction_candidates([_e(1, "browser_click", 0), _e(2, "browser_click", 5)]) == []


def test_coverage_manifest_and_friction_routes(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
import server.basic_mode_routes  # registered by the real entry point, server.enterprise_runner
import server.browser_signal_routes
from server import local_auth
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    assert client.put("/v1/capture-depth", headers=bearer, json={"profile": "enhanced"}).status_code == 403
    assert client.put("/v1/capture-depth", headers=owner, json={"profile": "enhanced"}).json()["profile"] == "enhanced"
    assert client.put("/v1/capture-depth", headers=owner, json={"profile": "full"}).status_code == 400
    manifest = client.get("/v1/capture-coverage", headers=owner).json()
    assert manifest["capture_profile"] == "enhanced" and manifest["modalities"]["desktop_hover_dwell"] == "unsupported"
    assert manifest["modalities"]["browser_hover_dwell"] in ("on", "on_but_browser_extension_not_seen")
    assert manifest["missing_is_not_absence"] is True
    assert client.get("/v1/capture-coverage/friction", headers=bearer).status_code == 403
    report = client.get("/v1/capture-coverage/friction", headers=owner).json()
    assert report["enabled"] is True and report["intent_or_attention_inferred"] is False
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


def test_browser_context_tells_the_extension_its_capture_depth(depth):
    from server.browser_signal_routes import browser_context_with_signal_settings
    depth.save({"profile": "enhanced"})
    context = browser_context_with_signal_settings()
    assert context["capture_depth"]["signals"]["hover_dwell"] is True
    assert context["capture_depth"]["parameters"]["hover_dwell_ms"] == 750


def test_shared_and_agent_views_get_trajectory_summaries_not_raw_points():
    from server.privacy_pipeline import summarize_pointer_trajectories
    event = {"event_type": "browser_pointer_trajectory",
             "metadata": {"action": "pointer_trajectory", "points": [[0, 0.123, 0.456], [480, 0.2, 0.3]], "sample_hz": 4}}
    out = summarize_pointer_trajectories({"events": [event, {"event_type": "browser_click", "metadata": {"points": [1]}}]})
    summary = out["events"][0]["metadata"]
    assert "points" not in summary and summary["point_count"] == 2 and summary["duration_ms"] == 480
    assert summary["raw_points_shared"] is False and summary["sample_hz"] == 4
    assert out["events"][1]["metadata"]["points"] == [1]
    assert event["metadata"]["points"], "the stored local event is not modified"


def test_real_exports_never_contain_raw_trajectory_points_or_editable_drag_labels(tmp_path):
    code = r'''
import io, json, zipfile
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, capture_depth, main
from server.db import init_db
from server.main import BrowserEvent
bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
with TestClient(app) as client:
    init_db()
    capture_depth.save({"profile": "full", "acknowledgement_version": capture_depth.FULL_ACK_VERSION})
    now = datetime.now(timezone.utc).isoformat()
    page = {"origin": "https://work.example", "hostname": "work.example", "pathname": "/x", "title": "X"}
    assert main.browser_event(BrowserEvent(observed_at=now, action="pointer_trajectory", page=page,
        metadata={"points": [[0, 0.1234, 0.9876], [250, 0.4321, 0.6789]]}))["inserted"] == 1
    assert main.browser_event(BrowserEvent(observed_at=now, action="drag_start", page=page,
        target={"tag": "textarea", "label": "PRIVATE DRAFT WORDS"},
        metadata={"payload_kinds": ["text"], "source_target": {"label": "OTHER DRAFT", "editable": True}}))["inserted"] == 1
    for fmt in ("json", "xlsx", "csvzip"):
        for redact in ("true", "false"):
            r = client.get(f"/v1/export/{fmt}?include_raw=true&redact_names={redact}", headers=bearer)
            assert r.status_code == 200, (fmt, redact, r.text[:200])
            body = r.content
            if fmt in ("xlsx", "csvzip"):
                with zipfile.ZipFile(io.BytesIO(body)) as z:
                    body = b"".join(z.read(n) for n in z.namelist())
            for leaked in (b"0.9876", b"0.6789", b"PRIVATE DRAFT WORDS", b"OTHER DRAFT"):
                assert leaked not in body, (fmt, redact, leaked)
    stored = [r for r in __import__("server.db", fromlist=["rows"]).rows("SELECT * FROM events")
              if r["event_type"] == "browser_pointer_trajectory"]
    assert stored[0]["metadata"]["points"], "raw points stay in local storage"
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
