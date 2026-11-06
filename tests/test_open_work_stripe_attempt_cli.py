"""Executable operator CLI tests; no Stripe network or credentials required."""
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from unittest.mock import patch

from server.open_work_stripe_attempt_cli import main, verify_explicit_attempt


class _Response:
    status = 200

    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size):
        return json.dumps(self.data).encode()


def _transport(req, timeout):
    if req.full_url.endswith("/account"):
        return _Response({"id": "acct_123"})
    return _Response({
        "object": "list", "has_more": False,
        "data": [{"id": "evt_123", "type": "invoice.payment_succeeded",
                  "data": {"object": {"id": "in_123"}},
                  "request": {"idempotency_key": "attempt_123"}}],
    })


def _verify(**overrides):
    args = dict(account="acct_123", object_id="in_123",
                operation="invoice.payment_succeeded",
                correlation_id="attempt_123", event_id="evt_123",
                token="rk_test_dummy", transport=_transport)
    args.update(overrides)
    return verify_explicit_attempt(**args)


def test_exact_authenticated_event_does_not_prove_customer_completion():
    result = _verify()
    assert result["status"] == "verified_provider_event"
    assert result["provider_operation"] == "unknown"
    assert result["customer_outcome"] == "unknown"


def test_wrong_event_and_account_fail_closed():
    assert _verify(event_id="evt_other")["status"] == "unknown"
    assert _verify(account="acct_other")["status"] == "unknown"


def test_invalid_identity_prevents_network():
    def forbidden(*args, **kwargs):
        raise AssertionError("network attempted")
    assert _verify(object_id="../bad", transport=forbidden)["reason"] == "invalid_identity"


def test_provider_outage_is_unknown_without_exception_or_leak():
    def outage(*args, **kwargs):
        raise RuntimeError("private token and account")
    result = _verify(transport=outage)
    assert result["reason"] == "provider_unavailable"
    assert "private" not in json.dumps(result)


def test_cli_requires_explicit_read_confirmation():
    output = io.StringIO()
    with patch.dict("os.environ", {"OWG_STRIPE_ATTEMPT_READ_KEY": "rk_test_dummy"}):
        with redirect_stdout(output):
            exit_code = main(["--account", "acct_123", "--object-id", "in_123",
                              "--event-type", "invoice.payment_succeeded",
                              "--idempotency-key", "attempt_123",
                              "--event-id", "evt_123"])
    assert exit_code == 3
    assert json.loads(output.getvalue())["reason"] == "explicit_provider_read_not_confirmed"


def test_cli_missing_key_never_calls_provider():
    output = io.StringIO()
    with patch.dict("os.environ", {}, clear=True), redirect_stdout(output):
        exit_code = main(["--account", "acct_123", "--object-id", "in_123",
                          "--event-type", "invoice.payment_succeeded",
                          "--idempotency-key", "attempt_123", "--event-id", "evt_123",
                          "--confirm-provider-read"])
    assert exit_code == 3
    assert json.loads(output.getvalue())["reason"] == "missing_provider_credential"
