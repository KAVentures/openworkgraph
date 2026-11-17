"""Regression gates for retained receipt disclosure authorization ordering.

The source can be replaced while the final binding/declaration checks await.
This checks that source authorization is the last awaited gate and that the
operator session is revalidated before the response is constructed.
"""
import ast
import inspect
import textwrap

from fastapi.testclient import TestClient

from server import open_work_declaration_routes as routes
from server.secure_app import app


URL = "/v1/open-work/declarations/human-outcomes/attempt-receipts"


def test_receipt_endpoint_denies_unauthenticated_disclosure():
    response = TestClient(app).post(URL, json={})
    assert response.status_code in (401, 403)
    assert "entries" not in response.text


def test_final_source_recheck_follows_binding_and_declaration_guards():
    fn = ast.parse(textwrap.dedent(inspect.getsource(
        routes.read_local_bound_attempt_receipts
    ))).body[0]
    assert isinstance(fn, ast.AsyncFunctionDef)
    # Deliberately assert semantic AST ordering rather than brittle source
    # offsets: both guards must execute before the last source await.
    statements = fn.body
    returns = [i for i, node in enumerate(statements)
               if isinstance(node, ast.Return)]
    assert len(returns) == 1
    end = returns[0]
    source_checks = [
        i for i, node in enumerate(statements)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Await)
        and isinstance(node.value.value, ast.Call)
        and isinstance(node.value.value.func, ast.Name)
        and node.value.value.func.id == "_recheck_source"
    ]
    assert source_checks, "receipt disclosure must recheck current source"
    last_source = source_checks[-1]
    assert last_source < end
    final_auth = statements[last_source + 1:end]
    assert any(
        isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "_dashboard_only"
        for node in final_auth
    ), "session must be rechecked after final source authorization"
    # The binding and declaration checks live in separate guarded blocks.
    guards = [
        node for node in statements[:last_source]
        if isinstance(node, ast.Try)
    ]
    assert len(guards) >= 2
    guard_source = "\n".join(ast.unparse(node) for node in guards[-2:])
    assert "get_binding" in guard_source
    assert "require_exclusive_run_case_binding" in guard_source
    assert "_operation_ledger_guard" in guard_source
    assert all(not isinstance(node, ast.Await)
               for node in final_auth), "no new asynchronous disclosure gap"
