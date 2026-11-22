"""Checkpoint race acceptance for the production bounded Graph extraction entrypoint.

Provider GET is contract-simulated; the production extract() implementation,
including its before/after durable checkpoint guard, is not mocked.
"""
import json
from unittest.mock import patch

import pytest

from server import m365_outlook_operator_cycle as cycle
from server.m365_outlook_owg_receipt import ReceiptError


def test_checkpoint_advance_during_graph_read_refuses_successor_without_append():
    first = {"generation": 1, "candidate_sha256": "a" * 64,
             "log_id": "log", "cursor_url": "https://graph.microsoft.com/v1.0/delta"}
    second = {**first, "generation": 2, "candidate_sha256": "b" * 64}
    kwargs = dict(tenant="tenant", mailbox="mailbox", folder="folder",
                  grant_file="/private/grant", previous_candidate="/private/previous",
                  log_id="log", ledger_dir="/private/ledger", expected_generation=1)
    with (
        patch.object(cycle, "inspect_checkpoint", side_effect=[first, second]) as inspect,
        patch.object(cycle, "_private_read",
                     return_value=json.dumps({"csv_sha256": "a" * 64}).encode()) as read,
        patch.object(cycle, "run_verified_next_round",
                     return_value={"status": "next_round_review_ready",
                                   "event_snapshots": 1,
                                   "removal_markers_for_review": 0}) as graph,
        patch.object(cycle, "append_and_record") as append,
        patch.object(cycle, "commit_checkpoint") as checkpoint,
    ):
        with pytest.raises(ReceiptError, match="Checkpoint changed"):
            cycle.extract(consent_local_read=True, consent_delta_read=True,
                          consent_local_export=True,
                          confirm_removals_reviewed=True, **kwargs)
    assert inspect.call_count == 2
    read.assert_called_once()
    graph.assert_called_once()
    append.assert_not_called()
    checkpoint.assert_not_called()


def test_stale_predecessor_blocks_graph_before_any_provider_request():
    previous = {"generation": 2, "candidate_sha256": "b" * 64,
                "log_id": "log"}
    with (
        patch.object(cycle, "inspect_checkpoint", return_value=previous),
        patch.object(cycle, "run_verified_next_round") as graph,
        patch.object(cycle, "append_and_record") as append,
    ):
        with pytest.raises(ReceiptError, match="Missing or stale"):
            cycle.extract(consent_local_read=True, consent_delta_read=True,
                          consent_local_export=True,
                          confirm_removals_reviewed=True,
                          tenant="tenant", mailbox="mailbox", folder="folder",
                          grant_file="/private/grant", log_id="log",
                          previous_candidate="/private/previous",
                          ledger_dir="/private/ledger", expected_generation=1)
    graph.assert_not_called()
    append.assert_not_called()
