from __future__ import annotations

import importlib
import json


def _reload(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path / "auth"))
    import server.ai_access as access
    return importlib.reload(access)


def test_new_install_defaults_ai_access_on(monkeypatch, tmp_path):
    access = _reload(monkeypatch, tmp_path)
    assert access.ai_access_enabled() is True
    assert access.resets_on_restart() is False


def test_explicit_ai_access_off_is_remembered(monkeypatch, tmp_path):
    access = _reload(monkeypatch, tmp_path)
    assert access.set_ai_access(False) is False

    access = importlib.reload(access)
    assert access.ai_access_enabled() is False


def test_reset_on_restart_still_forces_access_off(monkeypatch, tmp_path):
    access = _reload(monkeypatch, tmp_path)
    assert access.ai_access_enabled() is True
    assert access.set_reset_on_restart(True) is True

    access = importlib.reload(access)
    assert access.ai_access_enabled() is False
    assert access.resets_on_restart() is True


def test_saved_explicit_on_remains_on(monkeypatch, tmp_path):
    access = _reload(monkeypatch, tmp_path)
    assert access.set_ai_access(True) is True
    state = json.loads((tmp_path / "auth" / "ai_access.json").read_text(encoding="utf-8"))
    assert state["enabled"] is True

    access = importlib.reload(access)
    assert access.ai_access_enabled() is True
