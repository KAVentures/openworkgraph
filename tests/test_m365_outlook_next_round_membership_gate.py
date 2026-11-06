"""Functional gate ordering: never contact Graph on failed local membership."""
from unittest.mock import patch
import pytest
from server.m365_outlook_verified_next_round import run_verified_next_round
from server.m365_outlook_owg_receipt import ReceiptError

def test_missing_membership_refuses_before_provider_get(tmp_path):
    prior = tmp_path / "prior.json"
    prior.write_text('{"csv_sha256":"' + 'a'*64 + '"}')
    handoff = tmp_path / "handoff"
    handoff.mkdir()
    import hashlib
    from server.m365_outlook_resume_journal import _canonical
    key = hashlib.sha256(_canonical({"candidate_sha256":'a'*64,"log_id":"log_0123456789abcdef"})).hexdigest()
    (handoff / ("handoff-" + key + ".json")).write_text("{}")
    kwargs = dict(tenant="tenant", mailbox="mailbox", folder="folder",
        log_id="log_0123456789abcdef", confirm_log_id="log_0123456789abcdef",
        grant_file=tmp_path/"grant", previous_candidate=prior,
        previous_csv=tmp_path/"prior.csv", receipt_file=tmp_path/"receipt",
        journal_dir=tmp_path, handoff_dir=handoff, successor_label="next",
        output=tmp_path/"next.csv", candidate=tmp_path/"next.json",
        token="test", consent_local_read=True, confirm_removals_reviewed=True,
        consent_delta_read=True, consent_local_export=True,
        require_event_membership=True)
    with patch("server.m365_outlook_verified_next_round.prepare_handoff",
               return_value={"idempotent_replay":True}), \
         patch("server.m365_outlook_verified_next_round.reconcile_membership",
               return_value={"status":"manual_reconciliation_required"}) as membership, \
         patch("server.m365_outlook_verified_next_round.export_delta") as graph:
        with pytest.raises(ReceiptError, match="membership incomplete"):
            run_verified_next_round(**kwargs)
        membership.assert_called_once()
        graph.assert_not_called()

def test_membership_gate_defaults_off_for_compatibility():
    from inspect import signature
    assert signature(run_verified_next_round).parameters["require_event_membership"].default is False
