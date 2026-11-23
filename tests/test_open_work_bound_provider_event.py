"""Exact event reads require current authenticated run admission and fail closed."""
import asyncio
import pytest
from fastapi import HTTPException, Request, Response
from server import open_work_declaration_routes as routes

DATA = {
    "work_item_ref": "case-1", "expected_revision": 1,
    "expected_edit_token": "token", "confirm_historical_review": True,
    "confirm_run_comparison": True, "confirm_source_verification": True,
    "max_cases": 1, "deployment_id": "dep", "run_id": "run",
    "provider_object_id": "in_123456789", "provider_operation": "invoice.finalized",
    "provider_correlation_id": "run-123", "provider_event_id": "evt_12345678",
}

def request():
    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}
    return Request({"type": "http", "method": "POST", "path": "/",
                    "headers": [(b"authorization", b"owg-session test")]}, receive)

def install(monkeypatch, *, review=None, source=None, body=None):
    monkeypatch.setattr(routes, "_dashboard_only", lambda req: None)
    async def scope():
        return "tenant-1"
    async def parse(req, required, optional=None):
        return dict(DATA if body is None else body)
    async def recheck(s):
        assert s == "tenant-1"
        if source:
            source()
    async def admitted(req, resp):
        if review:
            return await review(req, resp)
        return {"case_ref": "case-1", "receipts": {"run_ref": "run-1"}}
    monkeypatch.setattr(routes, "_require_scope", scope)
    monkeypatch.setattr(routes, "_body", parse)
    monkeypatch.setattr(routes, "_recheck_source", recheck)
    monkeypatch.setattr(routes, "read_local_combined_case_review", admitted)
    async def receipts(req, resp):
        return {"run_ref": "run-1", "entries": [{"attempt_ref": "attempt-1"}], "status": "retained_untrusted"}
    monkeypatch.setattr(routes, "read_local_bound_attempt_receipts", receipts)

def test_no_provider_read_without_retained_run(monkeypatch):
    async def denied(req, resp):
        raise HTTPException(403, "revoked binding")
    install(monkeypatch, review=denied)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.read_local_bound_provider_event(request(), Response()))
    assert exc.value.status_code == 403

def test_no_provider_read_without_explicit_confirmation(monkeypatch):
    install(monkeypatch, body={**DATA, "confirm_source_verification": False})
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.read_local_bound_provider_event(request(), Response()))
    assert exc.value.status_code == 403

def test_provider_read_revocation_never_discloses(monkeypatch):
    calls = []
    def source():
        calls.append("scope")
        if len(calls) >= 2:
            raise HTTPException(409, "revoked")
    install(monkeypatch, source=source)
    # Supply a valid provider account so this test isolates source revocation,
    # rather than failing earlier on a missing local source installation.
    from server import local_source_credentials
    monkeypatch.setattr(local_source_credentials, "_read_metadata",
                        lambda path: {"account": "acct_12345678"})
    monkeypatch.setattr(routes, "_source_scope", lambda: "tenant-1")
    from server import open_work_provider_attempt_verification as verifier
    monkeypatch.setattr(verifier, "verify_provider_attempt",
                        lambda **kwargs: {"status": "unknown", "provider_operation": "unknown",
                                          "customer_outcome": "unknown"})
    # Admission succeeds, but source consent disappears before disclosure.
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.read_local_bound_provider_event(request(), Response()))
    assert exc.value.status_code == 409

def test_source_account_unavailable_fails_closed(monkeypatch):
    install(monkeypatch)
    from server import local_source_credentials
    monkeypatch.setattr(local_source_credentials, "_read_metadata", lambda path: None)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.read_local_bound_provider_event(request(), Response()))
    assert exc.value.status_code == 403


def test_provider_account_replacement_during_read_denies_verdict(monkeypatch):
    """A source switch during provider I/O must not disclose old-account evidence."""
    install(monkeypatch)
    from server import local_source_credentials
    from server import open_work_provider_attempt_verification as verifier
    accounts = iter(["acct_12345678", "acct_87654321"])
    monkeypatch.setattr(local_source_credentials, "_read_metadata",
                        lambda path: {"account": next(accounts)})
    monkeypatch.setattr(verifier, "verify_provider_attempt",
                        lambda **kwargs: {"status": "verified_provider_event"})
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.read_local_bound_provider_event(request(), Response()))
    assert exc.value.status_code == 403


def test_revoked_source_before_provider_io_never_calls_reader(monkeypatch):
    """Even an adapter ignoring its guard must not run after source revocation."""
    install(monkeypatch)
    from server import local_source_credentials
    from server import open_work_provider_attempt_verification as verifier
    monkeypatch.setattr(local_source_credentials, "_read_metadata",
                        lambda path: {"account": "acct_12345678"})
    calls = []
    def forbidden(**kwargs):
        calls.append("verifier")
        raise AssertionError("revoked source must not invoke the provider adapter")
    monkeypatch.setattr(verifier, "verify_provider_attempt", forbidden)
    monkeypatch.setattr(routes, "_source_scope", lambda: None)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.read_local_bound_provider_event(request(), Response()))
    assert exc.value.status_code == 403
    assert calls == []


def test_current_source_guard_is_passed_to_verifier(monkeypatch):
    """The synchronous provider boundary must receive a live authorization guard."""
    install(monkeypatch)
    from server import local_source_credentials
    from server import open_work_provider_attempt_verification as verifier
    monkeypatch.setattr(local_source_credentials, "_read_metadata",
                        lambda path: {"account": "acct_12345678"})
    monkeypatch.setattr(routes, "_source_scope", lambda: "tenant-1")
    def verify(**kwargs):
        assert kwargs["recheck_authorization"]() is True
        return {"status": "unknown", "provider_operation": "unknown",
                "customer_outcome": "unknown"}
    monkeypatch.setattr(verifier, "verify_provider_attempt", verify)
    result = asyncio.run(routes.read_local_bound_provider_event(request(), Response()))
    assert result["receipt_persisted"] is False
    assert result["customer_outcome_verified"] is False


def test_retained_receipt_run_mismatch_denied(monkeypatch):
    """A journal from another run cannot be presented beside provider evidence."""
    install(monkeypatch)
    from server import local_source_credentials
    from server import open_work_provider_attempt_verification as verifier
    monkeypatch.setattr(local_source_credentials, "_read_metadata",
                        lambda path: {"account": "acct_12345678"})
    monkeypatch.setattr(routes, "_source_scope", lambda: "tenant-1")
    monkeypatch.setattr(verifier, "verify_provider_attempt",
                        lambda **kwargs: {"status": "unknown", "provider_operation": "unknown", "customer_outcome": "unknown"})
    async def wrong_run(req, resp):
        return {"run_ref": "other-run", "entries": []}
    monkeypatch.setattr(routes, "read_local_bound_attempt_receipts", wrong_run)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.read_local_bound_provider_event(request(), Response()))
    assert exc.value.status_code == 409


def test_retained_attempts_are_not_provider_causality(monkeypatch):
    """A same-run journal may be displayed, but never attributed to the event."""
    install(monkeypatch)
    from server import local_source_credentials
    from server import open_work_provider_attempt_verification as verifier
    monkeypatch.setattr(local_source_credentials, "_read_metadata",
                        lambda path: {"account": "acct_12345678"})
    monkeypatch.setattr(routes, "_source_scope", lambda: "tenant-1")
    monkeypatch.setattr(verifier, "verify_provider_attempt",
                        lambda **kwargs: {"status": "verified_provider_event",
                                          "provider_operation": "unknown", "customer_outcome": "unknown",
                                          "event_id": DATA["provider_event_id"]})
    result = asyncio.run(routes.read_local_bound_provider_event(request(), Response()))
    assert result["retained_attempt_receipts"]["run_ref"] == "run-1"
    assert result["retained_attempt_receipts_are_untrusted"] is True
    assert result["provider_event_to_attempt_causality_verified"] is False
    assert result["provider_event_attributed_to_agent_run"] is False
    assert result["receipt_persisted"] is False


def test_missing_retained_run_provenance_denied(monkeypatch):
    """A journal without a run identity must not accompany a provider verdict."""
    install(monkeypatch)
    from server import local_source_credentials
    from server import open_work_provider_attempt_verification as verifier
    monkeypatch.setattr(local_source_credentials, "_read_metadata",
                        lambda path: {"account": "acct_12345678"})
    monkeypatch.setattr(routes, "_source_scope", lambda: "tenant-1")
    monkeypatch.setattr(verifier, "verify_provider_attempt",
                        lambda **kwargs: {"status": "unknown", "provider_operation": "unknown", "customer_outcome": "unknown"})
    async def missing_run(req, resp):
        return {"entries": []}
    monkeypatch.setattr(routes, "read_local_bound_attempt_receipts", missing_run)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.read_local_bound_provider_event(request(), Response()))
    assert exc.value.status_code == 409


def test_retained_journal_mutation_during_disclosure_denied(monkeypatch):
    """An actor's retained journal changing after provider I/O fails closed."""
    install(monkeypatch)
    from server import local_source_credentials
    from server import open_work_provider_attempt_verification as verifier
    monkeypatch.setattr(local_source_credentials, "_read_metadata",
                        lambda path: {"account": "acct_12345678"})
    monkeypatch.setattr(routes, "_source_scope", lambda: "tenant-1")
    monkeypatch.setattr(verifier, "verify_provider_attempt",
                        lambda **kwargs: {"status": "unknown", "provider_operation": "unknown", "customer_outcome": "unknown"})
    calls = []
    async def changing(req, resp):
        calls.append(1)
        return {"run_ref": "run-1", "entries": [{"id": str(len(calls))}]}
    monkeypatch.setattr(routes, "read_local_bound_attempt_receipts", changing)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.read_local_bound_provider_event(request(), Response()))
    assert exc.value.status_code == 409
    assert len(calls) == 2


def test_source_revoked_during_last_review_denied(monkeypatch):
    """Consent loss in the last async review cannot disclose provider evidence."""
    from server import local_source_credentials
    from server import open_work_provider_attempt_verification as verifier
    monkeypatch.setattr(local_source_credentials, "_read_metadata",
                        lambda path: {"account": "acct_12345678"})
    monkeypatch.setattr(routes, "_source_scope", lambda: "tenant-1")
    monkeypatch.setattr(verifier, "verify_provider_attempt",
                        lambda **kwargs: {"status": "unknown", "provider_operation": "unknown", "customer_outcome": "unknown"})
    state = {"revoked": False, "reviews": 0}
    async def review(req, resp):
        state["reviews"] += 1
        if state["reviews"] >= 4:
            state["revoked"] = True
        return {"case_ref": "case-1", "receipts": {"run_ref": "run-1"}}
    def source():
        if state["revoked"]:
            raise HTTPException(403, "source revoked")
    install(monkeypatch, review=review, source=source)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.read_local_bound_provider_event(request(), Response()))
    assert exc.value.status_code == 403


def test_internal_admission_requests_have_json_content_type(monkeypatch):
    """Real _body requires JSON media type; synthetic subrequests must supply it."""
    parse_actual_body = routes._body
    install(monkeypatch)
    from server import local_source_credentials
    from server import open_work_provider_attempt_verification as verifier
    monkeypatch.setattr(local_source_credentials, "_read_metadata",
                        lambda path: {"account": "acct_12345678"})
    monkeypatch.setattr(routes, "_source_scope", lambda: "tenant-1")
    monkeypatch.setattr(verifier, "verify_provider_attempt",
                        lambda **kwargs: {"status": "unknown", "provider_operation": "unknown", "customer_outcome": "unknown"})
    seen = []
    async def reviewed(req, resp):
        assert req.headers.get("content-type") == "application/json"
        parsed = await parse_actual_body(req, {
            "work_item_ref", "expected_revision", "expected_edit_token",
            "confirm_historical_review", "confirm_run_comparison", "max_cases",
            "deployment_id", "run_id",
        })
        assert parsed["run_id"] == DATA["run_id"]
        seen.append("review")
        return {"case_ref": "case-1", "receipts": {"run_ref": "run-1"}}
    async def receipts(req, resp):
        assert req.headers.get("content-type") == "application/json"
        parsed = await parse_actual_body(req, {
            "work_item_ref", "expected_revision", "expected_edit_token",
            "confirm_historical_review", "confirm_run_comparison", "max_cases",
            "deployment_id", "run_id",
        })
        assert parsed["run_id"] == DATA["run_id"]
        seen.append("receipts")
        return {"run_ref": "run-1", "entries": [{"attempt_ref": "attempt-1"}]}
    # The top-level request remains stubbed, but both nested requests use the
    # actual JSON parser and its strict content-type requirement.
    monkeypatch.setattr(routes, "read_local_combined_case_review", reviewed)
    monkeypatch.setattr(routes, "read_local_bound_attempt_receipts", receipts)
    result = asyncio.run(routes.read_local_bound_provider_event(request(), Response()))
    assert result["receipt_persisted"] is False
    assert seen.count("review") >= 3
    assert seen.count("receipts") == 5


def test_missing_retained_run_blocks_provider_network_io(monkeypatch):
    """An admitted case without a matching actor journal cannot trigger Stripe IO."""
    install(monkeypatch)
    from server import open_work_provider_attempt_verification as verifier
    calls = []
    def forbidden(**kwargs):
        calls.append("provider")
        raise AssertionError("provider network read must not begin")
    monkeypatch.setattr(verifier, "verify_provider_attempt", forbidden)
    async def wrong_journal(req, resp):
        return {"run_ref": "another-run", "entries": []}
    monkeypatch.setattr(routes, "read_local_bound_attempt_receipts", wrong_journal)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.read_local_bound_provider_event(request(), Response()))
    assert exc.value.status_code == 409
    assert calls == []


def test_journal_revocation_before_provider_io_blocks_read(monkeypatch):
    """Revoked journal permission must fail before provider access."""
    install(monkeypatch)
    from server import open_work_provider_attempt_verification as verifier
    calls = []
    monkeypatch.setattr(verifier, "verify_provider_attempt",
                        lambda **kwargs: calls.append("provider"))
    async def revoked(req, resp):
        raise HTTPException(403, "receipt read revoked")
    monkeypatch.setattr(routes, "read_local_bound_attempt_receipts", revoked)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.read_local_bound_provider_event(request(), Response()))
    assert exc.value.status_code == 403
    assert calls == []


def test_both_missing_run_identities_block_provider_io(monkeypatch):
    """Two absent run IDs must not compare equal and authorize a provider read."""
    install(monkeypatch)
    from server import open_work_provider_attempt_verification as verifier
    calls = []
    monkeypatch.setattr(verifier, "verify_provider_attempt",
                        lambda **kwargs: calls.append("provider"))
    async def missing_admitted(req, resp):
        return {"case_ref": "case-1", "receipts": {}}
    async def missing_journal(req, resp):
        return {"entries": []}
    monkeypatch.setattr(routes, "read_local_combined_case_review", missing_admitted)
    monkeypatch.setattr(routes, "read_local_bound_attempt_receipts", missing_journal)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.read_local_bound_provider_event(request(), Response()))
    assert exc.value.status_code == 409
    assert calls == []


def test_empty_run_identities_block_provider_io(monkeypatch):
    """Empty strings cannot establish an admitted retained-run binding."""
    install(monkeypatch)
    from server import open_work_provider_attempt_verification as verifier
    calls = []
    monkeypatch.setattr(verifier, "verify_provider_attempt",
                        lambda **kwargs: calls.append("provider"))
    async def empty_admitted(req, resp):
        return {"case_ref": "case-1", "receipts": {"run_ref": ""}}
    async def empty_journal(req, resp):
        return {"run_ref": "", "entries": []}
    monkeypatch.setattr(routes, "read_local_combined_case_review", empty_admitted)
    monkeypatch.setattr(routes, "read_local_bound_attempt_receipts", empty_journal)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.read_local_bound_provider_event(request(), Response()))
    assert exc.value.status_code == 409
    assert calls == []

@pytest.mark.parametrize("verdict", [
    {"status": "verified_provider_event", "provider_operation": "succeeded", "customer_outcome": "unknown"},
    {"status": "verified_provider_operation", "provider_operation": "succeeded", "customer_outcome": "unknown"},
    {"status": "verified_provider_operation", "provider_operation": "failed", "customer_outcome": "unknown"},
    {"status": "verified_provider_operation", "provider_operation": "unknown", "customer_outcome": "unknown"},
    {"status": "unknown", "provider_operation": "unknown", "customer_outcome": "succeeded"},
    {"status": "verified_provider_event", "customer_outcome": "unknown"},
    {"status": "verified_provider_event", "provider_operation": "unknown", "customer_outcome": "unknown", "event_id": "evt_wrong123"},
    {"status": "unknown", "provider_operation": "unknown", "customer_outcome": "unknown", "event_id": "evt_12345678"},
    None,
])
def test_contradictory_adapter_verdict_never_disclosed(monkeypatch, verdict):
    """No adapter response may manufacture a customer outcome or operation result."""
    install(monkeypatch)
    from server import local_source_credentials
    from server import open_work_provider_attempt_verification as verifier
    monkeypatch.setattr(local_source_credentials, "_read_metadata",
                        lambda path: {"account": "acct_12345678"})
    monkeypatch.setattr(routes, "_source_scope", lambda: "tenant-1")
    monkeypatch.setattr(verifier, "verify_provider_attempt", lambda **kwargs: verdict)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.read_local_bound_provider_event(request(), Response()))
    assert exc.value.status_code == 409


def test_empty_retained_attempt_journal_blocks_provider_io(monkeypatch):
    """A retained run without any attempts cannot authorize an exact provider read."""
    install(monkeypatch)
    from server import open_work_provider_attempt_verification as verifier
    calls = []
    monkeypatch.setattr(verifier, "verify_provider_attempt",
                        lambda **kwargs: calls.append("provider"))
    async def empty_journal(req, resp):
        return {"run_ref": "run-1", "entries": []}
    monkeypatch.setattr(routes, "read_local_bound_attempt_receipts", empty_journal)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.read_local_bound_provider_event(request(), Response()))
    assert exc.value.status_code == 409
    assert calls == []


@pytest.mark.parametrize("entries", [None, {}, "untrusted", 3])
def test_malformed_retained_attempt_journal_blocks_provider_io(monkeypatch, entries):
    """Malformed journal projections cannot initiate provider network access."""
    install(monkeypatch)
    from server import open_work_provider_attempt_verification as verifier
    calls = []
    monkeypatch.setattr(verifier, "verify_provider_attempt",
                        lambda **kwargs: calls.append("provider"))
    async def malformed(req, resp):
        return {"run_ref": "run-1", "entries": entries}
    monkeypatch.setattr(routes, "read_local_bound_attempt_receipts", malformed)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.read_local_bound_provider_event(request(), Response()))
    assert exc.value.status_code == 409
    assert calls == []


@pytest.mark.parametrize("entries", [[{}], [None], ["not-a-row"], [1], [{"attempt_ref": "a"}] * 201])
def test_unusable_or_oversized_journal_never_contacts_provider(monkeypatch, entries):
    """An empty placeholder or unbounded journal is not a retained attempt."""
    install(monkeypatch)
    from server import open_work_provider_attempt_verification as verifier
    calls = []
    monkeypatch.setattr(verifier, "verify_provider_attempt",
                        lambda **kwargs: calls.append("provider"))
    async def invalid(req, resp):
        return {"run_ref": "run-1", "entries": entries}
    monkeypatch.setattr(routes, "read_local_bound_attempt_receipts", invalid)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.read_local_bound_provider_event(request(), Response()))
    assert exc.value.status_code == 409
    assert calls == []


def test_retained_journal_revoked_during_preflight_blocks_provider_io(monkeypatch):
    """A journal revoked during source admission cannot authorize Stripe I/O."""
    install(monkeypatch)
    from server import open_work_provider_attempt_verification as verifier
    calls = []
    monkeypatch.setattr(verifier, "verify_provider_attempt",
                        lambda **kwargs: calls.append("provider"))
    journal_reads = []
    async def changing(req, resp):
        journal_reads.append(1)
        if len(journal_reads) > 1:
            raise HTTPException(403, "attempt access revoked")
        return {"run_ref": "run-1", "entries": [{"attempt_ref": "attempt-1"}]}
    monkeypatch.setattr(routes, "read_local_bound_attempt_receipts", changing)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.read_local_bound_provider_event(request(), Response()))
    assert exc.value.status_code == 403
    assert len(journal_reads) == 2
    assert calls == []


def test_oversized_retained_attempt_denied_before_provider_io(monkeypatch):
    """Untrusted retained rows cannot trigger a provider read or unbounded disclosure."""
    install(monkeypatch)
    async def oversized(req, resp):
        return {"run_ref": "run-1", "entries": [{"attempt_ref": "a", "detail": "x" * 65_000}]}
    monkeypatch.setattr(routes, "read_local_bound_attempt_receipts", oversized)
    from server import open_work_provider_attempt_verification as verifier
    calls = []
    monkeypatch.setattr(verifier, "verify_provider_attempt",
                        lambda **kwargs: calls.append(kwargs))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.read_local_bound_provider_event(request(), Response()))
    assert exc.value.status_code == 409
    assert calls == []


def test_unserializable_retained_attempt_denied_before_provider_io(monkeypatch):
    """Malformed journal projections fail closed rather than raising a server error."""
    install(monkeypatch)
    async def malformed(req, resp):
        return {"run_ref": "run-1", "entries": [{"attempt_ref": object()}]}
    monkeypatch.setattr(routes, "read_local_bound_attempt_receipts", malformed)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.read_local_bound_provider_event(request(), Response()))
    assert exc.value.status_code == 409


@pytest.mark.parametrize("operation", ["succeeded", "failed"])
def test_independent_provider_operation_verdict_is_not_customer_success(monkeypatch, operation):
    """Provider-reported operation status is usable without asserting agent causality."""
    install(monkeypatch)
    from server import local_source_credentials
    from server import open_work_provider_attempt_verification as verifier
    monkeypatch.setattr(local_source_credentials, "_read_metadata",
                        lambda path: {"account": "acct_12345678"})
    monkeypatch.setattr(routes, "_source_scope", lambda: "tenant-1")
    monkeypatch.setattr(verifier, "verify_provider_attempt",
                        lambda **kwargs: {"status": "verified_provider_operation",
                                          "provider_operation": operation,
                                          "customer_outcome": "unknown",
                                          "event_id": DATA["provider_event_id"]})
    result = asyncio.run(routes.read_local_bound_provider_event(request(), Response()))
    assert result["provider_event_verdict"]["provider_operation"] == operation
    assert result["provider_event_independently_rechecked_on_this_read"] is True
    assert result["customer_outcome_verified"] is False
    assert result["provider_event_attributed_to_agent_run"] is False
    assert result["receipt_persisted"] is False


@pytest.mark.parametrize("bad_operation", ["unknown", "customer_succeeded", None])
def test_provider_operation_verdict_must_have_valid_provider_status(monkeypatch, bad_operation):
    """A contradictory adapter result must not become verified provider evidence."""
    install(monkeypatch)
    from server import local_source_credentials
    from server import open_work_provider_attempt_verification as verifier
    monkeypatch.setattr(local_source_credentials, "_read_metadata",
                        lambda path: {"account": "acct_12345678"})
    monkeypatch.setattr(routes, "_source_scope", lambda: "tenant-1")
    monkeypatch.setattr(verifier, "verify_provider_attempt",
                        lambda **kwargs: {"status": "verified_provider_operation",
                                          "provider_operation": bad_operation,
                                          "customer_outcome": "unknown",
                                          "event_id": DATA["provider_event_id"]})
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.read_local_bound_provider_event(request(), Response()))
    assert exc.value.status_code == 409


def test_exact_retained_journal_correlation_is_explicitly_untrusted(monkeypatch):
    """An exact operator-retained match is useful context, never causal proof."""
    install(monkeypatch)
    from server import local_source_credentials
    from server import open_work_provider_attempt_verification as verifier
    monkeypatch.setattr(local_source_credentials, "_read_metadata",
                        lambda path: {"account": "acct_12345678"})
    monkeypatch.setattr(routes, "_source_scope", lambda: "tenant-1")
    monkeypatch.setattr(verifier, "verify_provider_attempt",
                        lambda **kwargs: {"status": "verified_provider_event",
                                          "provider_operation": "unknown",
                                          "customer_outcome": "unknown",
                                          "event_id": DATA["provider_event_id"]})
    async def receipts(req, resp):
        return {"run_ref": "run-1", "entries": [{
            "attempt_ref": "attempt-1",
            "provider_object_id": DATA["provider_object_id"],
            "provider_operation": DATA["provider_operation"],
            "provider_correlation_id": DATA["provider_correlation_id"],
        }]}
    monkeypatch.setattr(routes, "read_local_bound_attempt_receipts", receipts)
    result = asyncio.run(routes.read_local_bound_provider_event(request(), Response()))
    assert result["retained_journal_correlation"] == {
        "status": "untrusted_exact_match", "attempt_ref": "attempt-1",
        "host_attested": False, "provider_event_attributed_to_agent_run": False,
    }
    assert result["receipt_persisted"] is False
    assert result["provider_event_to_attempt_causality_verified"] is False


def test_duplicate_retained_correlations_do_not_choose_attempt(monkeypatch):
    """Two exact matches must not be silently attributed to either attempt."""
    install(monkeypatch)
    from server import local_source_credentials
    from server import open_work_provider_attempt_verification as verifier
    monkeypatch.setattr(local_source_credentials, "_read_metadata",
                        lambda path: {"account": "acct_12345678"})
    monkeypatch.setattr(routes, "_source_scope", lambda: "tenant-1")
    monkeypatch.setattr(verifier, "verify_provider_attempt",
                        lambda **kwargs: {"status": "unknown",
                                          "provider_operation": "unknown",
                                          "customer_outcome": "unknown"})
    async def receipts(req, resp):
        return {"run_ref": "run-1", "entries": [{
            "attempt_ref": ref,
            "provider_object_id": DATA["provider_object_id"],
            "provider_operation": DATA["provider_operation"],
            "provider_correlation_id": DATA["provider_correlation_id"],
        } for ref in ("attempt-1", "attempt-2")]}
    monkeypatch.setattr(routes, "read_local_bound_attempt_receipts", receipts)
    result = asyncio.run(routes.read_local_bound_provider_event(request(), Response()))
    assert result["retained_journal_correlation"]["status"] == "ambiguous"
    assert result["retained_journal_correlation"]["attempt_ref"] is None
    assert result["provider_event_attributed_to_agent_run"] is False
