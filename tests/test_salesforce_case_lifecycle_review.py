"""Real TCP Salesforce queryAll -> private lifecycle review integration tests."""
from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from threading import Thread
from urllib import parse

import pytest

from scripts.salesforce_case_history import _key
from server.salesforce_case_lifecycle_review import (
    LifecycleReviewError, FORMAT, main, review,
)

CASE_A = "500000000000001"
CASE_B = "500000000000002"
CASE_C = "500000000000003"
FOREIGN = "500000000000004"
TOKEN = "salesforce-local-test-token-only"


@contextmanager
def provider(rows=None, *, failure=None):
    calls = []
    if rows is None:
        rows = [{"Id": CASE_A, "IsDeleted": True},
                {"Id": CASE_B, "IsDeleted": False}]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append((self.command, self.path, self.headers.get("Authorization")))
            if failure == "unauthorized" or self.headers.get("Authorization") != "Bearer " + TOKEN:
                self.send_response(401)
                self.end_headers()
                return
            if failure == "rate_limit":
                self.send_response(429)
                self.end_headers()
                return
            if failure == "redirect":
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:1/unsafe")
                self.end_headers()
                return
            parts = parse.urlsplit(self.path)
            soql = parse.parse_qs(parts.query).get("q", [""])[0]
            if (parts.path != "/services/data/v60.0/queryAll"
                    or "SELECT Id, IsDeleted FROM Case WHERE Id IN (" not in soql
                    or "ORDER BY Id LIMIT 9" not in soql
                    or any(case not in soql for case in (CASE_A, CASE_B, CASE_C))):
                self.send_response(404)
                self.end_headers()
                return
            data = {"done": True, "totalSize": len(rows), "records": rows}
            if failure == "incomplete":
                data["done"] = False
                data["nextRecordsUrl"] = "/services/data/v60.0/query/next"
            if failure == "count":
                data["totalSize"] += 1
            if failure == "duplicate_json":
                raw = b'{"done":true,"done":true,"totalSize":0,"records":[]}'
            elif failure == "oversize":
                raw = json.dumps({**data, "unexpected": "x" * 70000}).encode()
            else:
                raw = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain" if failure == "media" else "application/json")
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
    folder = tmp_path / "private"
    folder.mkdir(mode=0o700)
    if os.name == "posix":
        folder.chmod(0o700)
    return folder


def run(origin, output, *, cases=None, token=TOKEN, namespace="sandbox"):
    return review(instance=origin, confirm_host="127.0.0.1", namespace=namespace,
                  cases=cases or [CASE_A, CASE_B, CASE_C], token=token,
                  output=output, allow_loopback_http=True)


def test_real_queryall_deleted_active_unresolved_and_private_artifact(private_dir):
    output = private_dir / "lifecycle.json"
    with provider() as (origin, calls):
        receipt = run(origin, output)
    assert len(calls) == 1 and calls[0][0] == "GET"
    assert calls[0][2] == "Bearer " + TOKEN
    assert receipt["status"] == "review_created"
    assert receipt["counts"] == {"active": 1, "soft_deleted": 1, "unresolved": 1}
    assert receipt["source_completeness"] == "unknown"
    assert receipt["checkpoint_advanced"] is False
    assert receipt["owg_deletion_performed"] is False
    assert receipt["source_writes_performed"] is False
    artifact = json.loads(output.read_text())
    assert artifact["format"] == FORMAT
    assert artifact["hard_deletion_proven"] is False
    assert artifact["missing_means_deleted"] is False
    assert artifact["review_required"] is True
    assert {entry["case_id"]: entry["source_state"] for entry in artifact["case_states"]} == {
        _key("sandbox", "case", CASE_A): "soft_deleted",
        _key("sandbox", "case", CASE_B): "active",
        _key("sandbox", "case", CASE_C): "unresolved",
    }
    combined = output.read_text() + json.dumps(receipt)
    for secret in (CASE_A, CASE_B, CASE_C, TOKEN, "sandbox"):
        assert secret not in combined
    if os.name == "posix":
        assert output.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("rows,failure", [
    (None, "unauthorized"), (None, "rate_limit"), (None, "redirect"),
    (None, "incomplete"), (None, "count"), (None, "duplicate_json"),
    (None, "oversize"), (None, "media"),
    ([{"Id": FOREIGN, "IsDeleted": True}], None),
    ([{"Id": CASE_A, "IsDeleted": True}, {"Id": CASE_A, "IsDeleted": False}], None),
    ([{"Id": CASE_A, "IsDeleted": "true"}], None),
    ([{"Id": CASE_A}], None),
])
def test_failed_or_untrustworthy_provider_read_never_creates_review(private_dir, rows, failure):
    output = private_dir / "refused.json"
    with provider(rows, failure=failure) as (origin, calls):
        with pytest.raises(LifecycleReviewError):
            run(origin, output)
    assert len(calls) == 1 and not output.exists()
    assert all(call[0] == "GET" for call in calls)


def test_missing_all_cases_is_unresolved_not_deletion(private_dir):
    with provider([]) as (origin, calls):
        receipt = run(origin, private_dir / "missing.json")
    assert receipt["counts"] == {"active": 0, "soft_deleted": 0, "unresolved": 3}
    assert len(calls) == 1


def test_invalid_scope_output_or_credentials_fail_before_network(private_dir):
    existing = private_dir / "existing.json"
    existing.write_text("do not overwrite")
    with provider() as (origin, calls):
        for kwargs in (
            {"cases": [CASE_A, CASE_A]},
            {"cases": [FOREIGN, "invalid"]},
            {"token": "short"},
            {"namespace": "bad namespace"},
        ):
            with pytest.raises(ValueError):
                run(origin, private_dir / "new.json", **kwargs)
        with pytest.raises(LifecycleReviewError):
            run(origin, existing)
        with pytest.raises(ValueError):
            review(instance=origin, confirm_host="foreign.example.com",
                   namespace="sandbox", cases=[CASE_A], token=TOKEN,
                   output=private_dir / "new.json", allow_loopback_http=True)
    assert not calls and existing.read_text() == "do not overwrite"


def test_cli_requires_both_consents_and_never_prints_source_ids(private_dir, monkeypatch, capsys):
    monkeypatch.setenv("OWG_SALESFORCE_READ_TOKEN", TOKEN)
    with provider() as (origin, calls):
        args = ["--instance", origin, "--confirm-host", "127.0.0.1",
                "--namespace", "sandbox", "--case", CASE_A, "--case", CASE_B,
                "--case", CASE_C, "--output", str(private_dir / "cli.json"),
                "--allow-loopback-http"]
        assert main(args) == 2
        assert main(args + ["--consent-source-lifecycle-read"]) == 2
        assert not calls
        assert main(args + ["--consent-source-lifecycle-read", "--consent-private-review"]) == 0
    printed = capsys.readouterr().out
    assert '"status": "refused"' in printed and '"status": "review_created"' in printed
    assert all(value not in printed for value in (CASE_A, CASE_B, CASE_C, TOKEN, "sandbox"))
    assert len(calls) == 1
