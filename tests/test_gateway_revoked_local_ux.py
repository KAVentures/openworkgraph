"""A revoked Gateway device stops retrying but keeps local evidence intact."""
from __future__ import annotations

import json

from connector.control import status
from connector.state import SyncState


def test_revoked_device_is_a_distinct_local_only_safe_state(tmp_path, monkeypatch):
    root=tmp_path
    data=root/"data"/"live"
    auth=root/"data"/"auth"
    data.mkdir(parents=True)
    auth.mkdir(parents=True)
    token=auth/".gateway_device_token"
    token.write_text("test-device-token",encoding="utf-8")
    config=root/"config.json"
    config.write_text(json.dumps({"gateway":{
        "enabled":True,"url":"https://org.example","token_file":str(token),
    }}))
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA",str(data))
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR",str(auth))
    state=SyncState(data/"gateway_sync_state.db")
    state.set("status","revoked")
    state.set("last_error","Organization access removed or device credential invalid")
    result=status(config)
    assert result["mode"] == "revoked"
    assert result["device_revoked"] is True
    assert result["revoke_requires_new_enrollment"] is True
    assert token.exists()  # only explicit locally chosen disconnect removes key


def test_revoked_sync_skips_network_and_does_not_advance_cursor(tmp_path, monkeypatch):
    # The local worker must stop immediately on a persisted revoked marker.
    from connector import sync
    root=tmp_path
    data=root/"data"/"live"
    auth=root/"data"/"auth"
    data.mkdir(parents=True)
    auth.mkdir(parents=True)
    token=auth/".gateway_device_token"
    token.write_text("test-device-token",encoding="utf-8")
    config=root/"config.json"
    config.write_text(json.dumps({"gateway":{
        "enabled":True,"url":"https://org.example","token_file":str(token),
    }}))
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA",str(data))
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR",str(auth))
    state=SyncState(data/"gateway_sync_state.db")
    state.set_int("last_local_event_id",31)
    state.set("status","revoked")
    def no_client(*a,**kw):
        raise AssertionError("revoked worker unexpectedly attempted HTTP")
    monkeypatch.setattr(sync.httpx,"Client",no_client)
    assert sync.run(config,once=True)==0
    assert state.get_int("last_local_event_id")==31
    assert token.exists()


def test_credential_revocation_does_not_include_raw_http_messages():
    from pathlib import Path
    source=(Path(__file__).resolve().parents[1]/"connector"/"sync.py").read_text()
    assert 'state.set("status", "revoked")' in source
    assert 'state.set("last_error", "Organization access removed or device credential invalid")' in source
    assert 'return 0' in source
