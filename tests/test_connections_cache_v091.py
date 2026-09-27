from __future__ import annotations

from server import connections


def test_switch_write_updates_cache_immediately(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path / "data"))
    connections._CACHE.update(mtime=None, data={})

    connections._write_switch("cursor", "mcp", False)
    assert connections._CACHE["data"]["mcp"]["cursor"] is False
    assert connections.is_enabled("cursor", "mcp") is False

    connections._write_switch("cursor", "mcp", True)
    assert connections._CACHE["data"]["mcp"]["cursor"] is True
    assert connections.is_enabled("cursor", "mcp") is True


def test_missing_switch_file_clears_stale_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path / "data"))
    connections._CACHE.update(mtime=123, data={"mcp": {"cursor": False}})

    assert connections._read_switches() == {}
    assert connections._CACHE == {"mtime": None, "data": {}}
    assert connections.is_enabled("cursor", "mcp") is True
