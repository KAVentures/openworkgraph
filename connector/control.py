from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from .config import load_device_token, load_gateway_settings
from .state import SyncState


def _paths(config_path: Path) -> tuple[Path, Path]:
    root = config_path.resolve().parent
    data_dir = Path(os.getenv("WORKFLOW_OBSERVER_DATA", root / "data" / "live"))
    auth_dir = Path(os.getenv("WORKFLOW_OBSERVER_AUTH_DIR", root / "data" / "auth"))
    return data_dir, auth_dir


def _max_local_event_id(data_dir: Path) -> int:
    db_path = data_dir / "workflow_observer.db"
    if not db_path.exists():
        return 0
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()
        return int(row[0] if row else 0)
    except sqlite3.Error:
        return 0
    finally:
        conn.close()


def _max_local_agent_message_id(data_dir: Path) -> int:
    db_path = data_dir / "workflow_observer.db"
    if not db_path.exists():
        return 0
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        try:
            row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM agent_session_messages").fetchone()
        except sqlite3.Error:
            return 0
        return int(row[0] if row else 0)
    finally:
        conn.close()


def _session_message_policy(data_dir: Path) -> dict[str, Any]:
    path = data_dir / "agent_session_policy.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        value = {}
    return {
        "capture_visible_messages": bool(value.get("capture_visible_messages", False)),
        "allow_gateway_session_messages": bool(value.get("allow_gateway_session_messages", False)),
        "effective_local_opt_in": bool(value.get("capture_visible_messages", False)) and bool(value.get("allow_gateway_session_messages", False)),
        "separate_from_structural_agent_sharing": True,
    }


def _read_root(config_path: Path) -> dict[str, Any]:
    try:
        value = json.loads(config_path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _write_root(config_path: Path, value: dict[str, Any]) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = config_path.with_name(config_path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except Exception:
        pass
    os.replace(tmp, config_path)


def local_agent_sharing(config_path: Path) -> dict[str, Any]:
    root = _read_root(config_path)
    gateway = root.get("gateway") if isinstance(root.get("gateway"), dict) else {}
    policy = gateway.get("local_policy") if isinstance(gateway.get("local_policy"), dict) else {}
    return {
        "allow_agent_events": bool(policy.get("allow_agent_events", False)),
        "scope": "endpoint_local_preference",
        "effective_requires_organization_permission": True,
        "content_capture_changed": False,
        "prompt_content_shared": False,
        "model_response_content_shared": False,
        "tool_arguments_shared": False,
        "tool_results_shared": False,
    }


def set_local_agent_sharing(config_path: Path, enabled: bool) -> dict[str, Any]:
    root = _read_root(config_path)
    gateway = root.get("gateway") if isinstance(root.get("gateway"), dict) else {}
    gateway = dict(gateway)
    policy = gateway.get("local_policy") if isinstance(gateway.get("local_policy"), dict) else {}
    policy = dict(policy)
    policy["allow_agent_events"] = bool(enabled)
    gateway["local_policy"] = policy
    root["gateway"] = gateway
    _write_root(config_path, root)
    return local_agent_sharing(config_path)


def status(config_path: Path) -> dict[str, Any]:
    data_dir, auth_dir = _paths(config_path)
    settings = load_gateway_settings(config_path, auth_dir=auth_dir)
    state = SyncState(data_dir / "gateway_sync_state.db")
    enrolled = bool(load_device_token(settings))
    paused = state.get_bool("sharing_paused", False)
    if not settings.enabled:
        mode = "local_only"
    elif not enrolled:
        mode = "configured_not_enrolled"
    elif paused:
        mode = "connected_paused"
    else:
        mode = "connected"
    return {
        "mode": mode,
        "gateway_enabled": settings.enabled,
        "gateway_url": settings.url,
        "enrolled": enrolled,
        "sharing_paused": paused,
        "pause_semantics": "evidence captured while paused remains local and is never backfilled",
        "sync_status": state.get("status", "not_started"),
        "last_success_at": state.get("last_success_at", "") or None,
        "last_error": state.get("last_error", "") or None,
        "last_local_event_id": state.get_int("last_local_event_id", 0),
        "last_batch_shared": state.get_int("last_batch_shared", 0),
        "policy_refreshed_at": state.get("policy_refreshed_at", "") or None,
        "quarantined_events": state.quarantine_count(),
        "credential_exposed": False,
        "local_agent_sharing": local_agent_sharing(config_path),
        "local_agent_session_sharing": _session_message_policy(data_dir),
        "last_local_agent_message_id": state.get_int("last_local_agent_message_id", 0),
        "last_agent_message_batch_shared": state.get_int("last_agent_message_batch_shared", 0),
    }


def set_sharing(config_path: Path, enabled: bool) -> dict[str, Any]:
    data_dir, auth_dir = _paths(config_path)
    settings = load_gateway_settings(config_path, auth_dir=auth_dir)
    if enabled and (not settings.enabled or not settings.url or not load_device_token(settings)):
        raise RuntimeError("Gateway sharing cannot be enabled until the endpoint is configured and enrolled")
    state = SyncState(data_dir / "gateway_sync_state.db")
    currently_paused = state.get_bool("sharing_paused", False)

    if not enabled and not currently_paused:
        # Capture the local boundary immediately. Evidence created after this ID is
        # permanently excluded when sharing resumes; local recording continues.
        state.set_int("pause_started_after_id", _max_local_event_id(data_dir))
        state.set_int("pause_started_after_agent_message_id", _max_local_agent_message_id(data_dir))
        state.set_bool("sharing_paused", True)
        state.set("status", "paused")
    elif enabled and currently_paused:
        end_id = _max_local_event_id(data_dir)
        marker = state.get("pause_started_after_id", "")
        if marker:
            try:
                start_after = int(marker)
            except Exception:
                start_after = end_id
            if end_id > start_after:
                state.add_skip_range(start_after + 1, end_id, "user_paused_gateway_sharing")
        message_end_id = _max_local_agent_message_id(data_dir)
        message_marker = state.get("pause_started_after_agent_message_id", "")
        if message_marker:
            try:
                message_start_after = int(message_marker)
            except Exception:
                message_start_after = message_end_id
            if message_end_id > message_start_after:
                state.add_agent_message_skip_range(
                    message_start_after + 1, message_end_id, "user_paused_gateway_sharing"
                )
        state.delete("pause_started_after_id")
        state.delete("pause_started_after_agent_message_id")
        state.set_bool("sharing_paused", False)
        state.set("status", "not_started")
    else:
        state.set_bool("sharing_paused", not enabled)
        state.set("status", "not_started" if enabled else "paused")
    return status(config_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Control optional OpenWorkGraph organization Gateway sharing")
    parser.add_argument("action", choices=["status", "pause", "resume"], nargs="?", default="status")
    parser.add_argument("--config", default="config.json")
    args = parser.parse_args()
    config_path = Path(args.config).resolve()
    if args.action == "pause":
        result = set_sharing(config_path, False)
    elif args.action == "resume":
        result = set_sharing(config_path, True)
    else:
        result = status(config_path)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
