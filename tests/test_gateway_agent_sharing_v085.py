from __future__ import annotations

import json
from pathlib import Path

from connector.config import load_gateway_settings
from connector.policy import DEFAULT_LOCAL_POLICY, merge_policies, prepare_event_for_gateway


def _config(tmp_path: Path, local_policy: dict | None = None) -> Path:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({
            "gateway": {
                "enabled": True,
                "url": "https://gateway.example",
                "local_policy": local_policy or {},
            }
        }),
        encoding="utf-8",
    )
    return path


def _event(*, source: str) -> dict:
    return {
        "event_id": f"v085-{source}-event",
        "observed_at": "2026-09-26T10:00:00.000000Z",
        "source": source,
        "event_type": "agent_tool_call" if source == "agent" else "focus",
        "app": "Test",
        "window_title": "Test",
        "metadata": {"actor_kind": "agent" if source == "agent" else "human"},
    }


def test_gateway_config_defaults_agent_evidence_to_local_only(tmp_path):
    settings = load_gateway_settings(_config(tmp_path), auth_dir=tmp_path / "auth")
    assert settings.local_policy["allow_agent_events"] is False

    effective = merge_policies(settings.local_policy, {})
    assert effective["allow_agent_events"] is False
    assert prepare_event_for_gateway(_event(source="agent"), effective) is None
    assert prepare_event_for_gateway(_event(source="desktop"), effective) is not None


def test_policy_layer_itself_fails_closed_when_local_agent_setting_is_missing():
    assert DEFAULT_LOCAL_POLICY["allow_agent_events"] is False
    assert merge_policies({}, {})["allow_agent_events"] is False
    assert merge_policies({}, {"allow_agent_events": True})["allow_agent_events"] is False


def test_missing_organization_agent_setting_is_neutral_after_local_opt_in():
    assert merge_policies({"allow_agent_events": True}, {})["allow_agent_events"] is True
    assert merge_policies({"allow_agent_events": True}, None)["allow_agent_events"] is True


def test_endpoint_must_explicitly_opt_in_to_agent_gateway_sharing(tmp_path):
    settings = load_gateway_settings(
        _config(tmp_path, {"allow_agent_events": True}),
        auth_dir=tmp_path / "auth",
    )
    assert settings.local_policy["allow_agent_events"] is True

    effective = merge_policies(settings.local_policy, {})
    shared = prepare_event_for_gateway(_event(source="agent"), effective)
    assert effective["allow_agent_events"] is True
    assert shared is not None
    assert shared["source"] == "agent"


def test_organization_can_narrow_but_never_enable_agent_sharing(tmp_path):
    opted_in = load_gateway_settings(
        _config(tmp_path, {"allow_agent_events": True}),
        auth_dir=tmp_path / "auth-opted-in",
    )
    remote_denied = merge_policies(opted_in.local_policy, {"allow_agent_events": False})
    assert remote_denied["allow_agent_events"] is False
    assert prepare_event_for_gateway(_event(source="agent"), remote_denied) is None

    local_only = load_gateway_settings(
        _config(tmp_path, {"allow_agent_events": False}),
        auth_dir=tmp_path / "auth-local-only",
    )
    remote_allowed = merge_policies(local_only.local_policy, {"allow_agent_events": True})
    assert remote_allowed["allow_agent_events"] is False
    assert prepare_event_for_gateway(_event(source="agent"), remote_allowed) is None
