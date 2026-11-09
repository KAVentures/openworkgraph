"""Focused refusal and successful operator readiness tests."""
import hashlib
import json
from unittest.mock import patch
import pytest
from server.m365_outlook_sync_readiness import readiness
from server.m365_outlook_owg_receipt import ReceiptError


def test_readiness_requires_consent():
    with pytest.raises(ReceiptError):
        readiness(tenant="t", mailbox="m", folder="f", expected_address="m@example.com",
                  grant_file="unused", identity_report="unused", folder_report="unused",
                  ledger_dir="unused", log_id="log")


def test_readiness_matching_private_evidence():
    scope = "a" * 64
    identity = {"format": "openworkgraph.m365-mailbox-identity.v1",
                "status": "mailbox_identity_observed", "scope_sha256": scope,
                "expected_address_sha256": hashlib.sha256(b"m@example.com").hexdigest(),
                "mailbox_identifier_matched": True, "upn_matched": True,
                "checkpoint_advanced": False}
    folder = {"format": "openworkgraph.m365-folder-preflight.v1",
              "status": "folder_metadata_observed", "scope_sha256": scope,
              "folder_id_sha256": hashlib.sha256(b"folder").hexdigest(),
              "folder_id_matched": True, "checkpoint_advanced": False}
    with (patch("server.m365_outlook_sync_readiness._scope", return_value=scope),
          patch("server.m365_outlook_sync_readiness._grant"),
          patch("server.m365_outlook_sync_readiness._private_read",
                side_effect=[json.dumps(identity).encode(), json.dumps(folder).encode()]),
          patch("server.m365_outlook_sync_readiness.inspect_checkpoint",
                return_value={"log_id": "log", "generation": 2})):
        result = readiness(tenant="t", mailbox="m", folder="folder",
                           expected_address="m@example.com", grant_file="grant",
                           identity_report="identity", folder_report="folder-report",
                           ledger_dir="ledger", log_id="log", consent_local_read=True)
    assert result["status"] == "operator_review_ready"
    assert result["provider_requests"] == 0
    assert result["checkpoint_advanced"] is False


def test_readiness_rejects_untrusted_preflight():
    with (patch("server.m365_outlook_sync_readiness._scope", return_value="a" * 64),
          patch("server.m365_outlook_sync_readiness._grant"),
          patch("server.m365_outlook_sync_readiness._private_read",
                side_effect=[b'{"format":"wrong"}', b'{}'])):
        with pytest.raises(ReceiptError):
            readiness(tenant="t", mailbox="m", folder="f",
                      expected_address="m@example.com", grant_file="grant",
                      identity_report="identity", folder_report="folder-report",
                      ledger_dir="ledger", log_id="log", consent_local_read=True)


@pytest.mark.parametrize("generation", [1, 3, 0, -1, True, "2"])
def test_readiness_refuses_stale_or_invalid_checkpoint_generation(generation):
    scope = "a" * 64
    identity = {"format": "openworkgraph.m365-mailbox-identity.v1",
                "status": "mailbox_identity_observed", "scope_sha256": scope,
                "expected_address_sha256": hashlib.sha256(b"m@example.com").hexdigest(),
                "mailbox_identifier_matched": True, "upn_matched": True,
                "checkpoint_advanced": False}
    folder = {"format": "openworkgraph.m365-folder-preflight.v1",
              "status": "folder_metadata_observed", "scope_sha256": scope,
              "folder_id_sha256": hashlib.sha256(b"folder").hexdigest(),
              "folder_id_matched": True, "checkpoint_advanced": False}
    with (patch("server.m365_outlook_sync_readiness._scope", return_value=scope),
          patch("server.m365_outlook_sync_readiness._grant"),
          patch("server.m365_outlook_sync_readiness._private_read",
                side_effect=[json.dumps(identity).encode(), json.dumps(folder).encode()]),
          patch("server.m365_outlook_sync_readiness.inspect_checkpoint",
                return_value={"log_id": "log", "generation": 2})):
        with pytest.raises(ReceiptError, match="generation changed"):
            readiness(tenant="t", mailbox="m", folder="folder",
                      expected_address="m@example.com", grant_file="grant",
                      identity_report="identity", folder_report="folder-report",
                      ledger_dir="ledger", log_id="log", consent_local_read=True,
                      expected_generation=generation)


def test_readiness_accepts_exact_committed_generation():
    scope = "a" * 64
    identity = {"format": "openworkgraph.m365-mailbox-identity.v1",
                "status": "mailbox_identity_observed", "scope_sha256": scope,
                "expected_address_sha256": hashlib.sha256(b"m@example.com").hexdigest(),
                "mailbox_identifier_matched": True, "mail_matched": True,
                "checkpoint_advanced": False}
    folder = {"format": "openworkgraph.m365-folder-preflight.v1",
              "status": "folder_metadata_observed", "scope_sha256": scope,
              "folder_id_sha256": hashlib.sha256(b"folder").hexdigest(),
              "folder_id_matched": True, "checkpoint_advanced": False}
    with (patch("server.m365_outlook_sync_readiness._scope", return_value=scope),
          patch("server.m365_outlook_sync_readiness._grant"),
          patch("server.m365_outlook_sync_readiness._private_read",
                side_effect=[json.dumps(identity).encode(), json.dumps(folder).encode()]),
          patch("server.m365_outlook_sync_readiness.inspect_checkpoint",
                return_value={"log_id": "log", "generation": 2})):
        result = readiness(tenant="t", mailbox="m", folder="folder",
                           expected_address="m@example.com", grant_file="grant",
                           identity_report="identity", folder_report="folder-report",
                           ledger_dir="ledger", log_id="log", consent_local_read=True,
                           expected_generation=2)
    assert result["committed_generation"] == 2


def _extract_kwargs():
    return dict(expected_generation=2, tenant="t", mailbox="m", folder="f",
                expected_address="m@example.com", grant_file="grant",
                identity_report="identity", folder_report="folder-report",
                ledger_dir="ledger", log_id="log", confirm_log_id="log",
                previous_candidate="previous.json", previous_csv="previous.csv",
                receipt_file="receipt", journal_dir="journal",
                handoff_dir="handoff", successor_label="next",
                output="output", candidate="candidate", token="token",
                consent_local_read=True, consent_delta_read=True,
                consent_local_export=True, confirm_removals_reviewed=True)


@pytest.mark.parametrize("change", [
    {"expected_generation": None}, {"expected_generation": True},
    {"expected_generation": 0}, {"consent_local_read": False},
    {"consent_delta_read": False}, {"consent_local_export": False},
    {"confirm_removals_reviewed": False}, {"confirm_log_id": "other"},
])
def test_extract_after_readiness_refuses_before_any_delegate(change):
    from server.m365_outlook_sync_readiness import extract_after_readiness
    args = {**_extract_kwargs(), **change}
    with (patch("server.m365_outlook_sync_readiness.readiness") as ready,
          patch("server.m365_outlook_checkpoint_next.extract_from_checkpoint") as extract):
        with pytest.raises(ReceiptError):
            extract_after_readiness(**args)
        ready.assert_not_called()
        extract.assert_not_called()


def test_extract_after_readiness_refuses_mismatched_preflight():
    from server.m365_outlook_sync_readiness import extract_after_readiness
    with (patch("server.m365_outlook_sync_readiness.readiness",
                side_effect=ReceiptError("Preflight evidence mismatch")),
          patch("server.m365_outlook_checkpoint_next.extract_from_checkpoint") as extract):
        with pytest.raises(ReceiptError, match="Preflight evidence mismatch"):
            extract_after_readiness(**_extract_kwargs())
        extract.assert_not_called()


def test_extract_after_readiness_requires_reviewable_delegate_result():
    from server.m365_outlook_sync_readiness import extract_after_readiness
    with (patch("server.m365_outlook_sync_readiness.readiness",
                return_value={"status": "operator_review_ready"}),
          patch("server.m365_outlook_checkpoint_next.extract_from_checkpoint",
                return_value={"status": "refused"}) as extract):
        with pytest.raises(ReceiptError, match="not ready for review"):
            extract_after_readiness(**_extract_kwargs())
        assert extract.call_args.kwargs["consent_delta_read"] is True
        assert extract.call_args.kwargs["consent_local_export"] is True


def test_extract_after_readiness_preserves_manual_review_boundary():
    from server.m365_outlook_sync_readiness import extract_after_readiness
    with (patch("server.m365_outlook_sync_readiness.readiness",
                return_value={"status": "operator_review_ready"}) as ready,
          patch("server.m365_outlook_checkpoint_next.extract_from_checkpoint",
                return_value={"status": "review_required",
                              "committed_predecessor_generation": 2,
                              "event_snapshots": 3,
                              "removal_markers_for_review": 1,
                              "checkpoint_advanced": False,
                              "owg_append_performed": False}) as extract,
          patch("server.m365_outlook_sync_readiness.inspect_checkpoint",
                return_value={"generation": 2, "log_id": "log"}) as inspect,
          patch("server.m365_outlook_sync_readiness._grant") as grant):
        result = extract_after_readiness(**_extract_kwargs())
    assert ready.call_args.kwargs["expected_generation"] == 2
    assert extract.call_args.kwargs["log_id"] == "log"
    assert inspect.call_args.kwargs["consent_local_read"] is True
    assert grant.called
    assert result["status"] == "review_required"
    assert result["checkpoint_advanced"] is False
    assert result["owg_append_performed"] is False
    assert result["automatic_retry_allowed"] is False


@pytest.mark.parametrize("generation", [None, 1, 3, True, "2"])
def test_extract_after_readiness_rejects_changed_delegate_generation(generation):
    """A concurrent checkpoint advance must never be labelled approved."""
    from server.m365_outlook_sync_readiness import extract_after_readiness
    with (patch("server.m365_outlook_sync_readiness.readiness",
                return_value={"status": "operator_review_ready"}),
          patch("server.m365_outlook_checkpoint_next.extract_from_checkpoint",
                return_value={"status": "review_required",
                              "committed_predecessor_generation": generation,
                              "event_snapshots": 3,
                              "removal_markers_for_review": 0})):
        with pytest.raises(ReceiptError, match="generation changed"):
            extract_after_readiness(**_extract_kwargs())


@pytest.mark.parametrize("bad", [
    {"event_snapshots": None},
    {"event_snapshots": True},
    {"event_snapshots": -1},
    {"removal_markers_for_review": None},
    {"removal_markers_for_review": -1},
    {"removal_markers_for_review": "1"},
    {"checkpoint_advanced": True},
    {"checkpoint_advanced": None},
    {"owg_append_performed": True},
    {"owg_append_performed": None},
])
def test_extract_after_readiness_refuses_unsafe_review_receipt(bad):
    """A malformed delegate receipt must never be presented as approved."""
    from server.m365_outlook_sync_readiness import extract_after_readiness
    result = {"status": "review_required",
              "committed_predecessor_generation": 2,
              "event_snapshots": 3, "removal_markers_for_review": 0,
              "checkpoint_advanced": False, "owg_append_performed": False}
    result.update(bad)
    with (patch("server.m365_outlook_sync_readiness.readiness",
                return_value={"status": "operator_review_ready"}),
          patch("server.m365_outlook_checkpoint_next.extract_from_checkpoint",
                return_value=result)):
        with pytest.raises(ReceiptError, match="Unsafe or incomplete"):
            extract_after_readiness(**_extract_kwargs())


def _cli_extract_args():
    return [
        "--tenant", "t", "--mailbox", "m", "--folder", "f",
        "--expected-address", "m@example.com", "--log-id", "log",
        "--grant-file", "grant", "--identity-report", "identity",
        "--folder-report", "folder", "--ledger-dir", "ledger",
        "--expected-generation", "2", "--extract-after-readiness",
        "--confirm-log-id", "log", "--previous-candidate", "prior.json",
        "--previous-csv", "prior.csv", "--receipt-file", "receipt.json",
        "--journal-dir", "journal", "--handoff-dir", "handoff",
        "--successor-label", "next", "--output", "next.csv",
        "--candidate", "next.json", "--consent-local-read",
        "--consent-delta-read", "--consent-local-export",
        "--confirm-removals-reviewed",
    ]


def test_operator_cli_routes_reviewed_extraction_without_secret_arguments(monkeypatch, capsys):
    from server.m365_outlook_sync_readiness import main
    monkeypatch.setenv("OWG_M365_GRAPH_TOKEN", "secret-bearer")
    monkeypatch.setenv("OWG_DASHBOARD_SESSION", "secret-session")
    with patch("server.m365_outlook_sync_readiness.extract_after_readiness",
               return_value={"status": "review_required",
                             "checkpoint_advanced": False}) as extract:
        assert main(_cli_extract_args()) == 0
    assert extract.call_args.kwargs["expected_generation"] == 2
    assert extract.call_args.kwargs["token"] == "secret-bearer"
    assert extract.call_args.kwargs["session"] == "secret-session"
    assert extract.call_args.kwargs["confirm_removals_reviewed"] is True
    output = capsys.readouterr().out
    assert '"review_required"' in output
    assert "secret-" not in output


@pytest.mark.parametrize("change", [
    lambda x: x.remove("--consent-delta-read"),
    lambda x: x.remove("--confirm-removals-reviewed"),
    lambda x: x.remove("--consent-local-read"),
    lambda x: x.__setitem__(x.index("--expected-generation") + 1, "0"),
    lambda x: x.__setitem__(x.index("--confirm-log-id") + 1, "other"),
])
def test_operator_cli_refuses_missing_approval_before_delegate(monkeypatch, capsys, change):
    from server.m365_outlook_sync_readiness import main
    monkeypatch.setenv("OWG_M365_GRAPH_TOKEN", "secret-bearer")
    monkeypatch.setenv("OWG_DASHBOARD_SESSION", "secret-session")
    args = _cli_extract_args()
    change(args)
    with patch("server.m365_outlook_sync_readiness.extract_after_readiness") as extract:
        assert main(args) == 2
        extract.assert_not_called()
    assert '"refused"' in capsys.readouterr().out


@pytest.mark.parametrize("missing", ["OWG_M365_GRAPH_TOKEN", "OWG_DASHBOARD_SESSION"])
def test_operator_cli_requires_private_credentials(monkeypatch, capsys, missing):
    from server.m365_outlook_sync_readiness import main
    monkeypatch.setenv("OWG_M365_GRAPH_TOKEN", "secret-bearer")
    monkeypatch.setenv("OWG_DASHBOARD_SESSION", "secret-session")
    monkeypatch.delenv(missing)
    with patch("server.m365_outlook_sync_readiness.extract_after_readiness") as extract:
        assert main(_cli_extract_args()) == 2
        extract.assert_not_called()
    assert "secret-" not in capsys.readouterr().out


def test_operator_cli_rejects_extraction_options_in_inspection_mode(capsys):
    from server.m365_outlook_sync_readiness import main
    args = _cli_extract_args()
    args.remove("--extract-after-readiness")
    with patch("server.m365_outlook_sync_readiness.readiness") as ready:
        assert main(args) == 2
        ready.assert_not_called()
    assert '"refused"' in capsys.readouterr().out


@pytest.mark.parametrize("latest", [
    None, {"generation": 3, "log_id": "log"},
    {"generation": 2, "log_id": "other"},
    {"generation": True, "log_id": "log"},
    {"generation": "2", "log_id": "log"},
])
def test_extract_after_readiness_refuses_concurrent_checkpoint_change(latest):
    """Never present a candidate as review-ready after a competing commit."""
    from server.m365_outlook_sync_readiness import extract_after_readiness
    with (patch("server.m365_outlook_sync_readiness.readiness",
                return_value={"status": "operator_review_ready"}),
          patch("server.m365_outlook_checkpoint_next.extract_from_checkpoint",
                return_value={"status": "review_required",
                              "committed_predecessor_generation": 2,
                              "event_snapshots": 1,
                              "removal_markers_for_review": 0,
                              "checkpoint_advanced": False,
                              "owg_append_performed": False}),
          patch("server.m365_outlook_sync_readiness.inspect_checkpoint",
                return_value=latest),
          patch("server.m365_outlook_sync_readiness._grant")):
        with pytest.raises(ReceiptError, match="Checkpoint advanced"):
            extract_after_readiness(**_extract_kwargs())


def test_extract_after_readiness_refuses_revoked_grant_after_graph():
    from server.m365_outlook_sync_readiness import extract_after_readiness
    with (patch("server.m365_outlook_sync_readiness.readiness",
                return_value={"status": "operator_review_ready"}),
          patch("server.m365_outlook_checkpoint_next.extract_from_checkpoint",
                return_value={"status": "review_required",
                              "committed_predecessor_generation": 2,
                              "event_snapshots": 1,
                              "removal_markers_for_review": 0,
                              "checkpoint_advanced": False,
                              "owg_append_performed": False}),
          patch("server.m365_outlook_sync_readiness.inspect_checkpoint",
                return_value={"generation": 2, "log_id": "log"}),
          patch("server.m365_outlook_sync_readiness._grant",
                side_effect=ReceiptError("Grant revoked"))):
        with pytest.raises(ReceiptError, match="Grant revoked"):
            extract_after_readiness(**_extract_kwargs())


@pytest.mark.parametrize("malformed", [None, False, [], "review_required", 42])
def test_extract_after_readiness_refuses_non_mapping_provider_result(malformed):
    """Malformed adapter output must fail closed as an operator refusal, not crash."""
    from server.m365_outlook_sync_readiness import extract_after_readiness
    with (patch("server.m365_outlook_sync_readiness.readiness",
                return_value={"status": "operator_review_ready"}),
          patch("server.m365_outlook_checkpoint_next.extract_from_checkpoint",
                return_value=malformed)):
        with pytest.raises(ReceiptError, match="not ready for review"):
            extract_after_readiness(**_extract_kwargs())


def test_commit_after_review_requires_explicit_append_and_commit():
    from server.m365_outlook_sync_readiness import commit_after_review
    with patch("server.m365_outlook_sync_readiness.readiness") as preflight:
        with pytest.raises(ReceiptError):
            commit_after_review(
                expected_generation=2, expected_previous_candidate_sha256="a"*64,
                tenant="t", mailbox="m", folder="f", expected_address="m@example.com",
                grant_file="g", identity_report="i", folder_report="f",
                ledger_dir="l", log_id="log", confirm_log_id="log",
                candidate="c", csv_path="csv", receipt_file="r",
                journal_dir="j", handoff_dir="h", successor_label="next",
                session="session", consent_local_read=True,
                confirm_removals_reviewed=True, confirm_checkpoint_commit=True)
    preflight.assert_not_called()


def test_commit_after_review_delegates_membership_and_predecessor_verification():
    from server.m365_outlook_sync_readiness import commit_after_review
    with (patch("server.m365_outlook_sync_readiness.readiness") as ready,
          patch("server.m365_outlook_checkpoint.commit_checkpoint",
                return_value={"status": "checkpoint_committed",
                              "generation": 3, "idempotent_replay": False}) as commit):
        result = commit_after_review(
            expected_generation=2, expected_previous_candidate_sha256="a"*64,
            tenant="t", mailbox="m", folder="f", expected_address="m@example.com",
            grant_file="g", identity_report="i", folder_report="f",
            ledger_dir="l", log_id="log", confirm_log_id="log",
            candidate="c", csv_path="csv", receipt_file="r",
            journal_dir="j", handoff_dir="h", successor_label="next",
            session="session", consent_local_read=True,
            confirm_removals_reviewed=True, confirm_events_appended=True,
            confirm_checkpoint_commit=True)
    assert result["committed_generation"] == 3
    assert result["event_membership_verified"] is True
    assert ready.call_args.kwargs["expected_generation"] == 2
    assert commit.call_args.kwargs["expected_previous_candidate_sha256"] == "a"*64
    assert commit.call_args.kwargs["confirm_checkpoint_commit"] is True


@pytest.mark.parametrize("response", [
    {"status": "checkpoint_committed", "generation": 2, "idempotent_replay": False},
    {"status": "checkpoint_committed", "generation": 3, "idempotent_replay": True},
    {"status": "refused", "generation": 3, "idempotent_replay": False},
])
def test_commit_after_review_refuses_ambiguous_commit(response):
    from server.m365_outlook_sync_readiness import commit_after_review
    with (patch("server.m365_outlook_sync_readiness.readiness"),
          patch("server.m365_outlook_checkpoint.commit_checkpoint", return_value=response)):
        with pytest.raises(ReceiptError):
            commit_after_review(
                expected_generation=2, expected_previous_candidate_sha256="a"*64,
                tenant="t", mailbox="m", folder="f", expected_address="m@example.com",
                grant_file="g", identity_report="i", folder_report="f",
                ledger_dir="l", log_id="log", confirm_log_id="log",
                candidate="c", csv_path="csv", receipt_file="r",
                journal_dir="j", handoff_dir="h", successor_label="next",
                session="session", consent_local_read=True,
                confirm_removals_reviewed=True, confirm_events_appended=True,
                confirm_checkpoint_commit=True)


def _cli_commit_args():
    args = _cli_extract_args()
    args.remove("--extract-after-readiness")
    args.remove("--consent-delta-read")
    args.remove("--consent-local-export")
    for key in ("--previous-candidate", "--previous-csv", "--output"):
        pos = args.index(key)
        del args[pos:pos + 2]
    args.extend(["--commit-after-review", "--csv-path", "next.csv",
                 "--expected-previous-candidate-sha256", "a" * 64,
                 "--confirm-events-appended", "--confirm-checkpoint-commit"])
    return args


def test_cli_commit_requires_separate_approval_and_dashboard_session(monkeypatch, capsys):
    from server.m365_outlook_sync_readiness import main
    monkeypatch.setenv("OWG_DASHBOARD_SESSION", "private-session")
    with patch("server.m365_outlook_sync_readiness.commit_after_review",
               return_value={"status": "checkpoint_committed", "committed_generation": 3}) as commit:
        assert main(_cli_commit_args()) == 0
        assert commit.call_args.kwargs["session"] == "private-session"
        assert commit.call_args.kwargs["confirm_events_appended"] is True
        assert commit.call_args.kwargs["expected_previous_candidate_sha256"] == "a" * 64
    assert "private-session" not in capsys.readouterr().out


@pytest.mark.parametrize("missing", ["--confirm-events-appended", "--confirm-checkpoint-commit",
                                      "--confirm-removals-reviewed", "--consent-local-read"])
def test_cli_commit_refuses_missing_approval_before_checkpoint(monkeypatch, missing):
    from server.m365_outlook_sync_readiness import main
    monkeypatch.setenv("OWG_DASHBOARD_SESSION", "private-session")
    args = _cli_commit_args()
    args.remove(missing)
    with patch("server.m365_outlook_sync_readiness.commit_after_review") as commit:
        assert main(args) == 2
        commit.assert_not_called()


def test_cli_commit_refuses_combined_graph_extraction(monkeypatch):
    from server.m365_outlook_sync_readiness import main
    monkeypatch.setenv("OWG_DASHBOARD_SESSION", "private-session")
    with (patch("server.m365_outlook_sync_readiness.commit_after_review") as commit,
          patch("server.m365_outlook_sync_readiness.extract_after_readiness") as extract):
        assert main(_cli_commit_args() + ["--extract-after-readiness"]) == 2
        commit.assert_not_called()
        extract.assert_not_called()


def test_append_after_review_requires_separate_consent():
    from server.m365_outlook_sync_readiness import append_after_review, ReceiptError as GateError
    with pytest.raises(GateError):
        append_after_review(expected_generation=2, tenant="t", mailbox="m", folder="f",
            expected_address="m", grant_file="g", identity_report="i",
            folder_report="f", ledger_dir="l", log_id="log", confirm_log_id="log",
            candidate="c", csv_path="csv", session="session",
            consent_local_read=True, confirm_removals_reviewed=True)


def test_append_after_review_verifies_checkpoint_without_advancing():
    from server.m365_outlook_sync_readiness import append_after_review
    with (patch("server.m365_outlook_sync_readiness.readiness",
                return_value={"status": "operator_review_ready"}),
          patch("server.m365_outlook_owg_receipt.append_verified",
                return_value={"status": "verified", "checkpoint_advanced": False}) as append,
          patch("server.m365_outlook_sync_readiness.inspect_checkpoint",
                return_value={"log_id": "log", "generation": 2}),
          patch("server.m365_outlook_sync_readiness._grant")):
        result = append_after_review(expected_generation=2, tenant="t", mailbox="m",
            folder="f", expected_address="m", grant_file="g", identity_report="i",
            folder_report="f", ledger_dir="l", log_id="log", confirm_log_id="log",
            candidate="c", csv_path="csv", session="session", consent_local_read=True,
            consent_local_append=True, confirm_removals_reviewed=True)
    assert result["status"] == "append_review_verified"
    assert result["checkpoint_advanced"] is False
    assert result["automatic_retry_allowed"] is False
    append.assert_called_once()


def test_append_after_review_rejects_concurrent_checkpoint_advance():
    from server.m365_outlook_sync_readiness import append_after_review
    from server.m365_outlook_owg_receipt import UncertainReceipt
    with (patch("server.m365_outlook_sync_readiness.readiness",
                return_value={"status": "operator_review_ready"}),
          patch("server.m365_outlook_owg_receipt.append_verified",
                return_value={"status": "verified", "checkpoint_advanced": False}),
          patch("server.m365_outlook_sync_readiness.inspect_checkpoint",
                return_value={"log_id": "log", "generation": 3}),
          patch("server.m365_outlook_sync_readiness._grant")):
        with pytest.raises(UncertainReceipt, match="may have committed"):
            append_after_review(expected_generation=2, tenant="t", mailbox="m",
                folder="f", expected_address="m", grant_file="g", identity_report="i",
                folder_report="f", ledger_dir="l", log_id="log", confirm_log_id="log",
                candidate="c", csv_path="csv", session="session", consent_local_read=True,
                consent_local_append=True, confirm_removals_reviewed=True)


@pytest.mark.parametrize("post_append_failure", ["revoked", "readback", "malformed"])
def test_append_after_review_preserves_possible_write_on_post_append_failure(post_append_failure):
    from server.m365_outlook_sync_readiness import append_after_review
    from server.m365_outlook_owg_receipt import UncertainReceipt
    latest = {"log_id": "log", "generation": 2}
    receipt = {"status": "verified", "checkpoint_advanced": False}
    if post_append_failure == "readback":
        latest = None
    if post_append_failure == "malformed":
        receipt = {"status": "refused", "checkpoint_advanced": False}
    grant = patch("server.m365_outlook_sync_readiness._grant")
    with (patch("server.m365_outlook_sync_readiness.readiness",
                return_value={"status": "operator_review_ready"}),
          patch("server.m365_outlook_owg_receipt.append_verified",
                return_value=receipt) as append,
          patch("server.m365_outlook_sync_readiness.inspect_checkpoint",
                return_value=latest),
          grant as grant_check):
        if post_append_failure == "revoked":
            grant_check.side_effect = ReceiptError("Grant revoked")
        with pytest.raises(UncertainReceipt, match="may have committed"):
            append_after_review(
                expected_generation=2, tenant="t", mailbox="m", folder="f",
                expected_address="m", grant_file="g", identity_report="i",
                folder_report="f", ledger_dir="l", log_id="log",
                confirm_log_id="log", candidate="c", csv_path="csv",
                session="session", consent_local_read=True,
                consent_local_append=True, confirm_removals_reviewed=True)
        append.assert_called_once()


@pytest.mark.parametrize("writer_outcome", [
    OSError("ledger write response lost"),
    {"status": "refused", "generation": 3, "idempotent_replay": False},
    {"status": "checkpoint_committed", "generation": 3, "idempotent_replay": True},
])
def test_commit_after_review_reports_possible_commit_after_writer_invocation(writer_outcome):
    """A lost reply must not be reported as a safe-to-retry refusal."""
    from server.m365_outlook_sync_readiness import commit_after_review
    from server.m365_outlook_owg_receipt import UncertainReceipt
    kwargs = dict(
        expected_generation=2, expected_previous_candidate_sha256="a"*64,
        tenant="t", mailbox="m", folder="f", expected_address="m@example.com",
        grant_file="g", identity_report="i", folder_report="f",
        ledger_dir="l", log_id="log", confirm_log_id="log",
        candidate="c", csv_path="csv", receipt_file="r",
        journal_dir="j", handoff_dir="h", successor_label="next",
        session="session", consent_local_read=True,
        confirm_removals_reviewed=True, confirm_events_appended=True,
        confirm_checkpoint_commit=True)
    with (patch("server.m365_outlook_sync_readiness.readiness"),
          patch("server.m365_outlook_checkpoint.commit_checkpoint",
                side_effect=writer_outcome if isinstance(writer_outcome, Exception) else None,
                return_value=None if isinstance(writer_outcome, Exception) else writer_outcome)):
        with pytest.raises(UncertainReceipt, match="may have committed"):
            commit_after_review(**kwargs)


def test_operator_two_round_review_append_commit_incremental_contract():
    """Exercise the real operator composition across two committed generations.

    Provider, OWG and durable-ledger boundaries are faked; this is not a live
    tenant test. Each delegate still receives the exact reviewed generation.
    """
    from server.m365_outlook_sync_readiness import (
        extract_after_readiness, append_after_review, commit_after_review)
    from server.m365_outlook_owg_receipt import UncertainReceipt

    generation = [2]
    actions = []

    def ready(**kw):
        assert kw["expected_generation"] == generation[0]
        actions.append(("preflight", generation[0]))
        return {"status": "operator_review_ready"}

    def extract(**kw):
        actions.append(("graph", generation[0]))
        return {"status": "review_required",
                "committed_predecessor_generation": generation[0],
                "event_snapshots": 1, "removal_markers_for_review": 0,
                "checkpoint_advanced": False, "owg_append_performed": False}

    def append(**kw):
        actions.append(("owg_append_verified", generation[0]))
        assert kw["consent_local_append"] is True
        return {"status": "verified", "checkpoint_advanced": False}

    def commit(**kw):
        actions.append(("membership_and_commit", generation[0]))
        assert kw["confirm_checkpoint_commit"] is True
        generation[0] += 1
        return {"status": "checkpoint_committed", "generation": generation[0],
                "idempotent_replay": False}

    def checkpoint(**kw):
        return {"generation": generation[0], "log_id": "log"}

    append_kw = dict(tenant="t", mailbox="m", folder="f",
                     expected_address="m@example.com", grant_file="g",
                     identity_report="i", folder_report="f", ledger_dir="l",
                     log_id="log", confirm_log_id="log", candidate="c",
                     csv_path="csv", session="session", consent_local_read=True,
                     consent_local_append=True, confirm_removals_reviewed=True)
    commit_kw = dict(append_kw)
    commit_kw.pop("consent_local_append")
    commit_kw.update(expected_previous_candidate_sha256="a"*64,
                     receipt_file="r", journal_dir="j", handoff_dir="h",
                     successor_label="next", confirm_events_appended=True,
                     confirm_checkpoint_commit=True)
    extraction_kw = _extract_kwargs()
    extraction_kw["expected_generation"] = 2
    with (patch("server.m365_outlook_sync_readiness.readiness", side_effect=ready),
          patch("server.m365_outlook_checkpoint_next.extract_from_checkpoint",
                side_effect=extract),
          patch("server.m365_outlook_owg_receipt.append_verified",
                side_effect=append),
          patch("server.m365_outlook_checkpoint.commit_checkpoint",
                side_effect=commit),
          patch("server.m365_outlook_sync_readiness.inspect_checkpoint",
                side_effect=checkpoint),
          patch("server.m365_outlook_sync_readiness._grant")):
        for round_generation in (2, 3):
            extraction_kw["expected_generation"] = round_generation
            assert extract_after_readiness(**extraction_kw)["status"] == "review_required"
            assert append_after_review(expected_generation=round_generation,
                                       **append_kw)["status"] == "append_review_verified"
            assert commit_after_review(expected_generation=round_generation,
                                       **commit_kw)["committed_generation"] == round_generation + 1
    assert actions == [
        ("preflight", 2), ("graph", 2), ("preflight", 2),
        ("owg_append_verified", 2), ("preflight", 2),
        ("membership_and_commit", 2),
        ("preflight", 3), ("graph", 3), ("preflight", 3),
        ("owg_append_verified", 3), ("preflight", 3),
        ("membership_and_commit", 3),
    ]


@pytest.mark.parametrize("action,expected_key,recovery", [
    ("commit", "possible_checkpoint_commit", "inspect_durable_ledger"),
    ("append", "possible_local_append", "inspect_owg_inventory_and_checkpoint"),
])
def test_operator_uncertain_write_identifies_correct_reconciliation(monkeypatch, capsys,
                                                                    action, expected_key, recovery):
    """Do not mislabel an uncertain checkpoint write as an OWG append."""
    from server.m365_outlook_sync_readiness import main
    from server.m365_outlook_owg_receipt import UncertainReceipt
    monkeypatch.setenv("OWG_DASHBOARD_SESSION", "private-session")
    if action == "commit":
        args = _cli_commit_args()
        target = "server.m365_outlook_sync_readiness.commit_after_review"
    else:
        args = _cli_commit_args()
        args.remove("--commit-after-review")
        args.remove("--confirm-events-appended")
        args.remove("--confirm-checkpoint-commit")
        pos = args.index("--expected-previous-candidate-sha256")
        del args[pos:pos + 2]
        for key in ("--receipt-file", "--journal-dir", "--handoff-dir",
                    "--successor-label"):
            pos = args.index(key)
            del args[pos:pos + 2]
        args.extend(["--append-after-review", "--consent-local-append"])
        target = "server.m365_outlook_sync_readiness.append_after_review"
    with patch(target, side_effect=UncertainReceipt("private provider response")):
        assert main(args) == 3
    output = capsys.readouterr().out
    receipt = json.loads(output)
    assert receipt["status"] == "unverified"
    assert receipt[expected_key] is True
    assert receipt["recovery_action"] == recovery
    assert receipt["automatic_retry_allowed"] is False
    assert "private" not in output
    assert ("possible_checkpoint_commit" in receipt) is (action == "commit")
    assert ("possible_local_append" in receipt) is (action == "append")


def test_private_operator_audit_is_immutable_and_redacted(tmp_path):
    import os
    from server.m365_outlook_sync_readiness import _audit_operator_result, ReceiptError
    private = tmp_path / "audit"
    private.mkdir(mode=0o700)
    if os.name != "posix":
        with pytest.raises(ReceiptError, match="unsupported"):
            _audit_operator_result(private, action="readiness",
                tenant="tenant-secret", mailbox="private-mailbox",
                folder="folderSecret", log_id="log-secret", generation=2,
                result={"status": "operator_review_ready"})
        assert not list(private.iterdir())
        return
    first = _audit_operator_result(
        private, action="extract", tenant="tenant-secret",
        mailbox="private-mailbox", folder="folderSecret",
        log_id="log-secret", generation=2,
        result={"status": "review_required", "event_snapshots": 3,
                "removal_markers_for_review": 1, "checkpoint_advanced": False,
                "provider_token": "never-write-token"})
    second = _audit_operator_result(
        private, action="commit", tenant="tenant-secret",
        mailbox="private-mailbox", folder="folderSecret",
        log_id="log-secret", generation=2,
        result={"status": "checkpoint_committed", "committed_generation": 3})
    assert first != second
    assert len(list(private.iterdir())) == 2
    data = (private / first).read_text()
    record = json.loads(data)
    assert record["event_snapshots"] == 3
    assert record["removal_markers_for_review"] == 1
    assert record["expected_generation"] == 2
    assert record["automatic_retry_allowed"] is False
    for secret in ("private@example.com", "tenant-secret", "folderSecret",
                   "log-secret", "never-write-token"):
        assert secret not in data
    assert os.stat(private / first).st_mode & 0o777 == 0o600


def test_operator_audit_rejects_public_directory_and_symlink(tmp_path):
    from server.m365_outlook_sync_readiness import _audit_operator_result, ReceiptError
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    public = tmp_path / "public"
    public.mkdir(mode=0o755)
    link = tmp_path / "link"
    link.symlink_to(private, target_is_directory=True)
    for directory in (public, link):
        with pytest.raises(ReceiptError):
            _audit_operator_result(
                directory, action="readiness", tenant="t", mailbox="m",
                folder="f", log_id="log", generation=2,
                result={"status": "operator_review_ready"})
    assert not list(private.iterdir())
    assert not list(public.iterdir())


def test_operator_audit_missing_directory_refuses_before_append(monkeypatch, capsys, tmp_path):
    """An unavailable audit directory must not produce an uncertain write.

    Preflight now runs before the append delegate. This is a definite
    refusal, unlike an audit persistence failure after a successful POST.
    """
    from server.m365_outlook_sync_readiness import main
    monkeypatch.setenv("OWG_DASHBOARD_SESSION", "private-session")
    args = _cli_commit_args()
    args.remove("--commit-after-review")
    args.remove("--confirm-events-appended")
    args.remove("--confirm-checkpoint-commit")
    pos = args.index("--expected-previous-candidate-sha256")
    del args[pos:pos + 2]
    for key in ("--receipt-file", "--journal-dir", "--handoff-dir",
                "--successor-label"):
        pos = args.index(key)
        del args[pos:pos + 2]
    args.extend(["--append-after-review", "--consent-local-append",
                 "--audit-dir", str(tmp_path / "missing")])
    with patch("server.m365_outlook_sync_readiness.append_after_review") as append:
        assert main(args) == 2
        append.assert_not_called()
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "refused"
    assert receipt["checkpoint_advanced"] is False
    assert receipt["automatic_retry_allowed"] is False
    assert "possible_local_append" not in receipt


@pytest.mark.skipif(__import__("os").name != "posix", reason="POSIX dirfd security contract")
def test_operator_audit_rejects_directory_replacement_between_checks(tmp_path, monkeypatch):
    """Replacing a checked private path must not redirect a receipt to an unsafe directory."""
    import os
    from server import m365_outlook_sync_readiness as module
    private = tmp_path / "audit"
    private.mkdir(mode=0o700)
    public = tmp_path / "public"
    public.mkdir(mode=0o755)
    original_open = module.os.open
    swapped = []

    def replace_on_open(path, flags, *args, **kwargs):
        if not swapped and os.fspath(path) == os.fspath(private):
            swapped.append(True)
            private.rename(tmp_path / "old-audit")
            private.symlink_to(public, target_is_directory=True)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(module.os, "open", replace_on_open)
    with pytest.raises((OSError, module.ReceiptError)):
        module._audit_operator_result(
            private, action="readiness", tenant="t", mailbox="m",
            folder="f", log_id="log", generation=2,
            result={"status": "operator_review_ready"})
    assert swapped
    assert not list(public.iterdir())
    assert not list((tmp_path / "old-audit").iterdir())


@pytest.mark.skipif(__import__("os").name != "posix", reason="POSIX dirfd security contract")
def test_operator_audit_revalidates_opened_directory_permissions(tmp_path, monkeypatch):
    """A directory made public after lstat must fail before creating a receipt."""
    import os
    from server import m365_outlook_sync_readiness as module
    private = tmp_path / "audit"
    private.mkdir(mode=0o700)
    original_open = module.os.open
    swapped = []

    def change_permissions_on_open(path, flags, *args, **kwargs):
        if not swapped and os.fspath(path) == os.fspath(private):
            swapped.append(True)
            private.chmod(0o755)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(module.os, "open", change_permissions_on_open)
    with pytest.raises(module.ReceiptError, match="remain private"):
        module._audit_operator_result(
            private, action="readiness", tenant="t", mailbox="m",
            folder="f", log_id="log", generation=2,
            result={"status": "operator_review_ready"})
    assert swapped
    assert not list(private.iterdir())


def test_operator_cli_extraction_kwargs_match_real_entrypoint(monkeypatch):
    """A mocked delegate must not hide unexpected CLI-only keyword arguments."""
    from inspect import signature
    from server.m365_outlook_sync_readiness import main, extract_after_readiness
    monkeypatch.setenv("OWG_M365_GRAPH_TOKEN", "private-token")
    monkeypatch.setenv("OWG_DASHBOARD_SESSION", "private-session")
    with patch("server.m365_outlook_sync_readiness.extract_after_readiness",
               return_value={"status": "review_required", "checkpoint_advanced": False}) as delegate:
        assert main(_cli_extract_args()) == 0
    signature(extract_after_readiness).bind(**delegate.call_args.kwargs)
    assert "consent_local_append" not in delegate.call_args.kwargs


def test_operator_rejects_unsafe_audit_storage_before_mutation(monkeypatch, capsys, tmp_path):
    """Invalid private storage must block both writers before any side effect."""
    from server.m365_outlook_sync_readiness import main
    monkeypatch.setenv("OWG_DASHBOARD_SESSION", "private-session")
    missing = tmp_path / "missing-audit"
    with (patch("server.m365_outlook_sync_readiness.append_after_review") as append,
          patch("server.m365_outlook_sync_readiness.commit_after_review") as commit):
        for action in ("append", "commit"):
            args = _cli_commit_args()
            if action == "append":
                args.remove("--commit-after-review")
                args.remove("--confirm-events-appended")
                args.remove("--confirm-checkpoint-commit")
                pos = args.index("--expected-previous-candidate-sha256")
                del args[pos:pos + 2]
                for key in ("--receipt-file", "--journal-dir", "--handoff-dir",
                            "--successor-label"):
                    pos = args.index(key)
                    del args[pos:pos + 2]
                args.extend(["--append-after-review", "--consent-local-append"])
            args.extend(["--audit-dir", str(missing)])
            assert main(args) == 2
            assert json.loads(capsys.readouterr().out)["status"] == "refused"
        append.assert_not_called()
        commit.assert_not_called()
