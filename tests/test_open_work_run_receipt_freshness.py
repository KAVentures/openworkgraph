"""Regression tests for run-receipt freshness classification contract."""
import ast
from pathlib import Path


def test_route_uses_integrity_checked_evidence_for_freshness():
    source = Path(__file__).resolve().parents[1] / "server/open_work_declaration_routes.py"
    tree = ast.parse(source.read_text())
    fn = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)
              and node.name == "bound_run_receipt_journal")
    names = {node.id for node in ast.walk(fn) if isinstance(node, ast.Name)}
    assert {"read_operation_evidence", "read_human_outcomes", "fresh_digest", "latest_digest"} <= names


def test_freshness_is_read_only_and_does_not_claim_run_causality():
    source = (Path(__file__).resolve().parents[1] / "server/open_work_declaration_routes.py").read_text()
    body = source.split('async def bound_run_receipt_journal(', 1)[1]
    assert '"case_evidence_scope"' in body
    assert '"case_wide_not_run_causal"' in body
    assert '"current_review_count"' in body
    assert '"stale"' in body
    assert '"current"' in body
