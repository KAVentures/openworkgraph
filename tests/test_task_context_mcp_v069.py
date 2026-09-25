from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_secure_task_context_mcp_uses_existing_access_and_audit_boundary():
    source = (ROOT / "mcp_server" / "secure_runtime.py").read_text(encoding="utf-8")
    start = source.index("def get_task_context(")
    next_marker = source.find("\n\nmcp =", start)
    block = source[start: next_marker if next_marker >= 0 else len(source)]

    assert 'name = "get_task_context"' in block
    assert "core._begin(name)" in block
    assert "core._finish(name, result)" in block
    assert 'secure_get("/v1/task-context"' in block
    assert '"family_key": family_key' in block
    assert '"task_family": task_family' in block
    assert '"current_steps": current_steps' in block
    assert "task_description" not in block
    assert "prompt" not in block.lower()


def test_task_context_docs_and_service_do_not_offer_automatic_execution_or_fuzzy_text():
    service = (ROOT / "server" / "task_context.py").read_text(encoding="utf-8")
    docs = (ROOT / "docs" / "TASK_CONTEXT.md").read_text(encoding="utf-8")

    assert '"automatic_execution": False' in service
    assert '"automatic_policy_enforcement": False' in service
    assert '"free_text_task_matching": False' in service
    assert "task_description" not in service
    assert "does not fuzzy-match arbitrary natural-language task descriptions" in docs
    assert "Repeated behavior never becomes policy" in docs
