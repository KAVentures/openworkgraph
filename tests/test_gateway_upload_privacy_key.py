"""Device pseudonym keys are independent of credentials and survive rotation."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from connector.cloud_privacy import local_owner_names, project_event_for_upload
from connector.privacy_key import load_or_create_upload_privacy_key


def _event(title: str) -> dict:
    return {
        "event_id": "event-001", "observed_at": "2026-10-13T08:00:00Z",
        "session_id": "session-01", "source": "desktop",
        "event_type": "focus_span", "app": "App",
        "window_title": title, "metadata": {},
    }


def test_device_key_persists_and_is_not_the_http_bearer(tmp_path):
    auth = tmp_path / "auth"
    key = load_or_create_upload_privacy_key(auth)
    assert len(key) == 64
    assert key != "Bearer demo-token"
    assert key == load_or_create_upload_privacy_key(auth)
    stored = auth / ".gateway_upload_privacy_key"
    assert stored.is_file()
    if os.name != "nt":
        assert stored.stat().st_mode & 0o077 == 0
    first = project_event_for_upload(_event("Välkommen Michaela!"), secret=key)
    # Simulate credential rotation: still load the SAME key rather than using
    # either old or new bearer as the HMAC secret.
    old_bearer, new_bearer = "device_token_old", "device_token_new"
    assert old_bearer != new_bearer
    next_key = load_or_create_upload_privacy_key(auth)
    second = project_event_for_upload(_event("Välkommen Michaela!"), secret=next_key)
    assert first == second
    serialized = str(second)
    assert key not in serialized
    assert old_bearer not in serialized
    assert new_bearer not in serialized


def test_device_key_fails_closed_if_corrupt_or_missing_after_storage(tmp_path):
    auth = tmp_path / "auth"
    key = load_or_create_upload_privacy_key(auth)
    path = auth / ".gateway_upload_privacy_key"
    path.write_text("corrupt\n", encoding="ascii")
    with pytest.raises(ValueError, match="invalid"):
        load_or_create_upload_privacy_key(auth)
    # A corrupt key is never silently regenerated and does not overwrite.
    assert path.read_text(encoding="ascii") == "corrupt\n"
    assert key != "corrupt"


def test_device_key_cannot_be_symlinked(tmp_path):
    auth = tmp_path / "auth"
    auth.mkdir()
    target = tmp_path / "target"
    target.write_text("a" * 64, encoding="ascii")
    link = auth / ".gateway_upload_privacy_key"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available on this runner")
    with pytest.raises(PermissionError, match="unsafe"):
        load_or_create_upload_privacy_key(auth)


def test_local_owner_full_name_masks_owner_first_name(monkeypatch):
    monkeypatch.setattr("server.presentation._owner_identity",
                        lambda: ({"koyar afrasyab": "OWNER"}, set(), set()))
    names = local_owner_names()
    assert "koyar" in names
    assert "koyar afrasyab" in names
    clean = project_event_for_upload(
        _event("Möte: Koyar / Emil"),
        secret="a-local-key-not-a-device-bearer",
        owner_names=names,
    )
    assert "Koyar" not in str(clean)
    assert "Emil" not in str(clean)
    assert "OWNER_" in clean["window_title"]
    assert "PERSON_" in clean["window_title"]
    assert "koyar" not in str(clean["metadata"]).lower()


def test_bare_os_username_is_not_promoted_to_owner(monkeypatch):
    # The identity provider does not trust a bare OS name. This function must
    # never use getpass.getuser() independently and broaden that rule.
    monkeypatch.setattr("server.presentation._owner_identity",
                        lambda: ({}, set(), set()))
    assert local_owner_names() == ()


def test_owner_key_is_never_exposed_in_device_projection_metadata(tmp_path):
    key = load_or_create_upload_privacy_key(tmp_path)
    row = _event("Tack Linnea")
    projected = project_event_for_upload(row, secret=key, owner_names=("Alexandra Named",))
    assert key not in str(projected)
    assert "Alexandra Named" not in str(projected)
    assert projected["metadata"]["gateway_sync"]["raw_metadata_preserved"] is False


def test_custom_connector_config_aliases_are_used_without_mutating_identity_cache(
    tmp_path, monkeypatch,
):
    local_cache = {"trusted doctor": "OWNER"}
    monkeypatch.setattr(
        "server.presentation._owner_identity",
        lambda: (local_cache, set(), set()),
    )
    config = tmp_path / "config.json"
    config.write_text('{"owner_aliases": ["Koyar Afrasyab"]}', encoding="utf-8")
    names = local_owner_names(config)
    assert "koyar" in names
    assert "koyar afrasyab" in names
    assert "koyar afrasyab" not in local_cache
    projected = project_event_for_upload(
        _event("Möte: Koyar / Emil"),
        secret="device-private-key-unrelated-to-bearer",
        owner_names=names,
    )
    assert "Koyar" not in str(projected)
