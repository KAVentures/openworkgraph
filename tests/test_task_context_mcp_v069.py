from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_secure_task_context_mcp_uses_existing_access_protection_and_audit_boundary():
    source = (ROOT / "mcp_server" / "secure_runtime.py").read_text(encoding="utf-8")
    start = source.index("def get_task_context(")
    next_marker = source.find("\n\n@core.mcp.tool()", start)
    block = source[start: next_marker if next_marker >= 0 else len(source)]
    signature = block.split('"""', 1)[0]

    assert 'name = "get_task_context"' in block
    assert "core._begin(name)" in block
    assert "_finish_task_context(name, result)" in block
    assert 'secure_get("/v1/task-context"' in block
    assert '"family_key": family_key' in block
    assert '"task_family": task_family' in block
    assert '"current_steps": current_steps' in block
    assert "task_description" not in signature
    assert "prompt" not in signature.lower()

    finish_start = source.index("def _finish_task_context(")
    finish_end = source.index("\n\ndef authorize_tool", finish_start)
    finish_block = source[finish_start:finish_end]
    assert "core.protect_observed_payload(source_result)" in finish_block
    assert "core._audit_tool(tool_name, protected)" in finish_block
    assert "source_api_snapshot_sha256" in finish_block
    assert "mcp_protected_snapshot_sha256" in finish_block
    assert '"model_context_consumption_attested": False' in finish_block
    assert '"model_context_use_attested": False' in finish_block
    assert '"automatic_context_injection": False' in finish_block


def test_task_context_docs_and_service_do_not_offer_automatic_execution_or_fuzzy_text():
    service = (ROOT / "server" / "task_context.py").read_text(encoding="utf-8")
    docs = (ROOT / "docs" / "TASK_CONTEXT.md").read_text(encoding="utf-8")

    assert '"automatic_execution": False' in service
    assert '"automatic_policy_enforcement": False' in service
    assert '"free_text_task_matching": False' in service
    assert "task_description" not in service
    assert "does not fuzzy-match arbitrary natural-language task descriptions" in docs
    assert "Repeated behavior never becomes policy" in docs
