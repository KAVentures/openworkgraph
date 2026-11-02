"""Real TCP Salesforce query cursor acceptance through live and incremental OWG CSV paths."""
from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
from threading import Thread
from urllib import parse

import pytest

from server.salesforce_case_history_live import LiveHistoryError, extract, main
from server.salesforce_case_history_incremental import prepare
from shared.event_log_formats import EventRecord, ReadStats, read_csv

CASE = "500000000000001"
OTHER = "500000000000002"
TOKEN = "salesforce-pagination-local-test-token"
SINCE = "2026-10-01T00:00:00Z"
UNTIL = "2026-10-02T00:00:00Z"
CURSOR = "/services/data/v60.0/query/01gPAGE2-1"
ROW1 = {"Id": "017000000000001", "CaseId": CASE,
        "CreatedDate": "2026-10-01T11:00:00Z", "Field": "Status",
        "NewValue": "In Progress"}
ROW2 = {"Id": "017000000000002", "CaseId": CASE,
        "CreatedDate": "2026-10-01T12:00:00Z", "Field": "Status",
        "NewValue": "Closed"}


@contextmanager
def source(mode="ok"):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append((self.command, self.path, self.headers.get("Authorization")))
            parsed = parse.urlsplit(self.path)
            if self.headers.get("Authorization") != "Bearer " + TOKEN:
                self.send_response(401)
                self.end_headers()
                return
            first = parsed.path == "/services/data/v60.0/query"
            second = parsed.path == CURSOR
            if not (first or second):
                self.send_response(404)
                self.end_headers()
                return
            if first:
                soql = parse.parse_qs(parsed.query).get("q", [""])[0]
                if ("FROM CaseHistory" not in soql
                        or "CaseId IN ('" + CASE + "')" not in soql
                        or "LIMIT 201" not in soql):
                    self.send_response(400)
                    self.end_headers()
                    return
                cursor = CURSOR
                if mode == "off_origin":
                    cursor = "https://example.invalid/services/data/v60.0/query/01gBAD"
                elif mode == "query_cursor":
                    cursor = CURSOR + "?q=secret"
                elif mode == "path_traversal":
                    cursor = "/services/data/v60.0/query/../query/01gBAD"
                payload = {"done": False, "totalSize": 2, "records": [ROW1],
                           "nextRecordsUrl": cursor}
                if mode == "empty_page":
                    payload["records"] = []
                elif mode == "over_limit":
                    payload["totalSize"] = 201
            else:
                if mode == "unauthorized":
                    self.send_response(401)
                    self.end_headers()
                    return
                if mode == "redirect":
                    self.send_response(302)
                    self.send_header("Location", "http://127.0.0.1:1/unsafe")
                    self.end_headers()
                    return
                row = ROW2
                if mode == "foreign":
                    row = {**row, "CaseId": OTHER}
                elif mode == "duplicate":
                    row = {**row, "Id": ROW1["Id"]}
                elif mode == "out_of_order":
                    row = {**row, "CreatedDate": "2026-10-01T10:00:00Z"}
                payload = {"done": True, "totalSize": 2, "records": [row]}
                if mode == "changed_count":
                    payload["totalSize"] = 3
                elif mode == "truncated":
                    payload["records"] = []
                elif mode == "repeat":
                    payload.update(done=False, nextRecordsUrl=CURSOR)
                elif mode == "done_extra_cursor":
                    payload["nextRecordsUrl"] = CURSOR
            raw = (b'{"done":' if second and mode == "malformed_json"
                   else json.dumps(payload).encode("utf-8"))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
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
def private(tmp_path):
    directory = tmp_path / "owner-only"
    directory.mkdir(mode=0o700)
    if os.name == "posix":
        directory.chmod(0o700)
    return directory


def read(origin, output, *, max_pages=2):
    return extract(origin, "127.0.0.1", "sandbox", [CASE], SINCE, UNTIL,
                   TOKEN, output, allow_loopback_http=True, max_pages=max_pages)


def test_real_two_page_http_to_production_owg_csv_parser(private):
    with source() as (origin, calls):
        receipt = read(origin, private / "history.csv")
    assert receipt["status"] == "exported"
    assert receipt["events"] == 2 and receipt["source_rows"] == 2
    assert receipt["pages_fetched"] == 2
    assert len(calls) == 2 and all(c[0] == "GET" for c in calls)
    assert all(c[2] == "Bearer " + TOKEN for c in calls)
    assert calls[1][1] == CURSOR
    raw = (private / "history.csv").read_text()
    assert all(s not in raw and s not in json.dumps(receipt)
               for s in [CASE, ROW1["Id"], ROW2["Id"], TOKEN, "sandbox"])
    stats = ReadStats()
    events = list(read_csv(io.StringIO(raw), case_column="case_id",
                           activity_column="activity", timestamp_column="timestamp",
                           attribute_columns=["source_event_id"], stats=stats))
    assert stats.events == 2
    assert len([e for e in events if isinstance(e, EventRecord)]) == 2


def test_real_two_page_incremental_prepare_digest_and_no_checkpoint(private):
    with source() as (origin, calls):
        receipt = prepare(
            instance=origin, confirm_host="127.0.0.1", namespace="sandbox",
            cases=[CASE], bootstrap_since=SINCE, until=UNTIL,
            overlap_hours=24, token=TOKEN,
            checkpoint=private / "checkpoint.json",
            manifest=private / "review.json", output=private / "events.csv",
            allow_loopback_http=True, max_pages=2)
    assert receipt["status"] == "prepared"
    assert receipt["pages_fetched"] == 2 and receipt["events"] == 2
    assert len(receipt["manifest_sha256"]) == 64
    assert len(calls) == 2
    assert (private / "events.csv").exists()
    assert (private / "review.json").exists()
    assert not (private / "checkpoint.json").exists()


@pytest.mark.parametrize("mode", [
    "off_origin", "query_cursor", "path_traversal", "empty_page",
    "over_limit", "unauthorized", "redirect",
    "foreign", "duplicate", "out_of_order", "changed_count", "truncated",
    "repeat", "done_extra_cursor", "malformed_json",
])
def test_untrusted_or_incomplete_cursor_refuses_without_artifact(private, mode):
    output = private / "bad.csv"
    with source(mode) as (origin, calls):
        with pytest.raises(LiveHistoryError):
            read(origin, output)
    assert not output.exists()
    assert 1 <= len(calls) <= 2
    assert all(call[0] == "GET" for call in calls)


def test_default_single_page_and_invalid_page_bounds_refuse(private):
    with source() as (origin, calls):
        with pytest.raises(LiveHistoryError):
            read(origin, private / "default.csv", max_pages=1)
        assert len(calls) == 1
        for bound in (0, 9, True, "2"):
            with pytest.raises(LiveHistoryError):
                read(origin, private / "invalid.csv", max_pages=bound)
        assert len(calls) == 1
    assert not (private / "default.csv").exists()
    assert not (private / "invalid.csv").exists()


def test_cli_requires_explicit_consents_and_pagination_flag(private, monkeypatch, capsys):
    monkeypatch.setenv("OWG_SALESFORCE_READ_TOKEN", TOKEN)
    with source() as (origin, calls):
        args = ["--instance", origin, "--confirm-host", "127.0.0.1",
                "--namespace", "sandbox", "--case", CASE, "--since", SINCE,
                "--until", UNTIL, "--output", str(private / "cli.csv"),
                "--allow-loopback-http", "--max-pages", "2"]
        assert main(args) == 2
        assert not calls
        assert main(args + ["--consent-selected-read", "--consent-private-export"]) == 0
    assert len(calls) == 2
    output = capsys.readouterr().out
    assert '"pages_fetched": 2' in output
    assert TOKEN not in output and CASE not in output
