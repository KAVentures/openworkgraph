"""Focused behavior checks for the authenticated source-key reconciliation boundary."""
import csv
import io
from unittest.mock import patch

import pytest

from server import m365_outlook_event_membership as membership
from server.m365_outlook_owg_receipt import ReceiptError


def _csv(rows):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(membership.HEADER)
    writer.writerows(rows)
    return out.getvalue().encode()


class Response:
    status = 200

    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, n):
        return self.body[:n]


class Transport:
    def __init__(self, body):
        self.body = body
        self.requests = []

    def open(self, req, timeout=30):
        self.requests.append((req.full_url, req.get_header("Authorization")))
        return Response(self.body)


def _run(body, **changes):
    transport = Transport(body)
    kwargs = dict(tenant="t", mailbox="m", folder="f", grant_file="grant",
                  candidate="candidate", csv_path="candidate.csv",
                  log_id="log1", confirm_log_id="log1", port=8765,
                  session="session", consent_local_read=True, opener=transport)
    kwargs.update(changes)
    candidate_csv = b"case_id,activity,timestamp,source_event_id\r\ncase1,read,2026-01-01T00:00:00Z,event1\r\n"
    with patch.object(membership, "_grant"), patch.object(membership, "_scope", return_value="a"*64), \
         patch.object(membership, "_session", return_value="session"), \
         patch.object(membership, "_artifact", return_value=(candidate_csv, {"events": 1})), \
         patch.object(membership, "_inventory", return_value=[]), \
         patch.object(membership, "_target", return_value=1):
        result = membership.reconcile(**kwargs)
    return result, transport


def test_verified_membership_is_read_only():
    ns = "m365_" + "a"*32
    result, transport = _run(_csv([[ns, "event1", ns, "case1", "read", "2026-01-01", "", "{}"]]))
    assert result["status"] == "all_candidate_events_present"
    assert result["automatic_retry_allowed"] is False
    assert result["checkpoint_advanced"] is False
    assert len(transport.requests) == 1
    assert "format=sourcecsv" in transport.requests[0][0]
    assert "case_type=source_case" in transport.requests[0][0]


def test_missing_membership_requires_manual_reconciliation():
    ns = "m365_" + "a"*32
    result, _ = _run(_csv([[ns, "unrelated", ns, "othercase", "read", "2026-01-01", "", "{}"]]))
    assert result["status"] == "manual_reconciliation_required"
    assert result["missing_events"] == 1


def test_cross_namespace_refused():
    with pytest.raises(ReceiptError):
        _run(_csv([["other", "event1", "other", "case1", "read", "2026-01-01", "", "{}"]]))


def test_case_mismatch_refused():
    ns = "m365_" + "a"*32
    with pytest.raises(ReceiptError):
        _run(_csv([[ns, "event1", ns, "wrong", "read", "2026-01-01", "", "{}"]]))


def test_requires_separate_consent_and_exact_destination():
    with pytest.raises(ReceiptError):
        _run(_csv([]), consent_local_read=False)
    with pytest.raises(ReceiptError):
        _run(_csv([]), confirm_log_id="different")


def test_oversized_export_fails_closed(monkeypatch):
    monkeypatch.setattr(membership, "MAX_EXPORT_BYTES", 10)
    with pytest.raises(ReceiptError):
        _run(b"x"*11)
