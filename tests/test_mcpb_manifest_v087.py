from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_COMPACT_TOOLS = {
    "get_current_work_context",
    "get_context_pulse",
    "list_history",
    "search_work",
    "get_workflow_trace",
    "get_work_profile",
    "find_repeated_workflows",
    "get_workflow_evidence",
    "get_task_context",
    "how_did_similar_runs_go",
    "get_agent_runs",
    "get_agent_handoff",
    "get_playbooks",
    "get_automation_capabilities",
    "read_evidence_file",
}


def test_mcpb_manifest_advertises_exact_compact_default_surface():
    manifest = json.loads((ROOT / "mcpb" / "manifest.json").read_text(encoding="utf-8"))
    names = {str(item.get("name") or "") for item in manifest.get("tools") or []}
    assert names == EXPECTED_COMPACT_TOOLS
    description = str(manifest.get("long_description") or "")
    assert "v0.116" in description
    assert "explicitly selected" in description
    assert "legacy 24-tool" in description


def test_mcp_architecture_docs_name_the_exact_compact_default_surface():
    docs = (ROOT / "docs" / "MCP_ARCHITECTURE.md").read_text(encoding="utf-8")
    compact = docs.split("```text", 2)[2].split("```", 1)[0]
    listed = {line.strip() for line in compact.splitlines() if line.strip()}
    assert listed == EXPECTED_COMPACT_TOOLS
    assert "MCP `instructions` block" in docs
    assert "next_cursor" in docs and "has_more" in docs
    assert "explicit execution" in docs.lower()
    assert "navigation" in docs.lower()


def test_experimental_governance_docs_preserve_rest_compatibility_boundary():
    docs = (ROOT / "docs" / "EXPERIMENTAL_GOVERNANCE.md").read_text(encoding="utf-8")
    assert "OWG_EXPERIMENTAL_GOVERNANCE=1" in docs
    assert "does **not**" in docs
    assert "unmount existing REST routes" in docs
    assert "get_action_policy_advisory" in docs
    assert "get_governed_context_pack" in docs
