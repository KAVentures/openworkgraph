"""Jira source-of-truth slice for issue 226: status, audit, privacy and consent."""
from __future__ import annotations

import json
from types import SimpleNamespace

from server.jira_source_evidence import jira_issue_receipt, _safe_audit
from server.record_graph import record_graph

REF_JIRA = "owg:r:" + "c" * 32
REF_OTHER = "owg:r:" + "d" * 32
HOST = "private-team.atlassian.net"
ENV = {
    "OWG_JIRA_SOURCE_READ_ENABLED": "true",
    "OWG_JIRA_HOST": HOST,
    "OWG_JIRA_ACCOUNT_EMAIL": "fictional@example.org",
    "OWG_JIRA_API_TOKEN": "synthetic-not-real-token",
}
INFO = {
    "provider": "jira", "resource_kind": "issue",
    "host": HOST, "resolver_locator": "TASK-12",
}


def _row(n: int, ref: str = REF_JIRA, provider: str = "jira",
         kind: str = "issue", host: str = HOST) -> dict:
    return {
        "event_id": f"evt-{n}", "event_type": "browser_click",
        "observed_at": f"2026-10-15T12:00:0{n}Z",
        "session_id": f"session-{n}", "actor_id": f"actor-{n}",
        "metadata": {"resource_reference": {
            "resource_ref": ref, "provider": provider,
            "resource_kind": kind, "host": host,
        }},
    }


def _reader(*, state="done", resolution=True, history=True):
    calls = []
    def fetch(url, params, auth):
        calls.append((url, params, auth))
        assert url.startswith(f"https://{HOST}/rest/api/3/issue/TASK-12")
        if url.endswith("/changelog"):
            if not history:
                raise ValueError("source_unavailable")
            return {
                "total": 3, "isLast": True, "values": [
                    {"id": "history-1", "created": "2026-10-15T08:00:00Z", "author": {
                        "displayName": "SENSITIVE TEST NAME"},
                     "items": [
                        {"field": "assignee", "from": "acct-old", "to": "acct-new",
                         "fromString": "SECRET USER", "toString": "PRIVATE USER"},
                        {"field": "summary", "fromString": "secret patient notes"},
                    ]},
                    {"id": "history-2", "created": "2026-10-15T09:00:00Z",
                     "items": [{"field": "status", "fromString": "In Progress",
                                "toString": "Done"}]},
                    {"id": "history-3", "created": "2026-10-15T10:00:00Z",
                     "items": [{"field": "assignee", "from": "acct-new", "to": "acct-final"}]},
                ],
            }
        return {
            "key": "TASK-12", "fields": {
                "status": {"name": "Customer sensitive title",
                           "statusCategory": {"key": state}},
                "resolution": {"name": "Fixed"} if resolution else None,
                "resolutiondate": "2026-10-15T10:00:00+0000" if resolution else None,
                "updated": "2026-10-15T10:00:00Z",
                "description": "Never return this secret field",
            },
        }
    return fetch, calls


def test_opt_in_off_does_not_resolve_identifier_or_use_network():
    called = []
    receipt = jira_issue_receipt(
        REF_JIRA, environment={**ENV, "OWG_JIRA_SOURCE_READ_ENABLED": "false"},
        resolver=lambda *_: called.append("resolver"),
        reader=lambda *_args: called.append("network"),
    )
    assert receipt["reason"] == "jira_source_read_opt_in_off"
    assert called == []


def test_jira_tenant_is_pinned_to_exact_permitted_origin():
    called = []
    for altered in [
        {**INFO, "host": "other-team.atlassian.net"},
        {**INFO, "host": "private-team.atlassian.net.evil.test"},
        {**INFO, "resolver_locator": "../../rest/api/3/user"},
        {**INFO, "provider": "salesforce"},
    ]:
        receipt = jira_issue_receipt(
            REF_JIRA, environment=ENV, resolver=lambda *_: altered,
            reader=lambda *_a: called.append("network"),
        )
        assert receipt["availability"] == "unknown"
    assert called == []


def test_jira_resolution_is_verified_while_customer_success_is_not():
    reader, calls = _reader()
    receipt = jira_issue_receipt(
        REF_JIRA, environment=ENV, resolver=lambda *_: INFO,
        reader=reader, key_factory=lambda: "local-synthetic-hmac-secret",
    )
    assert len(calls) == 2
    assert calls[0][1] == {"fields": "status,resolution,resolutiondate,updated"}
    assert calls[1][1] == {"startAt": 0, "maxResults": 40}
    assert receipt["availability"] == "source_reported"
    assert receipt["source_state"] == "done"
    assert receipt["verified_business_outcome"] is False
    assert receipt["verified_operational_outcome"]["kind"] == "jira_issue_resolved"
    assert receipt["verified_operational_outcome"]["verified"] is True
    assert receipt["verified_human_handoffs"] == 0
    assert receipt["assignment_transitions_verified"] == 2
    assert receipt["audit_complete"] is True
    assert all("actor:" in str(row) for row in receipt["review_audit_events"] if row["action"] == "assignee_transition")
    safe = json.dumps(receipt, sort_keys=True)
    for secret in ("TASK-12", HOST, "SENSITIVE", "SECRET", "PRIVATE USER",
                   "acct-old", "acct-new", "acct-final", "patient notes",
                   "fictional@example.org", "synthetic-not-real-token"):
        assert secret not in safe


def test_unresolved_issue_does_not_claim_verified_business_outcome():
    fetch, _ = _reader(state="indeterminate", resolution=False)
    r = jira_issue_receipt(REF_JIRA, environment=ENV, resolver=lambda *_: INFO, reader=fetch)
    assert r["source_state"] == "indeterminate"
    assert r["verified_business_outcome"] is False
    assert r["verified_operational_outcome"]["verified"] is False


def test_conflicting_status_resolved_fails_closed():
    fetch, _ = _reader(state="new", resolution=True)
    r = jira_issue_receipt(REF_JIRA, environment=ENV, resolver=lambda *_: INFO, reader=fetch)
    assert r["availability"] == "unknown"
    assert r["reason"] == "jira_resolution_status_conflict"


def test_optional_audit_failure_keeps_only_authoritative_status():
    fetch, _ = _reader(history=False)
    r = jira_issue_receipt(REF_JIRA, environment=ENV, resolver=lambda *_: INFO, reader=fetch)
    assert r["verified_business_outcome"] is False
    assert r["verified_operational_outcome"]["verified"] is True
    assert r["audit_available"] is False
    assert r["audit_complete"] is False
    assert r["verified_human_handoffs"] == 0


def test_partial_audit_does_not_claim_no_handoffs_or_rework():
    history = {"values": [{"id": "1", "created": "2026-10-15T10:00:00Z",
                "items": [{"field": "assignee", "from": "u1", "to": "u2"}]}],
               "total": 30, "isLast": False}
    rows, truncated, count = _safe_audit(history, "TASK-12", key=b"safe-testing-secret")
    assert len(rows) == 1
    assert truncated is True
    assert count == 1


def test_stale_wrong_issue_identity_cannot_borrow_another_record_outcome():
    def fetch(url, params, auth):
        return {"key": "TASK-99", "fields": {"status": {
            "statusCategory": {"key": "done"}}, "resolution": {"name": "Fixed"},
            "resolutiondate": "2026-10-15T10:00:00Z"}}
    r = jira_issue_receipt(REF_JIRA, environment=ENV, resolver=lambda *_: INFO, reader=fetch)
    assert r["reason"] == "jira_issue_identity_mismatch"


def test_record_graph_joins_only_confirmed_jira_record_and_separates_interleaving():
    invoked = []
    def source(ref):
        invoked.append(ref)
        return {"provider": "jira", "availability": "source_reported",
                "verified_operational_outcome": {"kind": "jira_issue_resolved",
                                                 "verified": True}}
    events = [_row(1), _row(2, REF_OTHER), _row(3),
              _row(4, REF_OTHER)]
    graph = record_graph(REF_JIRA, include_source_status=True,
                         _raw_events=events, source_reader=source)
    assert graph["observed_event_count"] == 2
    assert graph["source_log_join_supported"] is True
    assert graph["source_evidence"]["verified_operational_outcome"]["verified"] is True
    assert graph["verified_customer_outcome"] is False
    assert invoked == [REF_JIRA]
    missing = record_graph(REF_JIRA, include_source_status=True,
                            _raw_events=[_row(1, REF_OTHER)], source_reader=source)
    assert missing["source_evidence"]["availability"] == "unknown"
    assert invoked == [REF_JIRA]


def test_source_host_conflict_blocks_lookup():
    invoked = []
    rows = [_row(1), _row(2, host="other-team.atlassian.net")]
    g = record_graph(REF_JIRA, include_source_status=True, _raw_events=rows,
                     source_reader=lambda ref: invoked.append(ref))
    assert g["record_observed"] is False
    assert invoked == []


def test_status_reversal_is_structural_evidence_not_verified_failed_work():
    base_reader, _ = _reader()
    def reader(url, params, auth):
        if not url.endswith("/changelog"):
            return base_reader(url, params, auth)
        return {"total": 2, "isLast": True, "values": [
            {"id": "history-11", "created": "2026-10-15T08:00:00Z",
             "items": [{"field": "status", "from": "10001", "to": "10002",
                        "fromString": "SECRET OLD", "toString": "SECRET NEW"}]},
            {"id": "history-12", "created": "2026-10-15T09:00:00Z",
             "items": [{"field": "status", "from": "10002", "to": "10001"}]},
        ]}
    result = jira_issue_receipt(REF_JIRA, environment=ENV, resolver=lambda *_: INFO,
                                reader=reader, key_factory=lambda: "local-test-key")
    assert result["observed_structural_status_reversals"] == 1
    assert result["status_reversal_means_business_failure"] is False
    assert result["missing_status_reversals_means_no_rework"] is False
    assert "SECRET" not in str(result)
