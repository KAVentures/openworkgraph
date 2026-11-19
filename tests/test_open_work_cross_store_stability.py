"""Cross-store read consistency regression for reviewed Open Work outcomes."""
import asyncio

import pytest
from fastapi import HTTPException

from server import open_work_declaration_routes as routes


def _run_review(monkeypatch, *, mutate):
    case = {"work_item_ref": "case"}
    signature = (("case", 1),)
    monkeypatch.setattr(routes, "list_case_declarations", lambda scope: {"items": [{"work_item_ref": "case", "revision": 1, "edit_token": "token"}]})
    monkeypatch.setattr(routes, "admitted_case_dossier_snapshot", lambda *a, **kw: (case, signature))
    monkeypatch.setattr(routes, "select_dependency_status_targets", lambda *a, **kw: ([case], 0))
    monkeypatch.setattr(routes, "_operation_ledger_guard", lambda *a, **kw: None)
    monkeypatch.setattr(routes, "_read_or_create_secret", lambda *a, **kw: "test-secret")

    async def source_ok(scope):
        return None

    monkeypatch.setattr(routes, "_recheck_source", source_ok)
    reads = {"provider": 0, "human": 0}

    def provider(*a, **kw):
        reads["provider"] += 1
        return {"events": [reads["provider"] if mutate == "provider" else 1]}

    def human(*a, **kw):
        reads["human"] += 1
        return {"events": [reads["human"] if mutate == "human" else 1]}

    monkeypatch.setattr(routes, "read_operation_evidence", provider)
    monkeypatch.setattr(routes, "read_human_outcomes", human)
    monkeypatch.setattr(routes, "compose_outcome_evidence_triage", lambda *a, **kw: {"reviewed": True})
    payload = {"max_cases": 1, "work_item_ref": "case", "expected_revision": 1, "expected_edit_token": "token"}
    return asyncio.run(routes._load_review_triage("scope", payload)), reads


def test_cross_store_review_rechecks_both_journals(monkeypatch):
    result, reads = _run_review(monkeypatch, mutate=None)
    assert result[0] == {"reviewed": True}
    assert reads == {"provider": 2, "human": 2}


@pytest.mark.parametrize("mutate", ["provider", "human"])
def test_cross_store_review_rejects_concurrent_ledger_change(monkeypatch, mutate):
    with pytest.raises(HTTPException) as exc:
        _run_review(monkeypatch, mutate=mutate)
    assert exc.value.status_code == 409
