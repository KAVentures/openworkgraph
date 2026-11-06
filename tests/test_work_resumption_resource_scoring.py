"""Resource-local assignment evidence must not conflate interleaved cases."""
from tests.test_work_resumption import _database, _day, _ref, REF
from server.work_resumption import resumption_candidates


def test_interleaved_distinct_resources_are_not_a_correlated_case():
    db = _database([
        (_day(12, "09:00"), _ref("a")),
        (_day(12, "09:01"), _ref("b")),
        (_day(14, "10:00"), _ref("a")),
        (_day(14, "10:01"), _ref("b")),
    ])
    [candidate] = resumption_candidates(db, lease={"mode": "all_saved"})["candidates"]
    assert candidate["shared_pointer_count"] == 2
    assert candidate["case_assignment_status"] == "unresolved_independent_resources"
    assert candidate["correlated_resource_pair_count"] == 0
    assert candidate["case_assignment_proven"] is False


def test_correlated_pair_requires_coobservation_in_both_sessions():
    def together():
        return {**_ref("a"), "file_reference": {"file_ref": "owg:f:" + "9" * 32}}
    db = _database([(_day(12, "09:00"), together()),
                    (_day(14, "10:00"), together())])
    [candidate] = resumption_candidates(db, lease={"mode": "all_saved"})["candidates"]
    assert candidate["case_assignment_status"] == "corroborated_candidate"
    assert candidate["correlated_resource_pair_count"] == 1
    assert candidate["case_assignment_proven"] is False


def test_one_sided_coobservation_is_not_correlated():
    db = _database([
        (_day(12, "09:00"), {**_ref("a"), "file_reference": {"file_ref": "owg:f:" + "9" * 32}}),
        (_day(14, "10:00"), _ref("a")),
        (_day(14, "10:01"), {"file_reference": {"file_ref": "owg:f:" + "9" * 32}}),
    ])
    [candidate] = resumption_candidates(db, lease={"mode": "all_saved"})["candidates"]
    assert candidate["case_assignment_status"] == "unresolved_independent_resources"


def test_grant_off_has_no_assignment_candidates():
    db = _database([(_day(12, "09:00"), _ref("a")),
                    (_day(14, "10:00"), _ref("a"))])
    assert resumption_candidates(db, lease={"mode": "off"})["candidates"] == []
