"""Persisted checkpoint cursor and schema fail-closed regression tests."""
import json

import pytest

from server.m365_outlook_checkpoint import FORMAT, _entries
from server.m365_outlook_owg_receipt import ReceiptError
from server.m365_outlook_delta import DeltaError, _initial, _scope


TENANT = "tenant"
MAILBOX = "mailbox"
FOLDER = "inbox"


def _write(tmp_path, **changes):
    scope = _scope(TENANT, MAILBOX, FOLDER)
    entry = {
        "format": FORMAT,
        "scope_sha256": scope,
        "generation": 1,
        "candidate_sha256": "a" * 64,
        "cursor_url": (
            "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages/delta"
            "?%24deltatoken=opaque"
        ),
        "previous_entry_sha256": None,
        "log_id": "log-1",
        "event_membership_verified": True,
        "removals_reviewed": True,
        "checkpoint_advanced": True,
        "provider_requests": 0,
    }
    entry.update(changes)
    (tmp_path / f"checkpoint-{scope}-00000001.json").write_text(
        json.dumps(entry) + "\n", encoding="utf-8"
    )
    return scope


def test_valid_scoped_cursor_can_be_inspected(tmp_path):
    scope = _write(tmp_path)
    assert _entries(tmp_path, scope, FOLDER)[0]["generation"] == 1


@pytest.mark.parametrize("cursor", [
    "https://evil.example/v1.0/me/mailFolders/inbox/messages/delta?%24deltatoken=x",
    "http://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages/delta?%24deltatoken=x",
    "https://graph.microsoft.com/v1.0/me/mailFolders/other/messages/delta?%24deltatoken=x",
    "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages/delta?%24skiptoken=x",
])
def test_forged_or_wrong_scope_cursor_refused(tmp_path, cursor):
    scope = _write(tmp_path, cursor_url=cursor)
    with pytest.raises((ReceiptError, DeltaError)):
        _entries(tmp_path, scope, FOLDER)


def test_boolean_provider_request_count_is_not_zero(tmp_path):
    scope = _write(tmp_path, provider_requests=False)
    with pytest.raises(ReceiptError):
        _entries(tmp_path, scope, FOLDER)
