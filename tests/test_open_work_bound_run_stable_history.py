"""Opt-in stable read of independently HMAC-checked case-wide histories."""
import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from server import open_work_declaration_routes as routes
from test_open_work_bound_run_evidence_review import _prepare, Request, CASE


def _provider(seq=1):
    return {"work_item_ref": CASE, "events": [{"sequence": seq}],
            "provider_check_count": 1, "disputed_check_count": 0,
            "last_observed_provider_state": "paid"}


def _human(status="unknown"):
    return {"work_item_ref": CASE, "events": [],
            "event_count": 0, "latest_human_assessed_status": status}


def test_stable_histories_are_returned_with_explicit_nonatomic_boundary(monkeypatch, tmp_path):
    async def exercise():
        payload, source, _ = _prepare(monkeypatch, tmp_path)
        payload["confirm_stable_snapshot"] = True
        provider = Mock(return_value=_provider())
        human = Mock(return_value=_human())
        monkeypatch.setattr(routes, "read_operation_evidence", provider)
        monkeypatch.setattr(routes, "read_human_outcomes", human)
        result = await routes.review_local_bound_run_evidence_history(
            Request(), SimpleNamespace(headers={}))
        assert provider.call_count == human.call_count == 2
        assert result["double_read_ledger_stability_checked"] is True
        assert result["atomic_cross_ledger_snapshot"] is False
        assert result["customer_outcome_verified"] is False
        assert result["evidence_scope"] == "case_wide_not_run_attributed"
        source.assert_not_called()
    asyncio.run(exercise())


@pytest.mark.parametrize("changed", ["provider", "human"])
def test_changed_ledger_refuses_historical_response(monkeypatch, tmp_path, changed):
    async def exercise():
        payload, source, _ = _prepare(monkeypatch, tmp_path)
        payload["confirm_stable_snapshot"] = True
        provider = Mock(side_effect=[_provider(1), _provider(2)] if changed == "provider"
                        else [_provider(), _provider()])
        human = Mock(side_effect=[_human(), _human("not_met")] if changed == "human"
                     else [_human(), _human()])
        monkeypatch.setattr(routes, "read_operation_evidence", provider)
        monkeypatch.setattr(routes, "read_human_outcomes", human)
        with pytest.raises(HTTPException) as exc:
            await routes.review_local_bound_run_evidence_history(
                Request(), SimpleNamespace(headers={}))
        assert exc.value.status_code == 409
        source.assert_not_called()
    asyncio.run(exercise())


def test_stable_snapshot_is_opt_in_and_type_checked(monkeypatch, tmp_path):
    async def exercise():
        payload, _, _ = _prepare(monkeypatch, tmp_path)
        payload["confirm_stable_snapshot"] = "true"
        read = Mock()
        monkeypatch.setattr(routes, "read_operation_evidence", read)
        with pytest.raises(HTTPException) as exc:
            await routes.review_local_bound_run_evidence_history(
                Request(), SimpleNamespace(headers={}))
        assert exc.value.status_code == 422
        read.assert_not_called()
    asyncio.run(exercise())
