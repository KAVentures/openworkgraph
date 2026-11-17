"""Regression guard: retained receipts must reauthorize after the final source await.

This static guard supplements (but does not replace) live concurrent-revocation
integration tests. It prevents a future refactor from accidentally moving the
last binding/declaration checks before the final source recheck.
"""
import ast
from pathlib import Path


def test_receipt_disclosure_rechecks_binding_and_review_after_final_source_await():
    path = Path(__file__).resolve().parents[1] / "server" / "open_work_declaration_routes.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    route = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                 and node.name == "read_local_bound_attempt_receipts")
    calls = []
    for node in ast.walk(route):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
            calls.append((node.lineno, name, ast.get_source_segment(path.read_text(encoding="utf-8"), node) or ""))
    source_checks = [line for line, name, _ in calls if name == "_recheck_source"]
    assert len(source_checks) >= 2
    last_source = max(source_checks)
    binding = [line for line, name, _ in calls if name == "to_thread" and
               "get_binding" in _ and line > last_source]
    exclusive = [line for line, name, _ in calls if name == "to_thread" and
                 "require_exclusive_run_case_binding" in _ and line > last_source]
    declaration = [line for line, name, _ in calls if name == "to_thread" and
                   "_operation_ledger_guard" in _ and line > last_source]
    return_lines = [node.lineno for node in ast.walk(route) if isinstance(node, ast.Return)]
    assert binding and exclusive and declaration
    assert max(binding + exclusive + declaration) < max(return_lines)
