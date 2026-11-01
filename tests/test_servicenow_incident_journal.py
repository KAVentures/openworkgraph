"""Synthetic end-to-end acceptance for operator-initiated ServiceNow journal CLI.

All source HTTP is fake; no customer instance, credentials or source writes.
"""
from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path
import sqlite3

import pytest

from server.servicenow_incident_journal import main

A = "a" * 32
B = "b" * 32
HOST = "sample.service-now.com"
TOKEN = "FAKE-NO-REAL-CREDENTIAL"


def record(*, incident=A, revision="3", state="2", updated="2026-10-30 03:10:00"):
    return {"sys_id": incident, "sys_updated_on": updated,
            "sys_mod_count": revision, "state": state,
            "short_description": "PRIVATE-SOURCE-TEXT",
            "caller_id": "PRIVATE-PERSON"}


class FakeResponse:
    status = 200
    headers = {"Content-Type": "application/json"}

    def __init__(self, url, rows):
        self.url = url
        self.rows = rows

    def geturl(self):
        return self.url

    def read(self, size):
        return json.dumps({"result": self.rows}).encode("utf-8")[:size]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeTransport:
    def __init__(self, rows=None, on_open=None):
        self.rows = [record()] if rows is None else rows
        self.calls = []
        self.on_open = on_open

    def open(self, req, timeout):
        assert req.get_method() == "GET"
        assert req.full_url.startswith("https://" + HOST + "/api/now/table/incident?")
        assert req.get_header("Authorization") == "Bearer " + TOKEN
        assert req.get_header("Accept") == "application/json"
        assert timeout == 15
        self.calls.append(req.full_url)
        if self.on_open:
            self.on_open()
        return FakeResponse(req.full_url, self.rows)


def grant(tmp_path, *, tenant="team-a", ids=(A,)):
    path = tmp_path / "grant.json"
    path.write_text(json.dumps({
        "version": 1, "enabled": True, "consented": True,
        "host": HOST, "tenant_scope": tenant, "sys_ids": list(ids),
    }), encoding="utf-8")
    if os.name == "posix":
        path.chmod(0o600)
    return path


def args(tmp_path, grant_path, *, output="first.csv", journal="journal.sqlite",
         tenant="team-a", ids=(A,), since=None, export_only=False):
    cmd = ["--instance", HOST, "--tenant", tenant,
           "--grant-file", str(grant_path), "--timezone", "UTC",
           "--journal", str(tmp_path / journal), "--output", str(tmp_path / output)]
    for incident in ids:
        cmd += ["--incident-id", incident]
    if export_only:
        cmd += ["--export-only", "--consent-local-export"]
    else:
        cmd += ["--consent-selected-read"]
    if since is not None:
        cmd += ["--since", since]
    return cmd


def rows(path):
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def test_live_two_run_dedupe_and_new_revision_then_offline_export(
        tmp_path, monkeypatch, capsys):
    grant_path = grant(tmp_path)
    monkeypatch.setenv("OWG_SERVICENOW_TOKEN", TOKEN)
    one = FakeTransport()
    assert main(args(tmp_path, grant_path), opener=one) == 0
    assert len(one.calls) == 1
    first = rows(tmp_path / "first.csv")
    assert len(first) == 1
    assert first[0]["activity"] == "Incident state: 2"
    assert "PRIVATE" not in (tmp_path / "first.csv").read_text()
    report = json.loads(capsys.readouterr().out)
    assert report["new_snapshots"] == 1
    assert report["journal_snapshots"] == 1
    assert report["checkpoint_advanced"] is False
    assert report["automatic_owg_log_append"] is False

    replay = FakeTransport()
    assert main(args(tmp_path, grant_path, output="replay.csv"), opener=replay) == 0
    assert len(replay.calls) == 1
    assert rows(tmp_path / "replay.csv") == first
    report = json.loads(capsys.readouterr().out)
    assert report["new_snapshots"] == 0
    assert report["replayed_snapshots"] == 1

    updated = FakeTransport([record(revision="4", state="3",
                                    updated="2026-10-30 04:10:00")])
    assert main(args(tmp_path, grant_path, output="updated.csv",
                     since="2026-10-30 03:10:00"), opener=updated) == 0
    assert len(updated.calls) == 1
    updated_rows = rows(tmp_path / "updated.csv")
    assert len(updated_rows) == 2
    assert updated_rows[0]["source_event_id"] != updated_rows[1]["source_event_id"]
    assert {r["activity"] for r in updated_rows} == {
        "Incident state: 2", "Incident state: 3",
    }
    assert json.loads(capsys.readouterr().out)["journal_snapshots"] == 2

    monkeypatch.delenv("OWG_SERVICENOW_TOKEN")
    offline = FakeTransport()
    assert main(args(tmp_path, grant_path, output="recovered.csv",
                     export_only=True), opener=offline) == 0
    assert not offline.calls
    assert rows(tmp_path / "recovered.csv") == updated_rows
    assert json.loads(capsys.readouterr().out)["mode"] == "export_only"
    if os.name == "posix":
        assert (tmp_path / "journal.sqlite").stat().st_mode & 0o077 == 0
        assert (tmp_path / "recovered.csv").stat().st_mode & 0o077 == 0


def test_conflicting_replay_refuses_without_modifying_journal(tmp_path, monkeypatch, capsys):
    grant_path = grant(tmp_path)
    monkeypatch.setenv("OWG_SERVICENOW_TOKEN", TOKEN)
    assert main(args(tmp_path, grant_path), opener=FakeTransport()) == 0
    capsys.readouterr()
    conflict = FakeTransport([record(revision="3", state="8")])
    assert main(args(tmp_path, grant_path, output="conflict.csv"), opener=conflict) == 2
    assert not (tmp_path / "conflict.csv").exists()
    assert TOKEN not in capsys.readouterr().out
    assert main(args(tmp_path, grant_path, output="after.csv",
                     export_only=True), opener=FakeTransport()) == 0
    assert len(rows(tmp_path / "after.csv")) == 1


def test_selection_and_tenant_bound_journal_and_grant(tmp_path, monkeypatch):
    grant_path = grant(tmp_path)
    monkeypatch.setenv("OWG_SERVICENOW_TOKEN", TOKEN)
    assert main(args(tmp_path, grant_path), opener=FakeTransport()) == 0
    for tenant, ids in (("team-b", (A,)), ("team-a", (B,))):
        other_grant = grant(tmp_path, tenant=tenant, ids=ids)
        fake = FakeTransport([record(incident=ids[0])])
        assert main(args(tmp_path, other_grant, output="wrong.csv",
                         tenant=tenant, ids=ids), opener=fake) == 2
        assert not fake.calls  # existing journal mismatch refused before source read
        assert not (tmp_path / "wrong.csv").exists()
    assert len(rows(tmp_path / "first.csv")) == 1


def test_no_consent_or_revoked_grant_no_network_or_journal(tmp_path, monkeypatch):
    grant_path = grant(tmp_path)
    monkeypatch.setenv("OWG_SERVICENOW_TOKEN", TOKEN)
    no_consent = args(tmp_path, grant_path)
    no_consent.remove("--consent-selected-read")
    fake = FakeTransport()
    assert main(no_consent, opener=fake) == 2
    assert not fake.calls
    assert not (tmp_path / "journal.sqlite").exists()

    grant_path.unlink()
    assert main(args(tmp_path, grant_path), opener=fake) == 2
    assert not fake.calls
    assert not (tmp_path / "journal.sqlite").exists()


def test_revoke_during_provider_response_prevents_journal(tmp_path, monkeypatch):
    grant_path = grant(tmp_path)
    monkeypatch.setenv("OWG_SERVICENOW_TOKEN", TOKEN)
    fake = FakeTransport(on_open=grant_path.unlink)
    assert main(args(tmp_path, grant_path), opener=fake) == 2
    assert len(fake.calls) == 1
    assert not (tmp_path / "journal.sqlite").exists()
    assert not (tmp_path / "first.csv").exists()


def test_commit_before_output_error_can_be_recovered_offline(tmp_path, monkeypatch):
    grant_path = grant(tmp_path)
    monkeypatch.setenv("OWG_SERVICENOW_TOKEN", TOKEN)
    (tmp_path / "first.csv").write_text("DO NOT OVERWRITE")
    assert main(args(tmp_path, grant_path), opener=FakeTransport()) == 2
    assert (tmp_path / "first.csv").read_text() == "DO NOT OVERWRITE"
    assert (tmp_path / "journal.sqlite").exists()
    monkeypatch.delenv("OWG_SERVICENOW_TOKEN")
    assert main(args(tmp_path, grant_path, output="recover.csv",
                     export_only=True), opener=FakeTransport()) == 0
    assert len(rows(tmp_path / "recover.csv")) == 1


def test_offline_export_requires_existing_journal_and_explicit_consent(tmp_path):
    grant_path = grant(tmp_path)
    fake = FakeTransport()
    assert main(args(tmp_path, grant_path, export_only=True), opener=fake) == 2
    assert not fake.calls
    assert not (tmp_path / "journal.sqlite").exists()
    command = args(tmp_path, grant_path, export_only=True)
    command.remove("--consent-local-export")
    assert main(command, opener=fake) == 2
    assert not fake.calls


def test_private_file_and_symlink_refusals(tmp_path, monkeypatch):
    grant_path = grant(tmp_path)
    monkeypatch.setenv("OWG_SERVICENOW_TOKEN", TOKEN)
    external = tmp_path / "external.sqlite"
    external.write_bytes(b"DO NOT TOUCH")
    link = tmp_path / "journal.sqlite"
    try:
        link.symlink_to(external)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks not available in this environment")
    fake = FakeTransport()
    assert main(args(tmp_path, grant_path), opener=fake) == 2
    assert external.read_bytes() == b"DO NOT TOUCH"
    assert not (tmp_path / "first.csv").exists()
    link.unlink()
    if os.name == "posix":
        external.chmod(0o644)
        assert main(args(tmp_path, grant_path, journal="external.sqlite"),
                    opener=FakeTransport()) == 2
        assert external.read_bytes() == b"DO NOT TOUCH"


def test_output_rejected_after_grant_revocation_on_offline_export(
        tmp_path, monkeypatch):
    grant_path = grant(tmp_path)
    monkeypatch.setenv("OWG_SERVICENOW_TOKEN", TOKEN)
    assert main(args(tmp_path, grant_path), opener=FakeTransport()) == 0
    grant_path.unlink()
    assert main(args(tmp_path, grant_path, output="offline.csv",
                     export_only=True), opener=FakeTransport()) == 2
    assert not (tmp_path / "offline.csv").exists()


def test_private_directory_with_spaces_is_supported_on_all_platforms(
        tmp_path, monkeypatch):
    private = tmp_path / "private selected incidents"
    private.mkdir()
    if os.name == "posix":
        private.chmod(0o700)
    grant_path = grant(private)
    monkeypatch.setenv("OWG_SERVICENOW_TOKEN", TOKEN)
    assert main(args(private, grant_path), opener=FakeTransport()) == 0
    assert len(rows(private / "first.csv")) == 1
    monkeypatch.delenv("OWG_SERVICENOW_TOKEN")
    assert main(args(private, grant_path, output="offline recovered.csv",
                     export_only=True), opener=FakeTransport()) == 0
    assert rows(private / "offline recovered.csv") == rows(private / "first.csv")


def test_empty_filtered_page_never_deletes_existing_revision(tmp_path, monkeypatch):
    grant_path = grant(tmp_path)
    monkeypatch.setenv("OWG_SERVICENOW_TOKEN", TOKEN)
    assert main(args(tmp_path, grant_path), opener=FakeTransport()) == 0
    assert main(args(tmp_path, grant_path, output="empty.csv",
                     since="2026-10-31 00:00:00"),
                opener=FakeTransport([])) == 0
    assert len(rows(tmp_path / "empty.csv")) == 1
