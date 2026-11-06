import io
import json
import pytest
from server.open_work_stripe_attempt_events import read_stripe_attempt_events

class Reply:
    status = 200
    def __init__(self, obj):
        self.stream = io.BytesIO(json.dumps(obj).encode())
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, n): return self.stream.read(n)

def transport(*, more=False, account="acct_1", key="attempt_1"):
    def fetch(req, timeout):
        assert timeout == 8
        assert req.get_method() == "GET"
        assert req.get_header("Authorization") == "Bearer rk_test_secret"
        if req.full_url.endswith("/account"):
            return Reply({"id": account})
        assert "/events?" in req.full_url
        return Reply({"object": "list", "has_more": more, "data": [{
            "id": "evt_1", "type": "invoice.updated",
            "request": {"idempotency_key": key},
            "data": {"object": {"id": "in_1"}}}]})
    return fetch

def read(fetch):
    return read_stripe_attempt_events(token="rk_test_secret", expected_account="acct_1",
        object_id="in_1", event_type="invoice.updated",
        correlation_id="attempt_1", transport=fetch)

def test_exact_authenticated_event_from_read_only_source():
    p = read(transport())
    assert p["authenticated"] is True and p["complete"] is True
    assert p["events"][0]["event_id"] == "evt_1"

def test_unrelated_event_never_matches():
    assert read(transport(key="other"))["events"] == []

@pytest.mark.parametrize("kwargs", [{"more": True}, {"account": "acct_other"}])
def test_incomplete_or_wrong_account_fails_closed(kwargs):
    with pytest.raises(ValueError):
        read(transport(**kwargs))

def test_invalid_credential_never_calls_provider():
    with pytest.raises(ValueError):
        read_stripe_attempt_events(token="invalid", expected_account="acct_1",
            object_id="in_1", event_type="invoice.updated",
            correlation_id="attempt_1", transport=lambda *a, **k: 1)
