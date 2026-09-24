from __future__ import annotations

import importlib
import json
import tomllib
from pathlib import Path


def test_gateway_mcp_adapter_imports(monkeypatch):
    monkeypatch.setenv("OWG_GATEWAY_SERVICE_TOKEN", "test-token")
    module = importlib.import_module("gateway.mcp")
    assert hasattr(module, "mcp")


def test_product_versions_stay_in_sync():
    root = Path(__file__).resolve().parents[1]
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    manifest = json.loads((root / "mcpb" / "manifest.json").read_text(encoding="utf-8"))
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

    from gateway.settings import PRODUCT_VERSION

    assert manifest["version"] == version
    assert pyproject["project"]["version"] == version
    assert PRODUCT_VERSION == version
