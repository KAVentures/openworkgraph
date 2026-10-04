from __future__ import annotations

import json
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION = "0.122.0"


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
    assert "### v0.121: reliable desktop continuity" in workflow
    v121_section = workflow.split("### v0.121: reliable desktop continuity", 1)[1].split("### v0.120:", 1)[0]
    assert "start openworkgraph at login" in v121_section.lower()
    assert "bounded exponential backoff" in v121_section.lower()
    assert "intentional quit stays quit" in v121_section.lower()
    assert "five unexpected exits in five minutes" in v121_section.lower()
    assert "### v0.120: real desktop installers" in workflow
    v120_section = workflow.split("### v0.120: real desktop installers", 1)[1].split("### v0.119:", 1)[0]
    assert "no zip or terminal for normal users" in v120_section.lower()
    assert "openworkgraph-macos.pkg" in v120_section.lower()
    assert "openworkgraph-windows-setup.exe" in v120_section.lower()
    assert "same local state" in v120_section.lower()
    assert "### v0.119: easier local installation without a second runtime" in workflow
    v119_section = workflow.split("### v0.119: easier local installation without a second runtime", 1)[1].split("### v0.118:", 1)[0]
    assert "release-matched bootstrap installers" in v119_section.lower()
    assert "installer smoke coverage" in v119_section.lower()
    assert "explicit uninstall" in v119_section.lower()
    assert "no capture semantics change" in v119_section.lower()
    assert "### v0.118: Basic and Advanced views, one Privacy tab" in workflow
    v118_section = workflow.split("### v0.118: Basic and Advanced views, one Privacy tab", 1)[1].split("### v0.117:", 1)[0]
    assert "`" not in v118_section and "<" not in v118_section
    assert "never record" in v118_section.lower()
    assert "off on a new install" in v118_section
    assert "### v0.117: a clearer, calmer dashboard" in workflow
    v117_section = workflow.split("### v0.117: a clearer, calmer dashboard", 1)[1].split("### v0.116:", 1)[0]
    assert "`" not in v117_section and "<" not in v117_section
    assert "timeline shows again" in v117_section.lower()
    assert "fails closed" in v117_section
    assert "browser sensor 1.14.0 remains current" in v117_section.lower()
    assert "### v0.116: evidence-first workflow skill drafting" in workflow
    assert "get_workflow_evidence" in workflow
    assert "explicitly selected" in workflow_lower
    assert "support counts" in workflow_lower
    assert "external ai" in workflow_lower
    assert "### v0.115: human-first demo and distribution refresh" in workflow
    assert "ordinary human workflows" in workflow_lower
    assert "ai agent observation is an optional additional evidence stream" in workflow_lower
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

    changelog = (ROOT / "docs" / "CHANGELOG.md").read_text(encoding="utf-8")
    changelog_lower = changelog.lower()
    assert "0.121.0" in changelog
    assert "Start OpenWorkGraph at Login" in changelog
    assert "crash" in changelog_lower
    assert "0.120.0" in changelog
    assert "desktop installer" in changelog_lower
    assert "terminal" in changelog_lower
    assert "0.119.0" in changelog
    assert "release-matched" in changelog_lower
    assert "uninstall" in changelog_lower
    assert "0.118.0" in changelog and "Privacy tab" in changelog and "Never record" in changelog
    assert "off on a new install" in changelog
    assert "0.117.0" in changelog
    assert "timeline shows again" in changelog_lower
    assert "evidence keeps its context" in changelog_lower
    assert "settings tab" in changelog_lower
    assert "every 5 seconds" in changelog_lower
    assert "evidence-first" in changelog_lower
    assert "explicit execution" in changelog_lower
    assert "redacted" in changelog_lower
    assert "stored privacy-hardened" in changelog_lower
    assert "13 tools" in changelog_lower
    assert "0.116.0" in changelog
    assert "human-only" in changelog_lower
    assert "ai agent" in changelog_lower
    assert "copy/paste" in changelog_lower
    assert "try_demo_openworkgraph" in changelog_lower
    assert "privacy-first" in changelog_lower
    assert "rich enterprise" in changelog_lower
    assert "hmac" in changelog_lower
    assert "historical replayability" in changelog_lower
    assert "next autonomy boundary" in changelog_lower
    assert "consequence-aware" in changelog_lower
    assert "underestimation" in changelog_lower
    assert "overreach" in changelog_lower


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
