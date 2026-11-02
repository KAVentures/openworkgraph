"""Real TCP Salesforce queryAll -> private lifecycle review integration tests."""
from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import hashlib
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


# Multi-page queryAll acceptance is real HTTP, not a mocked opener.
CURSOR = "/services/data/v60.0/query/01gLIFECYCLEPAGE2"
ROW_A = {"Id": CASE_A, "IsDeleted": True}
ROW_B = {"Id": CASE_B, "IsDeleted": False}


@contextmanager
def paged_provider(mode="ok"):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append((self.command, self.path, self.headers.get("Authorization")))
            if self.headers.get("Authorization") != "Bearer " + TOKEN:
                self.send_response(401)
                self.end_headers()
                return
            parts = parse.urlsplit(self.path)
            first = parts.path == "/services/data/v60.0/queryAll"
            second = parts.path == CURSOR
            if not (first or second):
                self.send_response(404)
                self.end_headers()
                return
            if first:
                soql = parse.parse_qs(parts.query).get("q", [""])[0]
                if ("SELECT Id, IsDeleted FROM Case WHERE Id IN (" not in soql
                        or "ORDER BY Id LIMIT 9" not in soql
                        or any(case not in soql for case in (CASE_A, CASE_B, CASE_C))):
                    self.send_response(400)
                    self.end_headers()
                    return
                cursor = CURSOR
                if mode == "off_origin":
                    cursor = "https://example.invalid/services/data/v60.0/query/01gBAD"
                elif mode == "query_cursor":
                    cursor += "?q=exfiltrate"
                elif mode == "path_traversal":
                    cursor = "/services/data/v60.0/query/../query/01gBAD"
                data = {"done": False, "totalSize": 2, "records": [ROW_A],
                        "nextRecordsUrl": cursor}
                if mode == "empty_first":
                    data["records"] = []
                elif mode == "oversized_count":
                    data["totalSize"] = 9
                elif mode == "missing_cursor":
                    del data["nextRecordsUrl"]
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
                row = ROW_B
                if mode == "duplicate":
                    row = ROW_A
                elif mode == "foreign":
                    row = {"Id": FOREIGN, "IsDeleted": False}
                elif mode == "out_of_order":
                    row = {"Id": "500000000000000", "IsDeleted": False}
                data = {"done": True, "totalSize": 2, "records": [row]}
                if mode == "changed_count":
                    data["totalSize"] = 3
                elif mode == "truncated":
                    data["records"] = []
                elif mode == "repeat":
                    data.update(done=False, nextRecordsUrl=CURSOR)
                elif mode == "done_with_cursor":
                    data["nextRecordsUrl"] = CURSOR
            raw = (b'{"done":' if second and mode == "bad_json"
                   else json.dumps(data).encode("utf-8"))
            self.send_response(200)
            self.send_header("Content-Type",
                             "text/plain" if second and mode == "bad_media"
                             else "application/json")
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


def paged_run(origin, output, *, max_pages=2):
    return review(instance=origin, confirm_host="127.0.0.1", namespace="sandbox",
                  cases=[CASE_A, CASE_B, CASE_C], token=TOKEN,
                  output=output, allow_loopback_http=True, max_pages=max_pages)


def test_complete_two_page_queryall_produces_private_review(private_dir):
    output = private_dir / "two-page.json"
    with paged_provider() as (origin, calls):
        receipt = paged_run(origin, output)
    assert receipt["status"] == "review_created"
    assert receipt["pages_fetched"] == 2
    assert receipt["counts"] == {"active": 1, "soft_deleted": 1, "unresolved": 1}
    assert len(calls) == 2 and calls[1][1] == CURSOR
    assert all(c[0] == "GET" and c[2] == "Bearer " + TOKEN for c in calls)
    artifact = json.loads(output.read_text())
    assert artifact["pages_fetched"] == 2
    assert artifact["source_completeness"] == "unknown"
    assert artifact["missing_means_deleted"] is False
    assert artifact["checkpoint_advanced"] is False
    assert {entry["case_id"]: entry["source_state"]
            for entry in artifact["case_states"]} == {
                _key("sandbox", "case", CASE_A): "soft_deleted",
                _key("sandbox", "case", CASE_B): "active",
                _key("sandbox", "case", CASE_C): "unresolved",
            }
    for secret in (CASE_A, CASE_B, CASE_C, TOKEN, "sandbox"):
        assert secret not in output.read_text() + json.dumps(receipt)
    if os.name == "posix":
        assert output.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("mode", [
    "off_origin", "query_cursor", "path_traversal", "empty_first",
    "oversized_count", "missing_cursor", "unauthorized", "redirect",
    "duplicate", "foreign", "out_of_order", "changed_count", "truncated",
    "repeat", "done_with_cursor", "bad_json", "bad_media",
])
def test_untrusted_or_incomplete_queryall_cursor_refuses_without_review(private_dir, mode):
    output = private_dir / "bad-page.json"
    with paged_provider(mode) as (origin, calls):
        with pytest.raises(LifecycleReviewError):
            paged_run(origin, output)
    assert not output.exists()
    assert 1 <= len(calls) <= 2
    assert all(c[0] == "GET" for c in calls)


def test_default_page_budget_and_invalid_budgets_fail_closed(private_dir):
    with paged_provider() as (origin, calls):
        with pytest.raises(LifecycleReviewError):
            paged_run(origin, private_dir / "default.json", max_pages=1)
        assert len(calls) == 1
        for bound in (0, 9, True, "2"):
            with pytest.raises(LifecycleReviewError):
                paged_run(origin, private_dir / "invalid.json", max_pages=bound)
        assert len(calls) == 1
    assert not (private_dir / "default.json").exists()
    assert not (private_dir / "invalid.json").exists()


def test_two_page_cli_requires_consents_and_explicit_page_budget(
        private_dir, monkeypatch, capsys):
    monkeypatch.setenv("OWG_SALESFORCE_READ_TOKEN", TOKEN)
    with paged_provider() as (origin, calls):
        args = ["--instance", origin, "--confirm-host", "127.0.0.1",
                "--namespace", "sandbox", "--case", CASE_A, "--case", CASE_B,
                "--case", CASE_C, "--output", str(private_dir / "cli-paged.json"),
                "--allow-loopback-http", "--max-pages", "2"]
        assert main(args) == 2
        assert not calls
        assert main(args + ["--consent-source-lifecycle-read",
                            "--consent-private-review"]) == 0
    assert len(calls) == 2
    assert (private_dir / "cli-paged.json").exists()
    printed = capsys.readouterr().out
    assert '"pages_fetched": 2' in printed
    assert all(secret not in printed for secret in (CASE_A, CASE_B, CASE_C, TOKEN, "sandbox"))


def _compare(origin, output, previous, previous_sha, *, namespace="sandbox", consent=True):
    return review(
        instance=origin, confirm_host="127.0.0.1", namespace=namespace,
        cases=[CASE_A, CASE_B, CASE_C], token=TOKEN, output=output,
        allow_loopback_http=True, previous_review=previous,
        previous_sha256=previous_sha, consent_compare_previous=consent,
    )


def test_two_real_source_snapshots_expose_case_transitions_without_writes(private_dir):
    first = private_dir / "first.json"
    second = private_dir / "second.json"
    third = private_dir / "third.json"
    with provider() as (origin, calls):
        receipt1 = run(origin, first)
    assert len(calls) == 1
    baseline = first.read_bytes()
    with provider([
        {"Id": CASE_A, "IsDeleted": False},
        {"Id": CASE_B, "IsDeleted": True},
        {"Id": CASE_C, "IsDeleted": False},
    ]) as (origin, calls):
        receipt2 = _compare(origin, second, first, receipt1["review_sha256"])
    assert len(calls) == 1 and all(call[0] == "GET" for call in calls)
    assert first.read_bytes() == baseline
    assert receipt2["comparison"] == {
        "changed": 3, "unchanged": 0,
        "previous_review_sha256": receipt1["review_sha256"],
    }
    artifact = json.loads(second.read_text())
    assert artifact["comparison"]["previous_checked_at_utc"] == json.loads(
        baseline)["checked_at_utc"]
    assert artifact["comparison"]["transitions"] == [
        {"case_id": _key("sandbox", "case", case), "previous_state": old,
         "current_state": new}
        for case, old, new in sorted([
            (CASE_A, "soft_deleted", "active"),
            (CASE_B, "active", "soft_deleted"),
            (CASE_C, "unresolved", "active"),
        ], key=lambda row: _key("sandbox", "case", row[0]))
    ]
    assert artifact["missing_means_deleted"] is False
    assert artifact["owg_deletion_performed"] is False
    assert artifact["checkpoint_advanced"] is False
    with provider([
        {"Id": CASE_A, "IsDeleted": False},
        {"Id": CASE_B, "IsDeleted": True},
    ]) as (origin, calls):
        receipt3 = _compare(origin, third, second, receipt2["review_sha256"])
    assert len(calls) == 1
    assert receipt3["comparison"]["changed"] == 1
    assert receipt3["comparison"]["unchanged"] == 2
    assert json.loads(third.read_text())["comparison"]["transitions"]
    for secret in (CASE_A, CASE_B, CASE_C, TOKEN, "sandbox"):
        assert secret not in first.read_text() + second.read_text() + third.read_text()
        assert secret not in json.dumps(receipt2) + json.dumps(receipt3)
    if os.name == "posix":
        assert second.stat().st_mode & 0o077 == 0
        assert third.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("kind", [
    "missing_digest", "wrong_digest", "no_consent", "wrong_namespace",
    "altered_state", "altered_counts", "altered_flags", "noncanonical",
    "symlink", "wrong_case", "unknown_key",
])
def test_previous_review_refusal_happens_before_provider_get(private_dir, kind):
    first = private_dir / "baseline.json"
    with provider() as (origin, _):
        original = run(origin, first)
    digest = original["review_sha256"]
    previous = first
    namespace = "sandbox"
    consent = True
    if kind == "missing_digest":
        digest = None
    elif kind == "wrong_digest":
        digest = "a" * 64
    elif kind == "no_consent":
        consent = False
    elif kind == "wrong_namespace":
        namespace = "different"
    elif kind == "symlink":
        previous = private_dir / "linked.json"
        previous.symlink_to(first)
    elif kind in ("altered_state", "altered_counts", "altered_flags",
                  "noncanonical", "wrong_case", "unknown_key"):
        prior = json.loads(first.read_text())
        if kind == "altered_state":
            prior["case_states"][0]["source_state"] = "purged"
        elif kind == "altered_counts":
            prior["counts"]["active"] = 99
        elif kind == "altered_flags":
            prior["owg_deletion_performed"] = True
        elif kind == "wrong_case":
            prior["case_states"][0]["case_id"] = "a" * 64
        elif kind == "unknown_key":
            prior["source_token"] = "must-refuse"
        previous = private_dir / "altered.json"
        if kind == "noncanonical":
            previous.write_text(json.dumps(prior, indent=2))
        else:
            previous.write_text(json.dumps(prior, sort_keys=True,
                                           separators=(",", ":")) + "\n")
        if os.name == "posix":
            previous.chmod(0o600)
        digest = hashlib.sha256(previous.read_bytes()).hexdigest()
    with provider() as (origin, calls):
        with pytest.raises(LifecycleReviewError):
            _compare(origin, private_dir / "not-created.json", previous, digest,
                     namespace=namespace, consent=consent)
    assert calls == []
    assert not (private_dir / "not-created.json").exists()


def test_cli_previous_review_requires_explicit_separate_consent(private_dir,
                                                                monkeypatch, capsys):
    monkeypatch.setenv("OWG_SALESFORCE_READ_TOKEN", TOKEN)
    first = private_dir / "first.json"
    with provider() as (origin, _):
        receipt = run(origin, first)
    with provider([{"Id": CASE_A, "IsDeleted": False}]) as (origin, calls):
        args = ["--instance", origin, "--confirm-host", "127.0.0.1",
                "--namespace", "sandbox", "--case", CASE_A, "--case", CASE_B,
                "--case", CASE_C, "--output", str(private_dir / "second.json"),
                "--previous-review", str(first),
                "--previous-sha256", receipt["review_sha256"],
                "--allow-loopback-http", "--consent-source-lifecycle-read",
                "--consent-private-review"]
        assert main(args) == 2
        assert calls == []
        assert main(args + ["--consent-compare-previous-review"]) == 0
        assert len(calls) == 1
    printed = capsys.readouterr().out
    assert '"changed": 1' in printed
    for secret in (CASE_A, CASE_B, CASE_C, TOKEN, "sandbox"):
        assert secret not in printed
