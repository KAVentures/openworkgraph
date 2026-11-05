"""Real private-file candidate review tests; no provider calls."""
import csv
import hashlib
import io
import json
import os

import pytest

from server.m365_outlook_candidate_review import review, main
from server.m365_outlook_delta import FORMAT, _scope, _initial


def _private(path):
    path.chmod(0o600)
    return path


def _fixtures(tmp_path):
    tmp_path.chmod(0o700)
    tenant, mailbox, folder = "tenant", "mailbox", "folder"
    grant = tmp_path / "grant.json"
    grant.write_text(json.dumps(dict(version=1, enabled=True, consented=True,
        tenant_scope=tenant, mailbox_scope=mailbox, folder_id=folder)))
    _private(grant)
    csv_path = tmp_path / "events.csv"
    row = ["outlookcase:" + "a"*64, "Observed read status: read",
           "2026-11-05T10:00:00Z", "outlookevent:" + "b"*64]
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["case_id", "activity", "timestamp", "source_event_id"])
    writer.writerow(row)
    csv_path.write_bytes(out.getvalue().encode())
    _private(csv_path)
    candidate = tmp_path / "candidate.json"
    record = dict(format=FORMAT, scope_sha256=_scope(tenant, mailbox, folder),
                  csv_name=csv_path.name,
                  csv_sha256=hashlib.sha256(csv_path.read_bytes()).hexdigest(),
                  cursor_url=_initial(folder).split("?")[0] + "?%24deltatoken=next",
                  status="candidate_requires_confirmed_import",
                  events=1, removals=1,
                  removed_case_ids=["outlookcase:" + "c"*64], pages=1)
    candidate.write_text(json.dumps(record))
    _private(candidate)
    return dict(tenant=tenant, mailbox=mailbox, folder=folder,
                grant_file=grant, candidate=candidate), csv_path


def test_real_private_candidate_review(tmp_path):
    args, _ = _fixtures(tmp_path)
    result = review(**args, consent_local_read=True)
    assert result["status"] == "review_ready"
    assert result["event_snapshots"] == 1
    assert result["removal_markers_for_review"] == 1
    assert result["provider_requests"] == result["owg_writes"] == 0
    assert result["checkpoint_advanced"] is False


def test_consent_and_revocation_fail_closed(tmp_path):
    args, _ = _fixtures(tmp_path)
    with pytest.raises(ValueError):
        review(**args)
    grant = args["grant_file"]
    value = json.loads(grant.read_text())
    value["enabled"] = False
    grant.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        review(**args, consent_local_read=True)


def test_tamper_and_mismatched_scope_refuse(tmp_path):
    args, csv_path = _fixtures(tmp_path)
    csv_path.write_bytes(csv_path.read_bytes() + b"tamper")
    with pytest.raises(ValueError):
        review(**args, consent_local_read=True)
    args, _ = _fixtures(tmp_path)
    with pytest.raises(ValueError):
        review(**{**args, "folder": "other"}, consent_local_read=True)


def test_cli_minimized_refusal(tmp_path, capsys):
    args, _ = _fixtures(tmp_path)
    argv = ["--tenant", args["tenant"], "--mailbox", args["mailbox"],
            "--folder", args["folder"], "--grant-file", str(args["grant_file"]),
            "--candidate", str(args["candidate"])]
    assert main(argv) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "refused"
    assert main(argv + ["--consent-local-read"]) == 0
    response = json.loads(capsys.readouterr().out)
    assert response["status"] == "review_ready"
    assert "cursor_url" not in response
