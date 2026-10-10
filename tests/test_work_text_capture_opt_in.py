from __future__ import annotations

import json
import os
import stat

import pytest

from server import work_text_capture as wt


@pytest.fixture()
def isolated(monkeypatch, tmp_path):
    from server import ai_access, capture_exclusions
    from shared import capture_control
    monkeypatch.setattr(wt, "_POLICY", tmp_path / "policy.json")
    monkeypatch.setattr(wt, "_DB", tmp_path / "work_text.db")
    monkeypatch.setattr(ai_access, "ai_access_enabled", lambda: True)
    monkeypatch.setattr(capture_control, "read_state", lambda: {"state": "recording"})
    monkeypatch.setattr(capture_exclusions, "current", lambda: {
        "hosts": ["blocked.example"], "title_words": ["private"],
        "apps": [], "defaults": {},
    })
    return wt


def example(**overrides):
    return {
        "hostname": "docs.example.org",
        "pathname": "/project",
        "title": "Project rollout",
        "kind": "page",
        "text": "Project rollout needs tests and a QA review.",
        **overrides,
    }


def test_capture_and_local_ai_read_have_separate_opt_in(isolated):
    assert isolated.get_policy()["capture_enabled"] is False
    assert isolated.get_policy()["ai_read_enabled"] is False
    with pytest.raises(PermissionError):
        isolated.ingest(example())
    assert isolated.set_policy({"capture_enabled": True})["ai_read_enabled"] is False
    assert isolated.ingest(example())["status"] == "recorded_locally"
    assert isolated.ingest(example())["status"] == "duplicate_suppressed"
    with pytest.raises(PermissionError):
        isolated.recent_for_ai()
    assert isolated.set_policy({"ai_read_enabled": True})["ai_read_enabled"] is True
    rows = isolated.recent_for_ai()["items"]
    assert len(rows) == 1 and "Project rollout" in rows[0]["redacted_text"]
    assert not any(k in rows[0] for k in ("pathname", "query", "url"))
    assert isolated.get_policy()["retention_days"] == 7
    assert not (isolated._DB.parent / "workflow_observer.db").exists()


def test_never_record_and_sensitive_sites_are_enforced_again(isolated):
    isolated.set_policy({"capture_enabled": True})
    for args in (
        {"hostname": "blocked.example"},
        {"hostname": "sub.blocked.example"},
        {"hostname": "accounts.google.com"},
        {"hostname": "1177.se"},
        {"pathname": "/oauth/callback"},
        {"pathname": "/payments/checkout"},
        {"title": "private case"},
        {"text": "api_key = secretvalue12345"},
        {"text": "sk-abcdef123456789012345678"},
    ):
        with pytest.raises(PermissionError):
            isolated.ingest(example(**args))
    assert not isolated._DB.exists()


def test_stopping_capture_also_stops_text_ingest(isolated, monkeypatch):
    from shared import capture_control
    isolated.set_policy({"capture_enabled": True, "ai_read_enabled": True})
    monkeypatch.setattr(capture_control, "read_state", lambda: {"state": "paused"})
    with pytest.raises(PermissionError):
        isolated.ingest(example())
    monkeypatch.setattr(capture_control, "read_state", lambda: {"state": "stopped"})
    with pytest.raises(PermissionError):
        isolated.ingest(example())
    assert not isolated._DB.exists()


def test_revocation_deletes_work_text_and_ai_read_is_blocked(isolated):
    isolated.set_policy({"capture_enabled": True, "ai_read_enabled": True})
    isolated.ingest(example())
    assert isolated._DB.exists()
    isolated.set_policy({"capture_enabled": False})
    assert isolated.get_policy()["ai_read_enabled"] is False
    with pytest.raises(PermissionError):
        isolated.recent_for_ai()
    with isolated._conn() as db:
        assert db.execute("SELECT COUNT(*) FROM work_text").fetchone()[0] == 0


def test_local_text_db_uses_owner_only_permissions(isolated):
    isolated.set_policy({"capture_enabled": True})
    isolated.ingest(example())
    if os.name != "nt":
        assert stat.S_IMODE(isolated._DB.stat().st_mode) == 0o600
        assert stat.S_IMODE(isolated._POLICY.stat().st_mode) == 0o600


def test_no_gateway_sync_or_event_queue_in_work_text_route_source():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "server" / "work_text_routes.py").read_text()
    assert "from gateway" not in src and "import gateway" not in src
    assert "event_queue" not in src and "sync_upload" not in src
