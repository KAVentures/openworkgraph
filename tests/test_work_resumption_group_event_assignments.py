"""Exact event-level observed pair assignment, with independent interleaving."""
from tests.test_work_resumption_group_continuity import A, B, F, _db, _event
from server.work_resumption import resumption_candidates


def test_pair_event_assignments_exclude_interleaved_singletons():
    rows = [
        ("2026-10-12T09:00:00Z", _event(A, True)),
        ("2026-10-12T09:01:00Z", _event(B)),
        ("2026-10-12T09:02:00Z", _event(A)),
        ("2026-10-14T10:00:00Z", _event(B)),
        ("2026-10-14T10:01:00Z", _event(A, True)),
        ("2026-10-14T10:02:00Z", _event(A)),
    ]
    result = resumption_candidates(_db(rows), lease={"mode": "all_saved"})
    [pair] = result["observed_pair_event_assignments"]
    assert {p["resource_ref"] for p in pair["resource_pointers"]} == {A, F}
    assert pair["current_evidence_event_ids"] == ["4"]
    assert pair["earlier_corroborations"][0]["evidence_event_ids"] == ["0"]
    assert pair["assignment_status"] == "observed_same_event_pair"
    assert pair["proves_same_case"] is False
    assert "3" not in pair["current_evidence_event_ids"]
    assert "5" not in pair["current_evidence_event_ids"]


def test_unpaired_interleaving_and_denied_history_have_no_pair_assignments():
    rows = [
        ("2026-10-12T09:00:00Z", _event(A)),
        ("2026-10-12T09:01:00Z", _event(B)),
        ("2026-10-14T10:00:00Z", _event(A)),
        ("2026-10-14T10:01:00Z", _event(B)),
    ]
    assert resumption_candidates(_db(rows), lease={"mode": "all_saved"})["observed_pair_event_assignments"] == []
    paired = [("2026-10-12T09:00:00Z", _event(A, True)),
              ("2026-10-14T10:00:00Z", _event(A, True))]
    denied = resumption_candidates(_db(paired), lease={"mode": "off"},
                                   run_started_at="2026-10-14T00:00:00Z")
    assert denied["observed_pair_event_assignments"] == []
