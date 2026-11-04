"""Focused operator comparison acceptance/refusal tests."""
import csv
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from server.m365_outlook_event_membership_compare import compare, FORMAT
from server.m365_outlook_attempt_event_review import FORMAT as REVIEW_FORMAT
from server.m365_outlook_delta import _scope
from server.m365_outlook_owg_receipt import ReceiptError

LOG = "log_0123456789abcdef"


def setup(tmp_path):
    os.chmod(tmp_path, 0o700)
    paths = [tmp_path / n for n in ("grant.json", "review.json", "export.csv", "comparison-result.json")]
    grant, review, export, output = paths
    review.write_text(json.dumps({
        "format": REVIEW_FORMAT,
        "status": "requires_independent_event_membership_review",
        "scope_sha256": _scope("tenant", "mailbox", "folder"),
        "log_id": LOG,
        "event_ids": ["outlookevent:one", "outlookevent:two"],
        "event_membership_verified": False,
        "safe_to_retry": False, "checkpoint_advanced": False,
    }))
    export.write_text("case_id,source_event_id\ncase1,outlookevent:one\n")
    for p in (review, export):
        os.chmod(p, 0o600)
    kwargs = dict(tenant="tenant", mailbox="mailbox", folder="folder",
                  grant_file=grant, review_file=review, export_file=export,
                  output=output, log_id=LOG, confirm_log_id=LOG,
                  consent_local_read=True, consent_private_export=True)
    return kwargs


def run(kwargs):
    with patch("server.m365_outlook_event_membership_compare._grant"):
        return compare(**kwargs)


def test_private_comparison_has_no_retry_authority(tmp_path):
    kwargs = setup(tmp_path)
    result = run(kwargs)
    assert result["matching_source_events"] == 1
    assert result["event_membership_verified"] is False
    assert result["safe_to_retry"] is False
    report = json.loads(kwargs["output"].read_text())
    assert report["unmatched_source_events"] == ["outlookevent:two"]
    assert report["export_scope_authenticated"] is False
    assert report["owg_write_requests"] == 0
    assert kwargs["output"].stat().st_mode & 0o777 == 0o600


def test_refuses_missing_consent(tmp_path):
    kwargs = setup(tmp_path)
    kwargs["consent_local_read"] = False
    with pytest.raises(ReceiptError):
        run(kwargs)


def test_refuses_wrong_scope(tmp_path):
    kwargs = setup(tmp_path)
    data = json.loads(kwargs["review_file"].read_text())
    data["scope_sha256"] = "wrong"
    kwargs["review_file"].write_text(json.dumps(data))
    os.chmod(kwargs["review_file"], 0o600)
    with pytest.raises(ReceiptError):
        run(kwargs)


def test_duplicate_export_not_accepted_as_proof(tmp_path):
    kwargs = setup(tmp_path)
    kwargs["export_file"].write_text(
        "case_id,source_event_id\ncase1,outlookevent:one\ncase2,outlookevent:one\n")
    os.chmod(kwargs["export_file"], 0o600)
    result = run(kwargs)
    assert result["duplicate_export_ids"] is True
    assert result["event_membership_verified"] is False


def test_refuses_reused_destination(tmp_path):
    kwargs = setup(tmp_path)
    kwargs["output"].write_text("existing")
    with pytest.raises(ReceiptError):
        run(kwargs)
