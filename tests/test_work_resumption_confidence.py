"""Resumption confidence must not be inflated by multiplexed case pointers."""
import json
import sqlite3

from server.work_resumption import resumption_candidates


def _db(rows):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE events(event_id TEXT, observed_at TEXT, metadata_json TEXT)")
    for i, (at, metadata) in enumerate(rows):
        db.execute("INSERT INTO events VALUES (?, ?, ?)", (f"e{i}", at, json.dumps(metadata)))
    return db


def _ref(name):
    return {"resource_reference": {"provider": "github", "resource_kind": "pull_request",
                                   "resource_ref": "owg:r:" + name * 32}}


def _at(day, minute):
    return f"2026-10-{day:02d}T09:{minute:02d}:00Z"


def test_independent_interleaved_resources_do_not_inflate_likely_resumed():
    db = _db([(_at(12, 0), _ref("a")), (_at(12, 1), _ref("b")),
              (_at(14, 0), _ref("a")), (_at(14, 1), _ref("b"))])
    result = resumption_candidates(db, lease={"mode": "all_saved"})
    [candidate] = result["candidates"]
    assert candidate["pointer_overlap"] == 1.0
    assert candidate["case_assignment_status"] == "unresolved_independent_resources"
    assert candidate["association_status"] == "possible"
    assert candidate["confidence"] == 0.55
    assert candidate["case_assignment_proven"] is False


def test_correlated_pair_retains_stronger_candidate_evidence():
    both = {**_ref("a"), "file_reference": {"file_ref": "owg:f:" + "9" * 32}}
    db = _db([(_at(12, 0), both), (_at(14, 0), both)])
    [candidate] = resumption_candidates(db, lease={"mode": "all_saved"})["candidates"]
    assert candidate["case_assignment_status"] == "corroborated_candidate"
    assert candidate["association_status"] == "likely_resumed"
    assert candidate["confidence"] == 0.8
    assert candidate["case_assignment_proven"] is False


def test_one_resource_possible_and_access_off():
    db = _db([(_at(12, 0), _ref("a")), (_at(14, 0), _ref("a"))])
    [candidate] = resumption_candidates(db, lease={"mode": "all_saved"})["candidates"]
    assert candidate["association_status"] == "possible"
    assert candidate["confidence"] == 0.5
    assert resumption_candidates(db, lease={"mode": "off"})["candidates"] == []
