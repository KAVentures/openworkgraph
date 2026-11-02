"""Reviewed incremental Salesforce extraction through actual source TCP transport."""
from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from threading import Thread
from urllib import parse

import pytest

from server.salesforce_case_history_incremental import (
    IncrementalError, commit, main, prepare,
)

CASE = "500000000000001"
BEARER = "salesforce-local-test-token-only"
ROW = {"Id": "017000000000001", "CaseId": CASE,
       "CreatedDate": "2026-10-02T12:00:00Z", "Field": "Status",
       "NewValue": "In Progress"}
START = "2026-10-01T00:00:00Z"
END1 = "2026-10-03T00:00:00Z"
END2 = "2026-10-04T00:00:00Z"


@contextmanager
def _provider(rows=None, *, reject=False):
    calls = []
    source = [ROW] if rows is None else rows

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parts = parse.urlsplit(self.path)
            soql = parse.parse_qs(parts.query).get("q", [""])[0]
            calls.append((self.command, self.path, self.headers.get("Authorization"), soql))
            if reject or self.headers.get("Authorization") != "Bearer " + BEARER:
                self.send_response(401)
                self.end_headers()
                return
            if (parts.path != "/services/data/v60.0/query" or "FROM CaseHistory" not in soql
                    or "CaseId IN ('" + CASE + "')" not in soql or "LIMIT 201" not in soql):
                self.send_response(404)
                self.end_headers()
                return
            lower = soql.split("CreatedDate >= ", 1)[1].split(" ", 1)[0]
            upper = soql.split("CreatedDate < ", 1)[1].split(" ", 1)[0]
            selected = [r for r in source if lower <= r["CreatedDate"] < upper]
            body = json.dumps({"done": True, "totalSize": len(selected),
                               "records": selected}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_port), calls
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.fixture
def private_dir(tmp_path):
    folder = tmp_path / "private"
    folder.mkdir(mode=0o700)
    if os.name == "posix":
        folder.chmod(0o700)
    return folder


def _prepare(folder, origin, name, *, until=END1, bootstrap=START,
             namespace="sandbox", cases=None, overlap=24):
    return prepare(instance=origin, confirm_host="127.0.0.1",
                   namespace=namespace, cases=cases or [CASE],
                   bootstrap_since=bootstrap, until=until,
                   overlap_hours=overlap, token=BEARER,
                   checkpoint=folder / "checkpoint.json",
                   manifest=folder / (name + ".json"),
                   output=folder / (name + ".csv"), allow_loopback_http=True)


def _commit(folder, name, receipt, *, imported=True, empty=False):
    return commit(checkpoint=folder / "checkpoint.json",
                  manifest=folder / (name + ".json"),
                  confirm_manifest_sha256=receipt["manifest_sha256"],
                  confirm_imported=imported, confirm_empty_window=empty)


def test_real_tcp_prepare_review_commit_and_overlapping_resume(private_dir):
    with _provider() as (origin, calls):
        first = _prepare(private_dir, origin, "first")
        assert first["status"] == "prepared" and first["events"] == 1
        assert not (private_dir / "checkpoint.json").exists()
        assert len(calls) == 1 and calls[0][0] == "GET"
        assert calls[0][2] == "Bearer " + BEARER
        assert "CreatedDate >= " + START in calls[0][3]
        assert (private_dir / "first.csv").exists()
        assert _commit(private_dir, "first", first)["checkpoint_advanced"] is True
        second = _prepare(private_dir, origin, "second", until=END2, bootstrap=None)
        assert second["events"] == 1
        assert "CreatedDate >= 2026-10-02T00:00:00Z" in calls[1][3]
        assert "CreatedDate < " + END2 in calls[1][3]
        assert (private_dir / "first.csv").read_bytes() == (private_dir / "second.csv").read_bytes()
        assert _commit(private_dir, "second", second)["checkpoint_advanced"] is True
    state = json.loads((private_dir / "checkpoint.json").read_text())
    assert state["until"] == END2
    assert state["overlap_hours"] == 24
    assert len(calls) == 2


def test_no_checkpoint_before_import_and_stale_competing_review(private_dir):
    with _provider() as (origin, calls):
        one = _prepare(private_dir, origin, "one")
        two = _prepare(private_dir, origin, "two")
        with pytest.raises(IncrementalError):
            _commit(private_dir, "one", one, imported=False)
        assert not (private_dir / "checkpoint.json").exists()
        _commit(private_dir, "one", one)
        with pytest.raises(IncrementalError, match="Stale"):
            _commit(private_dir, "two", two)
        with pytest.raises(IncrementalError, match="Stale"):
            _commit(private_dir, "one", one)
    assert len(calls) == 2


def test_tampered_manifest_and_csv_refused_without_checkpoint(private_dir):
    with _provider() as (origin, _):
        first = _prepare(private_dir, origin, "first")
    path = private_dir / "first.csv"
    path.write_text(path.read_text() + "\n")
    with pytest.raises(IncrementalError, match="CSV changed"):
        _commit(private_dir, "first", first)
    path = private_dir / "first.json"
    path.write_text(path.read_text() + "\n")
    with pytest.raises(IncrementalError):
        _commit(private_dir, "first", first)
    assert not (private_dir / "checkpoint.json").exists()


def test_empty_window_requires_separate_explicit_confirmation(private_dir):
    with _provider(rows=[]) as (origin, calls):
        empty = _prepare(private_dir, origin, "empty")
        assert empty["events"] == 0 and empty["csv_created"] is False
        assert not (private_dir / "empty.csv").exists()
        with pytest.raises(IncrementalError):
            _commit(private_dir, "empty", empty)
        with pytest.raises(IncrementalError):
            _commit(private_dir, "empty", empty, imported=True, empty=True)
        result = _commit(private_dir, "empty", empty, imported=False, empty=True)
        assert result["operator_reviewed_empty"] is True
        assert json.loads((private_dir / "checkpoint.json").read_text())["until"] == END1
    assert len(calls) == 1


def test_changed_scope_replay_window_and_private_dir_fail_before_provider_read(private_dir):
    with _provider() as (origin, calls):
        first = _prepare(private_dir, origin, "first")
        _commit(private_dir, "first", first)
        with pytest.raises(IncrementalError, match="scope mismatch"):
            _prepare(private_dir, origin, "wrong", until=END2, bootstrap=None,
                     namespace="different")
        with pytest.raises(IncrementalError, match="upper bound"):
            _prepare(private_dir, origin, "replay", until=END1, bootstrap=None)
        with pytest.raises(IncrementalError, match="scope mismatch"):
            _prepare(private_dir, origin, "overlap", until=END2,
                     bootstrap=None, overlap=48)
        if os.name == "posix":
            private_dir.chmod(0o755)
            with pytest.raises(IncrementalError):
                _prepare(private_dir, origin, "unsafe", until=END2, bootstrap=None)
    assert len(calls) == 1


def test_failed_provider_read_never_creates_checkpoint_or_review(private_dir):
    with _provider(reject=True) as (origin, calls):
        with pytest.raises(ValueError):
            _prepare(private_dir, origin, "fail")
    assert len(calls) == 1
    assert not list(private_dir.iterdir())


def test_cli_consent_gate_and_no_identifiers_in_receipts(private_dir, monkeypatch, capsys):
    monkeypatch.setenv("OWG_SALESFORCE_READ_TOKEN", BEARER)
    with _provider() as (origin, calls):
        args = ["prepare", "--instance", origin, "--confirm-host", "127.0.0.1",
                "--namespace", "sandbox", "--case", CASE,
                "--bootstrap-since", START, "--until", END1,
                "--checkpoint", str(private_dir / "checkpoint.json"),
                "--manifest", str(private_dir / "cli.json"),
                "--output", str(private_dir / "cli.csv"), "--allow-loopback-http"]
        assert main(args) == 2
        assert not calls
        assert main(args + ["--consent-selected-read", "--consent-private-export"]) == 0
    text = capsys.readouterr().out
    assert CASE not in text and BEARER not in text and "sandbox" not in text
    assert '"checkpoint_advanced": false' in text
    assert not (private_dir / "checkpoint.json").exists()
