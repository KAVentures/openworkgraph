"""Source-first case context: retained evidence only, no inferred authority."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from server import open_work_case_context as case_mod
from server import local_reference_lookup as lookup

ITEM = "owg:w:" + "a" * 32
OBSERVED = "owg:r:" + "b" * 32
NATIVE = "in_1234567890ABCDEF"


def scan(**opts):
    assert opts["explicit_scan_authorized"] is True
    assert callable(opts["candidate_enricher"])
    item = opts["candidate_enricher"](NATIVE, {
        "work_item_ref": ITEM,
        "kind": "open_invoice",
        "past_due_date": True,
        "historically_observed_in_owg": "not_checked",
    })
    return {"availability": "source_reported", "verified_at": "2026-10-19T10:00:00+00:00",
            "source_coverage_complete": True, "candidates": [item]}


def base_graph(ref, *, include_source_status=False):
    assert ref == OBSERVED
    assert include_source_status is False
    return {
        "record_observed": True,
        "record_type": {"provider": "stripe", "kind": "invoice"},
        "record_identity_complete_within_retained_window": True,
        "observed_event_count": 3, "observed_session_count": 2,
        "observed_actor_count": 1, "timeline": [
            {"event_id": "e-1", "observed_action_type": "browser_click",
             "source": "browser_extension", "observed_at": "2026-10-10T09:00:00Z"}
        ], "source_event_limit_reached": False,
    }


def history_lookup(**kwargs):
    assert kwargs == {
        "provider": "stripe", "resource_kind": "invoice",
        "host": "dashboard.stripe.com", "locator": "invoice:" + NATIVE,
        "limit": 8,
    }
    return [OBSERVED]


def test_context_links_source_case_to_same_exact_retained_record_only():
    context = case_mod.stripe_open_work_case_context(
        ITEM, explicit_scan_authorized=True, inventory_scan=scan,
        history_lookup=history_lookup, record_reader=base_graph,
    )
    assert context["availability"] == "source_reported"
    assert context["source_evidence"]["source_state"] == "open"
    assert context["related_owg_evidence"][0]["resource_ref"] == OBSERVED
    assert context["related_owg_evidence"][0]["observed_session_count"] == 2
    assert context["candidate"]["historically_observed_in_owg"] == "yes"
    assert context["declared_business_policy"] == "not_supplied"
    assert context["customer_contact_authorized"] is False
    assert context["agent_execution_authorized"] is False
    assert context["verified_customer_outcome"] is False
    for value in (NATIVE, "VERY_PRIVATE", "acct_1234567890ABCDEF"):
        assert value not in str(context)


def test_ref_not_yet_observed_still_has_source_state_without_fake_history():
    result = case_mod.stripe_open_work_case_context(
        ITEM, explicit_scan_authorized=True, inventory_scan=scan,
        history_lookup=lambda **kwargs: [], record_reader=lambda *_a, **_k: pytest.fail("history access"),
    )
    assert result["related_owg_evidence"] == []
    assert result["candidate"]["historically_observed_in_owg"] == "no_matching_retained_reference"
    assert result["source_evidence"]["source_state"] == "open"


def test_revoked_expired_or_conflicting_history_cannot_be_reported_as_found():
    for graph_value in (
        {"record_observed": False},
        {"record_observed": True, "record_type": {"provider": "stripe", "kind": "refund"}},
        {**base_graph(OBSERVED), "source_event_limit_reached": True},
        {**base_graph(OBSERVED), "record_identity_complete_within_retained_window": False},
    ):
        result = case_mod.stripe_open_work_case_context(
            ITEM, explicit_scan_authorized=True, inventory_scan=scan,
            history_lookup=history_lookup, record_reader=lambda *_a, **_k: graph_value,
        )
        assert result["related_owg_evidence"] == []
        assert result["agent_execution_authorized"] is False


def test_no_scan_when_user_has_not_explicitly_authorized():
    calls = []
    result = case_mod.stripe_open_work_case_context(
        ITEM, inventory_scan=lambda **kw: calls.append(kw),
    )
    assert result["availability"] == "unknown"
    assert calls == []


@pytest.mark.parametrize("invalid", [
    "in_1234567890ABCDEF", "owg:w:"+"Z"*32, "owg:r:"+"a"*32, "",
])
def test_guessed_or_invalid_item_reference_rejected_without_source_reads(invalid):
    with pytest.raises(ValueError):
        case_mod.stripe_open_work_case_context(
            invalid, explicit_scan_authorized=True,
            inventory_scan=lambda **kw: pytest.fail("provider scanned"),
        )


def test_disappeared_source_case_unknown_not_finished():
    def absent(**opts):
        return {"availability": "source_reported", "source_coverage_complete": True,
                "candidates": []}
    result = case_mod.stripe_open_work_case_context(
        ITEM, explicit_scan_authorized=True, inventory_scan=absent,
    )
    assert result["availability"] == "unknown"
    assert result["reason"] == "case_no_longer_open_or_reference_invalid"
    assert result["verified_customer_outcome"] is False


def test_incomplete_inventory_miss_never_infers_case_resolved():
    result = case_mod.stripe_open_work_case_context(
        ITEM, explicit_scan_authorized=True,
        inventory_scan=lambda **opts: {
            "availability": "source_reported", "source_coverage_complete": False,
            "candidates": [],
        },
    )
    assert result["reason"] == "case_not_in_bounded_inventory"


def test_local_inverse_reference_lookup_scoped_and_no_native_ids_returned(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    ref2 = "owg:r:"+"c"*32
    lookup.remember_resource_reference(
        {"resource_ref": OBSERVED, "provider": "stripe", "resource_kind": "invoice",
         "host": "dashboard.stripe.com"}, "invoice:"+NATIVE,
    )
    lookup.remember_resource_reference(
        {"resource_ref": ref2, "provider": "stripe", "resource_kind": "refund",
         "host": "dashboard.stripe.com"}, "invoice:"+NATIVE,
    )
    result = lookup.find_observed_resource_references(
        provider="stripe", resource_kind="invoice", host="dashboard.stripe.com",
        locator="invoice:"+NATIVE,
    )
    assert result == [OBSERVED]
    assert NATIVE not in str(result)
    lookup.forget_references([OBSERVED])
    assert lookup.find_observed_resource_references(
        provider="stripe", resource_kind="invoice", host="dashboard.stripe.com",
        locator="invoice:"+NATIVE,
    ) == []


def test_local_dashboard_case_route_requires_session_without_source_read(tmp_path):
    import os
    import subprocess
    import sys

    script = """
import server.secure_app
from fastapi.testclient import TestClient
from server.secure_app import app
c = TestClient(app)
r = c.get('/v1/open-work/stripe-preview/case', params={
    'work_item_ref': 'owg:w:' + 'a'*32, 'confirm_scan': 'true'})
assert r.status_code in (401, 403), r.text
"""
    env = os.environ.copy()
    env["WORKFLOW_OBSERVER_DATA"] = str(tmp_path / "data")
    env["WORKFLOW_OBSERVER_AUTH_DIR"] = str(tmp_path / "auth")
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(Path(__file__).resolve().parents[1]), capture_output=True,
        text=True, env=env, timeout=75 if os.name == "nt" else 35,
    )
    assert result.returncode == 0, result.stderr[-3000:]
