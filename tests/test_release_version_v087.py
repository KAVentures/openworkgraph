from __future__ import annotations

import json
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION = "0.115.0"


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
    workflow_lower = workflow.lower()
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
    assert "### v0.115: human-first demo and distribution refresh" in workflow
    assert "ordinary human workflows" in workflow_lower
    assert "ai agent is not required" in workflow_lower
    assert "try_demo_openworkgraph" in workflow_lower
    assert "### v0.114: configurable browser context with keyed correlation" in workflow
    assert "privacy-first" in workflow_lower
    assert "installation-keyed" in workflow_lower
    assert "browser sensor update" in workflow_lower
    assert "### v0.113: frontier-aware automation interpretation" in workflow
    assert "missing historical payload" in workflow_lower
    assert "next autonomy boundary" in workflow_lower
    assert "consequence-aware" in workflow_lower
    assert "evaluation corpus" in workflow_lower
    assert "### v0.112: trustworthy AI answers and agent-run accounting" in workflow
    assert "opaque cross-sensor identity" in workflow
    assert "Token usage" in workflow
    assert "compact by default" in workflow
    assert "local today" in workflow
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

    changelog = (ROOT / "docs" / "CHANGELOG_V0115.md").read_text(encoding="utf-8")
    changelog_lower = changelog.lower()
    assert "human-only" in changelog_lower
    assert "ai agent" in changelog_lower
    assert "copy/paste" in changelog_lower
    assert "try_demo_openworkgraph" in changelog_lower
    assert "0.115.0" in changelog

    prior_114 = (ROOT / "docs" / "CHANGELOG_V0114.md").read_text(encoding="utf-8").lower()
    assert "privacy-first" in prior_114
    assert "rich enterprise" in prior_114
    assert "hmac" in prior_114

    prior = (ROOT / "docs" / "CHANGELOG_V0113.md").read_text(encoding="utf-8").lower()
    assert "historical replayability" in prior
    assert "next autonomy boundary" in prior
    assert "consequence-aware" in prior
    assert "underestimation" in prior
    assert "overreach" in prior


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
