from __future__ import annotations

import json
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION = "0.86.0"


def test_release_version_sources_are_aligned():
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    manifest = json.loads((ROOT / "mcpb" / "manifest.json").read_text(encoding="utf-8"))

    assert version == EXPECTED_VERSION
    assert pyproject["project"]["version"] == EXPECTED_VERSION
    assert manifest["version"] == EXPECTED_VERSION


def test_release_notes_are_current_and_version_driven():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert 'VERSION="$(tr -d' in workflow
    assert 'TAG="v${VERSION}"' in workflow
    assert "v0.53" not in workflow
    assert "${TAG}" in workflow
    assert "Agent evidence is local-only by default" in workflow
    assert "dedicated privacy-safe agent-run table" in workflow


def test_generic_otel_docs_use_exact_json_trace_endpoint():
    docs = (ROOT / "docs" / "NATIVE_AGENT_ADAPTERS.md").read_text(encoding="utf-8")
    for marker in (
        'OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="http://127.0.0.1:8787/agent-ingest/v1/otel"',
        'OTEL_EXPORTER_OTLP_TRACES_PROTOCOL="http/json"',
        'OTEL_EXPORTER_OTLP_TRACES_HEADERS="Authorization=Bearer WRITE_ONLY_AGENT_TOKEN"',
        "does not accept protobuf bodies",
        "does not currently expose the conventional `/v1/traces` alias",
    ):
        assert marker in docs
