"""Synthetic actual-CLI acceptance for finite ServiceNow polling.

All HTTPS responses are fake; no real ServiceNow tenant or credential is used.
"""
from __future__ import annotations

import csv
import json
import os

import pytest

from server.servicenow_incident_poll import main

HOST = "sample.service-now.com"
INCIDENT = "a" * 32
TOKEN = "FAKE-TEST-TOKEN-NOT-A-CREDENTIAL"


def snapshot(revision="3", state="2", updated="2026-10-30 03:10:00"):
    return {
        "sys_id": INCIDENT, "sys_mod_count": revision, "state": state,
        "sys_updated_on": updated,
        "short_description": "PRIVATE-INCIDENT-DESCRIPTION",
        "caller_id": "PRIVATE-EMPLOYEE",
    }


class FakeResponse:
    status = 200
    headers = {"Content-Type": "application/json"}

    def __init__(self, url, rows, *, status=200):
        self.url, self.rows, self.status = url, rows, status

    def geturl(self):
        return self.url

    def read(self, size):
        return json.dumps({"result": self.rows}).encode("utf-8")[:size]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class FakeHTTPS:
    def __init__(self, batches):
        self.batches = batches
        self.calls = []

    def open(self, req, timeout):
        assert req.get_method() == "GET"
        assert req.full_url.startswith(f"https://{HOST}/api/now/table/incident?")
        assert req.get_header("Authorization") == "Bearer " + TOKEN
        assert timeout == 15
        self.calls.append(req.full_url)
        if len(self.calls) > len(self.batches):
            raise AssertionError("Unexpected extra provider read")
        return FakeResponse(req.full_url, self.batches[len(self.calls) - 1])


def setup(tmp_path, monkeypatch):
    if os.name == "posix":
        tmp_path.chmod(0o700)
    path = tmp_path / "grant.json"
    path.write_text(json.dumps({
        "version": 1, "enabled": True, "consented": True,
        "host": HOST, "tenant_scope": "team-a", "sys_ids": [INCIDENT],
    }), encoding="utf-8")
    if os.name == "posix":
        path.chmod(0o600)
    monkeypatch.setenv("OWG_SERVICENOW_TOKEN", TOKEN)
    return path


def cli(tmp_path, grant, *, runs=2, interval=60, prefix="poll", consent=True,
        tenant="team-a"):
    args = [
        "--instance", HOST, "--tenant", tenant, "--incident-id", INCIDENT,
        "--grant-file", str(grant), "--timezone", "UTC",
        "--journal", str(tmp_path / "journal.sqlite"),
        "--output-dir", str(tmp_path), "--output-prefix", prefix,
        "--runs", str(runs), "--interval-seconds", str(interval),
    ]
    if consent:
        args += ["--consent-selected-read", "--consent-bounded-poll"]
    return args


def csv_rows(path):
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def test_two_real_cli_polls_append_new_revision_and_export(tmp_path, monkeypatch, capsys):
    grant = setup(tmp_path, monkeypatch)
    provider = FakeHTTPS([
        [snapshot()],
        [snapshot(revision="4", state="3", updated="2026-10-30 04:10:00")],
    ])
    slept = []
    assert main(cli(tmp_path, grant), opener=provider, sleep_fn=slept.append) == 0
    assert len(provider.calls) == 2
    assert slept == [5] * 12
    first = csv_rows(tmp_path / "poll-001.csv")
    second = csv_rows(tmp_path / "poll-002.csv")
    assert len(first) == 1 and len(second) == 2
    assert {row["activity"] for row in second} == {
        "Incident state: 2", "Incident state: 3",
    }
    assert "PRIVATE-" not in (tmp_path / "poll-002.csv").read_text()
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "completed"
    assert result["completed_polls"] == 2
    assert result["new_snapshots"] == 2
    assert result["journal_snapshots"] == 2
    assert result["csv_exports_created"] == 2
    assert result["historical_completeness"] == "unknown"
    assert result["checkpoint_advanced"] is False
    assert result["automatic_owg_log_append"] is False
    assert result["source_writes_performed"] is False
    if os.name == "posix":
        for name in ("poll-001.csv", "poll-002.csv", "journal.sqlite"):
            assert (tmp_path / name).stat().st_mode & 0o077 == 0


def test_identical_revision_is_replayed_without_duplicate(tmp_path, monkeypatch, capsys):
    grant = setup(tmp_path, monkeypatch)
    provider = FakeHTTPS([[snapshot()], [snapshot()]])
    assert main(cli(tmp_path, grant), opener=provider, sleep_fn=lambda _: None) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["new_snapshots"] == 1
    assert result["replayed_snapshots"] == 1
    assert len(csv_rows(tmp_path / "poll-002.csv")) == 1


def test_revocation_during_wait_stops_before_second_http(tmp_path, monkeypatch, capsys):
    grant = setup(tmp_path, monkeypatch)
    provider = FakeHTTPS([[snapshot()]])
    calls = []

    def revoke_after_first_wait(seconds):
        calls.append(seconds)
        data = json.loads(grant.read_text())
        data["enabled"] = False
        grant.write_text(json.dumps(data), encoding="utf-8")

    assert main(cli(tmp_path, grant), opener=provider,
                sleep_fn=revoke_after_first_wait) == 2
    assert calls == [5]
    assert len(provider.calls) == 1
    assert (tmp_path / "poll-001.csv").exists()
    assert not (tmp_path / "poll-002.csv").exists()
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "refused"
    assert report["completed_polls"] == 1
    assert TOKEN not in json.dumps(report)


def test_second_provider_failure_keeps_first_export_and_stops(tmp_path, monkeypatch, capsys):
    grant = setup(tmp_path, monkeypatch)

    class FailingSecond(FakeHTTPS):
        def open(self, req, timeout):
            response = super().open(req, timeout)
            if len(self.calls) == 2:
                response.status = 403
            return response

    provider = FailingSecond([[snapshot()], [snapshot()]])
    assert main(cli(tmp_path, grant), opener=provider, sleep_fn=lambda _: None) == 2
    assert len(provider.calls) == 2
    assert (tmp_path / "poll-001.csv").exists()
    assert not (tmp_path / "poll-002.csv").exists()
    assert json.loads(capsys.readouterr().out)["completed_polls"] == 1


@pytest.mark.parametrize("runs,interval", [
    (0, 60), (9, 60), (2, 59), (2, 1801), (8, 1800),
])
def test_bounded_run_window_refuses_before_http(
        tmp_path, monkeypatch, runs, interval, capsys):
    grant = setup(tmp_path, monkeypatch)
    provider = FakeHTTPS([])
    assert main(cli(tmp_path, grant, runs=runs, interval=interval),
                opener=provider, sleep_fn=lambda _: None) == 2
    assert not provider.calls
    assert not (tmp_path / "journal.sqlite").exists()
    assert json.loads(capsys.readouterr().out)["status"] == "refused"


def test_no_consent_refuses_before_http(tmp_path, monkeypatch, capsys):
    grant = setup(tmp_path, monkeypatch)
    provider = FakeHTTPS([])
    assert main(cli(tmp_path, grant, consent=False), opener=provider) == 2
    assert not provider.calls
    assert json.loads(capsys.readouterr().out)["completed_polls"] == 0


def test_preexisting_later_output_refuses_before_any_http(
        tmp_path, monkeypatch, capsys):
    grant = setup(tmp_path, monkeypatch)
    (tmp_path / "poll-002.csv").write_text("existing", encoding="utf-8")
    provider = FakeHTTPS([])
    assert main(cli(tmp_path, grant), opener=provider) == 2
    assert not provider.calls
    assert not (tmp_path / "poll-001.csv").exists()
    assert (tmp_path / "poll-002.csv").read_text() == "existing"
    assert json.loads(capsys.readouterr().out)["completed_polls"] == 0


@pytest.mark.parametrize("prefix", ["../outside", ".hidden", "a/b", "a\\b", "a" * 41])
def test_unsafe_output_prefix_refused(tmp_path, monkeypatch, prefix):
    grant = setup(tmp_path, monkeypatch)
    provider = FakeHTTPS([])
    assert main(cli(tmp_path, grant, prefix=prefix), opener=provider) == 2
    assert not provider.calls


def test_scope_mismatch_refused_before_next_provider_read(
        tmp_path, monkeypatch, capsys):
    grant = setup(tmp_path, monkeypatch)
    assert main(cli(tmp_path, grant, runs=1), opener=FakeHTTPS([[snapshot()]])) == 0
    capsys.readouterr()
    data = json.loads(grant.read_text())
    data["tenant_scope"] = "team-b"
    grant.write_text(json.dumps(data))
    provider = FakeHTTPS([])
    assert main(cli(tmp_path, grant, runs=1, prefix="new", tenant="team-b"),
                opener=provider) == 2
    assert not provider.calls
    assert json.loads(capsys.readouterr().out)["completed_polls"] == 0
