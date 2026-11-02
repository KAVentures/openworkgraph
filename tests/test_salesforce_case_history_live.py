"""Real loopback Salesforce-shaped HTTPS-read contract and actual OWG CSV parser."""
from __future__ import annotations

from contextlib import contextmanager
import csv
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
from threading import Thread
from urllib import parse

import pytest

from server.salesforce_case_history_live import (
    FORMAT, LiveHistoryError, extract, main,
)
from shared.event_log_formats import EventRecord, ObjectRecord, ReadStats, read_csv

CASE = "500000000000001"
OTHER = "500000000000002"
HISTORY = "017000000000001"
SINCE = "2026-10-01T00:00:00Z"
UNTIL = "2026-10-02T00:00:00Z"
BEARER = "salesforce-local-test-token-only"
ROW = {"Id": HISTORY, "CaseId": CASE, "CreatedDate": "2026-10-01T12:00:00Z",
       "Field": "Status", "NewValue": "In Progress",
       "OldValue": "private", "CreatedBy": {"Name": "private"}}


@contextmanager
def _salesforce(rows=None, *, failure=None):
    calls = []
    rows = [ROW] if rows is None else rows

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append((self.command, self.path, self.headers.get("Authorization")))
            if failure == "401" or self.headers.get("Authorization") != "Bearer " + BEARER:
                self.send_response(401)
                self.end_headers()
                return
            if failure == "429":
                self.send_response(429)
                self.end_headers()
                return
            if failure == "redirect":
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:1/unsafe")
                self.end_headers()
                return
            parsed = parse.urlsplit(self.path)
            soql = parse.parse_qs(parsed.query).get("q", [""])[0]
            if (parsed.path != "/services/data/v60.0/query"
                    or "FROM CaseHistory" not in soql
                    or "CaseId IN ('" + CASE + "')" not in soql
                    or "CreatedDate >= " + SINCE not in soql
                    or "CreatedDate < " + UNTIL not in soql
                    or "LIMIT 201" not in soql):
                self.send_response(404)
                self.end_headers()
                return
            body = {"done": True, "totalSize": len(rows), "records": rows}
            if failure == "pagination":
                body["done"] = False
                body["nextRecordsUrl"] = "/services/data/v60.0/query/next"
            if failure == "count":
                body["totalSize"] += 1
            if failure == "duplicate_json":
                raw = b'{"done":true,"done":true,"totalSize":0,"records":[]}'
            elif failure == "oversize":
                raw = json.dumps({**body, "unused": "x" * 300000}).encode()
            else:
                raw = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json;charset=UTF-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            calls.append((self.command, self.path, None))
            self.send_response(405)
            self.end_headers()
        do_PATCH = do_POST
        do_DELETE = do_POST

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
    folder = tmp_path / "owner-private"
    folder.mkdir(mode=0o700)
    if os.name == "posix":
        folder.chmod(0o700)
    return folder


def _extract(origin, output, **kwargs):
    return extract(origin, "127.0.0.1", "acme-sandbox", [CASE], SINCE, UNTIL,
                   BEARER, output, allow_loopback_http=True, **kwargs)


def test_real_http_source_to_production_owg_csv_parser(private_dir):
    output = private_dir / "selected.csv"
    with _salesforce() as (origin, calls):
        receipt = _extract(origin, output)
    assert receipt["status"] == "exported"
    assert receipt["events"] == 1 and receipt["csv_created"] is True
    assert len(calls) == 1 and calls[0][0] == "GET"
    assert calls[0][2] == "Bearer " + BEARER
    assert output.stat().st_mode & 0o077 == 0 if os.name == "posix" else True
    raw = output.read_text()
    assert all(secret not in raw and secret not in json.dumps(receipt)
               for secret in [CASE, HISTORY, "private", BEARER, "acme-sandbox"])
    assert "In Progress" in raw
    stats = ReadStats()
    parsed = list(read_csv(io.StringIO(raw), case_column="case_id",
                           activity_column="activity", timestamp_column="timestamp",
                           attribute_columns=["source_event_id"], stats=stats))
    assert len(parsed) == 2 and stats.events == 1 and stats.objects == 1
    assert isinstance(parsed[0], ObjectRecord)
    assert isinstance(parsed[1], EventRecord)
    assert parsed[1].attributes["source_event_id"].startswith("sf:")
    assert receipt["source_completeness"] == "unknown"
    assert receipt["source_writes_performed"] is False


def test_empty_history_is_nonwriting_and_explicit(private_dir):
    output = private_dir / "empty.csv"
    with _salesforce([]) as (origin, calls):
        receipt = _extract(origin, output)
    assert receipt["status"] == "no_selected_history"
    assert receipt["csv_created"] is False and not output.exists()
    assert len(calls) == 1


@pytest.mark.parametrize("failure", [
    "401", "429", "redirect", "pagination", "count", "duplicate_json", "oversize",
])
def test_network_and_incomplete_pages_fail_closed(private_dir, failure):
    output = private_dir / "refused.csv"
    with _salesforce(failure=failure) as (origin, calls):
        with pytest.raises(LiveHistoryError):
            _extract(origin, output)
    assert len(calls) == 1 and not output.exists()


@pytest.mark.parametrize("rows", [
    [{**ROW, "CaseId": OTHER}],
    [{**ROW, "CreatedDate": "2026-10-04T12:00:00Z"}],
    [{**ROW, "Id": "bad"}],
    [{**ROW, "Field": "Subject"}],
    [{**ROW, "NewValue": {"nested": "secret"}}],
    [ROW, {**ROW, "NewValue": "Closed"}],
])
def test_source_scope_or_conflicting_history_refuses(private_dir, rows):
    output = private_dir / "bad.csv"
    with _salesforce(rows) as (origin, calls):
        with pytest.raises(LiveHistoryError):
            _extract(origin, output)
    assert len(calls) == 1 and not output.exists()


def test_private_destination_and_existing_output_preflight_no_provider_read(private_dir):
    output = private_dir / "existing.csv"
    output.write_text("do not overwrite")
    with _salesforce() as (origin, calls):
        with pytest.raises(LiveHistoryError):
            _extract(origin, output)
    assert not calls and output.read_text() == "do not overwrite"
    if os.name == "posix":
        private_dir.chmod(0o755)
        with _salesforce() as (origin, calls):
            with pytest.raises(LiveHistoryError):
                _extract(origin, private_dir / "new.csv")
        assert not calls


def test_invalid_selection_and_host_refuse_before_get(private_dir):
    with _salesforce() as (origin, calls):
        with pytest.raises(LiveHistoryError):
            extract(origin, "wrong-host", "acme-sandbox", [CASE], SINCE, UNTIL,
                    BEARER, private_dir / "bad.csv", allow_loopback_http=True)
        with pytest.raises(LiveHistoryError):
            extract(origin, "127.0.0.1", "acme-sandbox", [CASE, CASE], SINCE, UNTIL,
                    BEARER, private_dir / "bad.csv", allow_loopback_http=True)
        with pytest.raises(LiveHistoryError):
            extract(origin, "127.0.0.1", "acme-sandbox", [CASE], UNTIL, SINCE,
                    BEARER, private_dir / "bad.csv", allow_loopback_http=True)
    assert not calls


def test_cli_explicit_consents_and_no_secret_receipt(private_dir, monkeypatch, capsys):
    output = private_dir / "cli.csv"
    monkeypatch.setenv("OWG_SALESFORCE_READ_TOKEN", BEARER)
    with _salesforce() as (origin, calls):
        args = ["--instance", origin, "--confirm-host", "127.0.0.1",
                "--namespace", "acme-sandbox", "--case", CASE,
                "--since", SINCE, "--until", UNTIL, "--output", str(output),
                "--allow-loopback-http"]
        assert main(args) == 2
        assert not calls
        assert main(args + ["--consent-selected-read", "--consent-private-export"]) == 0
    text = capsys.readouterr().out
    assert '"status": "refused"' in text and '"status": "exported"' in text
    assert CASE not in text and BEARER not in text and "acme-sandbox" not in text
    assert output.exists()
