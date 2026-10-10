from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from scripts.build_chatgpt_plugin import build


def _make(tmp_path: Path, app_id: str | None = None) -> Path:
    root = tmp_path / ("registered" if app_id else "direct")
    root.mkdir()
    logo = root / "logo.svg"
    logo.write_text('<svg xmlns="http://www.w3.org/2000/svg"></svg>', encoding="utf-8")
    return build(
        mcp_url="https://mcp.owg.kinvectum.com/mcp",
        homepage="https://owg.kinvectum.com",
        privacy_url="https://owg.kinvectum.com/privacy",
        company_url="https://owg.kinvectum.com",
        support_url="https://owg.kinvectum.com/support",
        terms_url="https://owg.kinvectum.com/terms",
        demo_recording_url="https://owg.kinvectum.com/review/demo",
        logo=logo, countries=["SE"], developer_name="Test publisher",
        version="0.0.1", output=root / "bundle.zip",
        registered_app_id=app_id,
    )


def test_default_bundle_keeps_portable_remote_mcp(tmp_path):
    with ZipFile(_make(tmp_path)) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("plugin.json"))
        mcp = json.loads(archive.read("mcp.json"))
        assert ".app.json" not in names
        assert manifest["extensions"]["com.openai"].get("apps") is None
        assert mcp["mcpServers"]["openworkgraph"]["url"] == "https://mcp.owg.kinvectum.com/mcp"
        assert "skills/openworkgraph/SKILL.md" in names


@pytest.mark.parametrize("prefix", ["plugin_asdk_app_", "asdk_app_"])
def test_registered_app_bundle_uses_app_mapping_not_direct_mcp(tmp_path, prefix):
    registered = prefix + "a" * 32  # synthetic fixture only; no real app is claimed
    with ZipFile(_make(tmp_path, registered)) as archive:
        names = set(archive.namelist())
        assert "mcp.json" not in names  # avoids a second desktop-style MCP dependency
        assert ".app.json" in names
        assert "skills/openworkgraph/SKILL.md" in names
        manifest = json.loads(archive.read("plugin.json"))
        apps = json.loads(archive.read(".app.json"))
        assert manifest["extensions"]["com.openai"]["apps"] == "./.app.json"
        assert apps == {"apps": {"openworkgraph": {"id": "asdk_app_" + "a" * 32, "required": True}}}


@pytest.mark.parametrize("bad", ["openworkgraph", "plugin_asdk_app_REPLACE_ME",
                                  "https://mcp.owg.kinvectum.com/mcp", "asdk_app_abc",
                                  "plugin_asdk_app_" + "a" * 32 + "/other"])
def test_registered_app_id_rejects_placeholders_and_invalid_values(tmp_path, bad):
    with pytest.raises(SystemExit):
        _make(tmp_path, bad)
