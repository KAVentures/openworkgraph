from __future__ import annotations

import json
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION = "0.111.0"


def test_release_version_sources_are_aligned():
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    manifest = json.loads((ROOT / "mcpb" / "manifest.json").read_text(encoding="utf-8"))
    sdk_python = tomllib.loads((ROOT / "sdk" / "python" / "pyproject.toml").read_text(encoding="utf-8"))
    sdk_node = json.loads((ROOT / "sdk" / "typescript" / "package.json").read_text(encoding="utf-8"))

    assert version == EXPECTED_VERSION
    assert pyproject["project"]["version"] == EXPECTED_VERSION
    assert manifest["version"] == EXPECTED_VERSION
    assert sdk_python["project"]["version"] == EXPECTED_VERSION
    assert sdk_node["version"] == EXPECTED_VERSION


def test_release_notes_are_current_and_version_driven():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert 'VERSION="$(tr -d' in workflow
    assert 'TAG="v${VERSION}"' in workflow
    assert "v0.53" not in workflow
    assert "${TAG}" in workflow
    assert "Agent evidence is local-only by default" in workflow
    assert "dedicated privacy-safe agent-run table" in workflow
    assert "gateway.local_policy.allow_agent_events=true" in workflow
    assert "`gateway.local_policy.allow_agent_events" not in workflow
    assert "compact MCP" in workflow
    assert "legacy 24-tool" in workflow
    assert "Readable procedural feedback" in workflow
    assert "stable structural identity layer" in workflow
    assert "Agent setup control plane" in workflow
    assert "telemetry actually observed" in workflow
    assert "Custom harnesses" in workflow
    assert "First useful reconstruction" in workflow
    assert "### v0.111: provenance-safe agent Working Detail" in workflow
    assert "workspace-relative file" in workflow
    assert "without persisting raw tool output" in workflow
    assert "Import last 7 days is a separate explicit action" in workflow
    v111_section = workflow.split("### v0.111: provenance-safe agent Working Detail", 1)[1].split("### v0.110: evidence-first MCP guidance and safer first-run history", 1)[0]
    assert "`" not in v111_section
    assert "### v0.110: evidence-first MCP guidance and safer first-run history" in workflow
    assert "MCP initialize guidance" in workflow
    assert "Packaged MCPB smoke test" in workflow
    assert "First-run retention grace" in workflow
    assert "### v0.109: native agent session continuity" in workflow
    assert "visible user/assistant messages are OFF by default" in workflow
    assert "allow_agent_session_messages" in workflow
    assert "agent-sessions:read" in workflow
    assert "### v0.108: full context, personal details tokenized" in workflow
    assert "pkg-agent-sdk" in workflow
    assert "OpenWorkGraph-Agent-Python.py" in workflow
    assert "OpenWorkGraph-Agent-Node.mjs" in workflow
    assert "OpenWorkGraph-Agent-Node.d.ts" in workflow
    changelog = (ROOT / "docs" / "CHANGELOG_V0111.md").read_text(encoding="utf-8")
    assert "Working Detail" in changelog
    assert "hidden reasoning" in changelog
    assert "raw tool output" in changelog


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
