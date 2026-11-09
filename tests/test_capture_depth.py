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
    # Turning pointer movement off clears the acknowledgement, so turning it on again asks again.
    with pytest.raises(depth.CaptureDepthError, match="acknowledge"):
        depth.save({"profile": "full"})
    assert depth.save({"profile": "full", "acknowledgement_version": depth.FULL_ACK_VERSION})["profile"] == "full"


def test_detailed_capture_is_time_limited_and_expires_to_standard(depth, monkeypatch):
    from datetime import datetime, timedelta, timezone
    state = depth.save({"profile": "enhanced", "duration_days": 3})
    expires = datetime.fromisoformat(state["expires_at"])
    assert timedelta(days=2, hours=23) < expires - datetime.now(timezone.utc) <= timedelta(days=3)
    assert depth.for_extension()["expires_at"] == state["expires_at"]
    assert depth.save({"profile": "standard"})["expires_at"] is None
    for bad in (0, 91, True, "7"):
        with pytest.raises(depth.CaptureDepthError, match="duration_days"):
            depth.save({"profile": "enhanced", "duration_days": bad})
    depth.save({"profile": "full", "acknowledgement_version": depth.FULL_ACK_VERSION, "duration_days": 1})
    real = datetime

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return real.now(tz) + timedelta(days=2)
    monkeypatch.setattr(depth, "datetime", Later)
    later = depth.load()
    assert later["profile"] == "standard" and later["expired"] and later["full_acknowledged_version"] is None
    assert not depth.action_allowed("pointer_trajectory") and not depth.action_allowed("control_hover_dwell")
    assert depth.for_extension()["signals"]["hover_dwell"] is False


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
                                              "data": "file contents", "source_target": {"label": "Invoice row", "role": "row",
                                                                                         "value": "x"}})
    assert drag["payload_kinds"] == ["files", "text"] and "data" not in drag and drag["source_target"] == {"role": "row"}
    # No label of any kind is kept for high-fidelity targets: attributes can be set from typed text.
    assert depth.bound_target("drag_start", {"tag": "div", "role": "button", "label": "private typed words"}) == \
        {"tag": "div", "role": "button"}
    assert depth.bound_target("click", {"tag": "button", "label": "Send"}) == {"tag": "button", "label": "Send"}
    # Ordinary actions cannot carry the keys reserved for high-fidelity data.
    assert depth.bound_metadata("click", {"points": [[0, 0.1, 0.2]], "sample_hz": 4, "button": 0}) == {"button": 0}
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
    assert manifest["capture_profile"] == "enhanced" and manifest["modalities"]["desktop_hover_dwell"] == "on_but_desktop_collector_not_seen"
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
import server.history_routes  # registers /v1/history/export-json
bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
with TestClient(app) as client:
    init_db()
    capture_depth.save({"profile": "full", "acknowledgement_version": capture_depth.FULL_ACK_VERSION})
    now = datetime.now(timezone.utc).isoformat()
    page = {"origin": "https://work.example", "hostname": "work.example", "pathname": "/x", "title": "X"}
    points = [[0, 0.137, 0.519], [250, 0.862, 0.746]]
    assert main.browser_event(BrowserEvent(observed_at=now, action="pointer_trajectory", page=page,
        metadata={"points": points}))["inserted"] == 1
    assert main.browser_event(BrowserEvent(observed_at=now, action="drag_start", page=page,
        target={"tag": "textarea", "label": "PRIVATE DRAFT WORDS"},
        metadata={"payload_kinds": ["text"], "source_target": {"label": "OTHER DRAFT", "editable": True}}))["inserted"] == 1
    # The exact stored values (bound_metadata keeps three decimals) are what must never leave.
    # The exact stored coordinate pairs (bound_metadata keeps three decimals), however they are serialized,
    # and a points key: single values like 0.746 can occur by chance elsewhere (a timestamp's fraction).
    import re
    pairs = [re.compile(rb"%s[^0-9]{1,12}%s" % (re.escape(str(p[1]).encode()), re.escape(str(p[2]).encode())))
             for p in points]
    def check(body, where):
        for pattern in pairs:
            assert not pattern.search(body), (where, pattern.pattern)
        for leaked in (b'"points"', b"&quot;points&quot;", b"PRIVATE DRAFT WORDS", b"OTHER DRAFT"):
            assert leaked not in body, (where, leaked)
    for fmt in ("json", "xlsx", "csvzip"):
        for redact in ("true", "false"):
            r = client.get(f"/v1/export/{fmt}?include_raw=true&redact_names={redact}", headers=bearer)
            assert r.status_code == 200, (fmt, redact, r.text[:200])
            body = r.content
            if fmt in ("xlsx", "csvzip"):
                with zipfile.ZipFile(io.BytesIO(body)) as z:
                    body = b"".join(z.read(n) for n in z.namelist())
            else:
                rows = json.loads(body)["raw_local_evidence"]
                traj = [e for e in rows if e["event_type"] == "browser_pointer_trajectory"]
                assert traj and "points" not in traj[0]["metadata"] and traj[0]["metadata"]["point_count"] == 2
            check(body, (fmt, redact))
    for redact in ("true", "false"):
        r = client.get(f"/v1/history/export-json?include_raw=true&redact_names={redact}", headers=bearer)
        assert r.status_code == 200, r.text[:200]
        check(r.content, ("history", redact))
    from fastapi import HTTPException
    from server.workflow_evidence_routes import export_workflow_evidence
    for mode in ("stored", "redacted"):
        try:
            check(export_workflow_evidence(since="2000-01-01T00:00:00Z", representation=mode, archive=False).body, mode)
        except HTTPException:
            pass
    r = client.get("/v1/events?limit=50", headers=bearer)
    if r.status_code == 200:
        check(r.content, "events api")
    from server.db import connect, pointer_trajectory_points
    with connect() as conn:
        for table in ("events", "normalized_events", "context_events"):
            for (blob,) in conn.execute(f"SELECT metadata_json FROM {table}").fetchall():
                check((blob or "").encode(), table)
        event_id = conn.execute("SELECT event_id FROM events WHERE event_type = 'browser_pointer_trajectory'").fetchone()[0]
    assert pointer_trajectory_points(event_id) == points, "raw points stay on this device, apart from events"
    with connect() as conn:
        conn.execute("DELETE FROM events WHERE event_id = ?", (event_id,))
    assert pointer_trajectory_points(event_id) is None, "deleting the event deletes its points"
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


def test_retention_delete_now_and_local_heatmap(tmp_path):
    code = r'''
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, capture_depth, main
from server.db import init_db, connect, pointer_trajectory_points
from server.main import BrowserEvent
import server.basic_mode_routes  # registers the capture-depth routes
owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
with TestClient(app) as client:
    init_db()
    capture_depth.save({"profile": "full", "acknowledgement_version": capture_depth.FULL_ACK_VERSION,
                        "parameters": {"retention_days": 7}})
    assert capture_depth.load()["parameters"]["retention_days"] == 7
    try:
        capture_depth.save({"parameters": {"retention_days": 0}}); raise SystemExit("0 days accepted")
    except capture_depth.CaptureDepthError:
        pass
    now = datetime.now(timezone.utc)
    page = {"origin": "https://work.example", "hostname": "work.example", "pathname": "/inbox", "title": "Inbox"}
    other = {"origin": "https://work.example", "hostname": "work.example", "pathname": "/other", "title": "Other"}
    def send(action, when, metadata=None, target=None, p=page):
        return main.browser_event(BrowserEvent(observed_at=when.isoformat(), action=action, page=p,
                                               target=target or {}, metadata=metadata or {}))["inserted"]
    old = now - timedelta(days=10)
    assert send("pointer_trajectory", old, {"points": [[0, 0.01, 0.01]]}) == 1
    assert send("control_hover_dwell", old, {"duration_ms": 900}, {"tag": "button", "role": "button"}) == 1
    assert send("click", old, {}, {"tag": "button", "label": "Send"}) == 1
    assert send("pointer_trajectory", now, {"points": [[0, 0.52, 0.27], [250, 0.53, 0.27], [500, 0.97, 0.97]]}) == 1
    assert send("pointer_trajectory", now, {"points": [[0, 0.1, 0.1]]}, p=other) == 1
    # The first ingest already applied the limit (to the old trajectory); later ones wait an hour.
    assert capture_depth.apply_retention() is None
    purged = capture_depth.apply_retention(force=True)
    assert purged["deleted_events"] == 1 and purged["structural_evidence_kept"]
    with connect() as conn:
        types = sorted(r[0] for r in conn.execute("SELECT event_type FROM events").fetchall())
        assert types.count("browser_click") == 1 and types.count("browser_pointer_trajectory") == 2, types
        assert conn.execute("SELECT count(*) FROM pointer_trajectory_points").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM normalized_events WHERE event_type = 'browser_control_hover_dwell'").fetchone()[0] == 0
    # Heatmap: dashboard only, cell counts only.
    assert client.get("/v1/capture-coverage/heatmap", headers=bearer).status_code == 403
    pages = client.get("/v1/capture-coverage/heatmap", headers=owner).json()
    assert pages["pages"][0] == {"page": "work.example/inbox", "samples": 3} and "grid" not in pages
    h = client.get("/v1/capture-coverage/heatmap?hostname=work.example&pathname=/inbox", headers=owner).json()
    assert h["samples"] == 3 and h["batches"] == 1 and h["raw_points_returned"] is False
    assert h["grid"][5][10] == 2 and h["grid"][19][19] == 1 and sum(map(sum, h["grid"])) == 3
    assert "0.52" not in str(h) and "0.97" not in str(h)
    # Delete now: dashboard only, needs confirmation, keeps structural evidence.
    path = "/v1/capture-depth/delete"
    assert client.post(path, json={"confirm": True}, headers=bearer).status_code == 403
    assert client.post(path, json={}, headers=owner).status_code == 400
    assert client.post(path, json={"confirm": True, "older_than_days": -1}, headers=owner).status_code == 400
    gone = client.post(path, json={"confirm": True}, headers=owner).json()
    assert gone["deleted_events"] == 2
    with connect() as conn:
        assert [r[0] for r in conn.execute("SELECT event_type FROM events").fetchall()] == ["browser_click"]
        assert conn.execute("SELECT count(*) FROM pointer_trajectory_points").fetchone()[0] == 0
    assert client.get("/v1/capture-coverage/heatmap", headers=owner).json()["pages"] == []
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


def test_standard_ingest_drops_coordinates_and_labels_from_any_client_and_backups_disclose_points(tmp_path):
    code = r'''
import json, sqlite3, zipfile, tempfile, os
from datetime import datetime, timezone
from server import capture_depth, main, local_backup
from server.db import init_db, connect, rows
from server.main import BrowserEvent
from server.privacy_pipeline import redact_for_display
init_db()
now = datetime.now(timezone.utc).isoformat()
page = {"origin": "https://work.example", "hostname": "work.example", "pathname": "/x", "title": "X"}
assert capture_depth.load()["profile"] == "standard"
points = [[0, 0.137, 0.519], [250, 0.862, 0.746]]
# A modified paired client tries to smuggle coordinates into an ordinary click under Standard.
assert main.browser_event(BrowserEvent(observed_at=now, action="click", page=page, target={"tag": "button", "label": "Send"},
    metadata={"points": points, "coordinates": "viewport_fraction"}))["inserted"] == 1
stored = [r for r in rows("SELECT * FROM events") if r["event_type"] == "browser_click"][0]
assert "points" not in stored["metadata"] and "coordinates" not in stored["metadata"], stored["metadata"]
assert "0.137" not in json.dumps(redact_for_display(stored))
# Standard drops the high-fidelity actions themselves.
assert main.browser_event(BrowserEvent(observed_at=now, action="drag_start", page=page,
    target={"tag": "div", "role": "button", "label": "private typed words"}, metadata={}))["skipped"] == "capture_depth_signal_off"
# Enhanced: a drag target whose label was copied from typed text is stored without any label.
capture_depth.save({"profile": "enhanced"})
assert main.browser_event(BrowserEvent(observed_at=now, action="drag_start", page=page,
    target={"tag": "SYNTHETIC_DRAFT_742", "role": "SYNTHETIC_DRAFT_742", "type": "SYNTHETIC_DRAFT_742"},
    metadata={"source_target": {"tag":"div", "role":"SYNTHETIC_DRAFT_742", "type":"SYNTHETIC_DRAFT_742"},
              "drag_id":"SYNTHETIC_DRAFT_742", "top_frame":"SYNTHETIC_DRAFT_742"}))["inserted"] == 1
assert main.browser_event(BrowserEvent(observed_at=now, action="drag_start", page=page,
    target={"tag": "div", "role": "button", "label": "private typed words"},
    metadata={"source_target": {"tag": "div", "label": "private typed words"}, "payload_kinds": ["text"]}))["inserted"] == 1
with connect() as conn:
    blobs = " ".join(r[0] for t in ("events", "normalized_events", "context_events")
                     for r in conn.execute(f"SELECT metadata_json FROM {t}").fetchall())
assert "private typed words" not in blobs and "SYNTHETIC_DRAFT_742" not in blobs and "0.137" not in blobs
assert main.browser_event(BrowserEvent(observed_at=now, action="control_hover_dwell", page=page,
    target={"tag": "button", "type": "secure"}, metadata={"duration_ms": 900}))["skipped"] == "secure_target"
# Full: raw points live in their own local table, and a local backup of the database includes them, as stated.
capture_depth.save({"profile": "full", "acknowledgement_version": capture_depth.FULL_ACK_VERSION})
assert main.browser_event(BrowserEvent(observed_at=now, action="pointer_trajectory", page=page, metadata={"points": points}))["inserted"] == 1
assert capture_depth.public()["backups_include_raw_pointer_points"] is True
target = os.path.join(os.environ["WORKFLOW_OBSERVER_DATA"], "..", "backup.zip")
local_backup.create_backup(target)
with zipfile.ZipFile(target) as z:
    name = next(n for n in z.namelist() if n.endswith("workflow_observer.db"))
    extracted = z.extract(name, tempfile.mkdtemp())
with sqlite3.connect(extracted) as db:
    assert db.execute("SELECT count(*) FROM pointer_trajectory_points").fetchone()[0] == 1
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


def test_retried_high_fidelity_events_are_stored_once(tmp_path):
    code = r'''
from datetime import datetime, timezone
from server import capture_depth, main
from server.db import init_db, connect
from server.main import BrowserEvent
init_db()
capture_depth.save({"profile": "full", "acknowledgement_version": capture_depth.FULL_ACK_VERSION})
now = datetime.now(timezone.utc).isoformat()
page = {"origin": "https://work.example", "hostname": "work.example", "pathname": "/x", "title": "X"}
events = [
    dict(event_id="retry-traj", action="pointer_trajectory", metadata={"points": [[0, 0.1, 0.2], [250, 0.3, 0.4]]}),
    dict(event_id="retry-hover", action="control_hover_dwell", target={"tag": "button"},
         metadata={"duration_ms": 900, "control_rerendered": True}),
    dict(event_id="retry-drag", action="drag_start", target={"tag": "div", "role": "button"}, metadata={"payload_kinds": ["text"]}),
]
# The extension's queue resends an event with the same id after a failed delivery.
first = [main.browser_event(BrowserEvent(observed_at=now, page=page, **e))["inserted"] for e in events]
second = [main.browser_event(BrowserEvent(observed_at=now, page=page, **e))["inserted"] for e in events]
assert first == [1, 1, 1] and second == [0, 0, 0], (first, second)
with connect() as conn:
    for table in ("events", "normalized_events", "context_events"):
        assert conn.execute(f"SELECT count(*) FROM {table} WHERE event_id LIKE 'retry-%'").fetchone()[0] == 3, table
    assert conn.execute("SELECT count(*) FROM pointer_trajectory_points").fetchone()[0] == 1
    hover = conn.execute("SELECT metadata_json FROM events WHERE event_id = 'retry-hover'").fetchone()[0]
assert '"control_rerendered": true' in hover
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


def test_workflow_trace_labels_interaction_detail_observed_and_friction_inferred(tmp_path):
    code = r'''
from datetime import datetime, timedelta, timezone
from server import capture_depth, main
from server.db import init_db
from server.main import BrowserEvent
from server.mcp_trace import workflow_trace
init_db()
start = datetime.now(timezone.utc) - timedelta(minutes=5)
page = {"origin": "https://work.example", "hostname": "work.example", "pathname": "/orders", "title": "Orders"}
def send(action, seconds, **kw):
    return main.browser_event(BrowserEvent(observed_at=(start + timedelta(seconds=seconds)).isoformat(), action=action,
                                           page=page, **kw))
send("click", 0, target={"tag": "button", "label": "Save"})
trace = workflow_trace(since=(start - timedelta(minutes=1)).isoformat(), limit=50)
assert "interaction_candidates" not in trace, "Standard: no candidates are offered"
capture_depth.save({"profile": "enhanced"})
send("control_hover_dwell", 1, target={"tag": "button", "role": "menuitem"},
     metadata={"duration_ms": 900, "ended_by": "pointer_left", "control_rerendered": True})
send("drag_start", 2, target={"tag": "div", "role": "button"}, metadata={"payload_kinds": ["text"], "completion": "unknown"})
send("drag_end", 3, target={"tag": "div", "role": "button"}, metadata={"payload_kinds": [], "completion": "incomplete"})
for i in range(3):
    send("click", 10 + i * 0.4, target={"tag": "button", "label": "Retry"})
trace = workflow_trace(since=(start - timedelta(minutes=1)).isoformat(), limit=50)
rows = {r["event_type"]: r for r in trace["rows"]}
hover = rows["browser_control_hover_dwell"]["interaction_detail"]
assert hover == {"signal": "hover_dwell", "basis": "observed", "inferred": False, "context_only": True,
                 "workflow_step": False, "merge_evidence": False, "control_rerendered": True}, hover
assert rows["browser_drag_end"]["interaction_detail"]["completion"] == "incomplete"
assert rows["browser_drag_start"]["interaction_detail"]["completion_basis"] == "not_observed"
assert "interaction_detail" not in rows["browser_click"]
candidates = trace["interaction_candidates"]
# The menu hover with no choice within 5 s and the three quick clicks on one control.
assert [c["kind"] for c in candidates] == ["hover_menu_not_followed", "rapid_repeated_activation"], candidates
assert all(c["basis"] == "inferred_candidate" and c["requires_review"] and not c["workflow_step"] for c in candidates)
assert trace["interaction_intent_or_attention_inferred"] is False and trace["interaction_per_person_scoring"] is False
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


def test_local_interaction_playback(tmp_path):
    code = r'''
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, capture_depth, main
from server.db import init_db
from server.main import BrowserEvent
import server.basic_mode_routes  # registers the capture-depth routes
owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
with TestClient(app) as client:
    init_db()
    capture_depth.save({"profile": "full", "acknowledgement_version": capture_depth.FULL_ACK_VERSION})
    now = datetime.now(timezone.utc).replace(microsecond=0)
    page = {"origin": "https://work.example", "hostname": "work.example", "pathname": "/inbox", "title": "Inbox"}
    other = {"origin": "https://work.example", "hostname": "work.example", "pathname": "/other", "title": "Other"}
    def send(action, when, metadata=None, target=None, p=page):
        return main.browser_event(BrowserEvent(observed_at=when.isoformat(), action=action, page=p,
                                               target=target or {}, metadata=metadata or {}))["inserted"]
    first = now - timedelta(hours=2)
    send("control_hover_dwell", first, {"duration_ms": 900, "ended_by": "pointer_left"}, {"tag": "button", "role": "menuitem"})
    later = now - timedelta(minutes=1)
    send("pointer_trajectory", later, {"points": [[0, 0.1, 0.2], [500, 0.4, 0.5], [1000, 0.8, 0.9]]})
    send("control_hover_dwell", later + timedelta(seconds=2), {"duration_ms": 1200, "ended_by": "activated"},
         {"tag": "button", "role": "button", "label": "Send invoice to Ada"})
    send("click", later + timedelta(seconds=3), {}, {"tag": "button", "role": "button", "label": "Send invoice to Ada"})
    send("pointer_trajectory", later, {"points": [[0, 0.9, 0.9]]}, p=other)
    path = "/v1/capture-coverage/playback?hostname=work.example&pathname=/inbox"
    assert client.get(path, headers=bearer).status_code == 403
    r = client.get(path, headers=owner)
    assert r.status_code == 200, r.text
    r = r.json()
    assert [v["items"] for v in r["visits"]] == [1, 3] and r["visit"] == 1
    assert [i["kind"] for i in r["items"]] == ["hover", "click"]
    assert r["items"][0]["target"] == {"tag": "button", "role": "button"} and r["items"][0]["duration_ms"] == 1200
    # The pointer batch is stored when flushed: it began 1 s before its stored time.
    assert r["path"] == [[0, 0.1, 0.2], [500, 0.4, 0.5], [1000, 0.8, 0.9]], r["path"]
    assert [i["t_ms"] for i in r["items"]] == [3000, 4000] and r["duration_ms"] == 4000
    assert "Ada" not in str(r) and "invoice" not in str(r)
    assert r["counts"] == {"click": 1, "hover": 1}
    old = client.get(path + "&visit=0", headers=owner).json()
    assert [i["kind"] for i in old["items"]] == ["hover"] and old["items"][0]["target"]["role"] == "menuitem"
    assert client.get(path + "&visit=9", headers=owner).status_code == 404
    none = client.get("/v1/capture-coverage/playback?hostname=nowhere.example", headers=owner).json()
    assert none["visit"] is None and none["items"] == [] and none["visits"] == []
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
