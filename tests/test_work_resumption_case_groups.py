"""Observed case-group partitioning is evidence-based, not time-based."""
from server.work_resumption import _observed_case_groups


def _bucket():
    a = ("jira", "issue", "A")
    b = ("jira", "issue", "B")
    f = ("local_file", "file", "F")
    refs = {key: {"provider": key[0], "resource_kind": key[1], "resource_ref": key[2]}
            for key in (a, b, f)}
    tracks = {a: {"observations": [(1.0, "event-a"), (1.1, "event-af")]},
              b: {"observations": [(1.05, "event-b")]},
              f: {"observations": [(1.1, "event-af")]}}
    return {"refs": refs, "tracks": tracks, "coobserved": {(a, f)}}


def test_interleaved_independent_resources_do_not_merge_by_time():
    groups = _observed_case_groups(_bucket())
    assert len(groups) == 2
    paired = next(group for group in groups if group["resource_count"] == 2)
    solo = next(group for group in groups if group["resource_count"] == 1)
    assert {p["resource_ref"] for p in paired["resource_pointers"]} == {"A", "F"}
    assert [p["resource_ref"] for p in solo["resource_pointers"]] == ["B"]
    assert paired["assignment_status"] == "observed_co_reference"
    assert solo["assignment_status"] == "independent_resource"
    assert paired["proves_same_case"] is False
    assert "event-b" not in paired["evidence_event_ids"]


def test_no_correlated_events_means_all_singletons():
    bucket = _bucket()
    bucket["coobserved"] = set()
    groups = _observed_case_groups(bucket)
    assert len(groups) == 3
    assert all(group["resource_count"] == 1 for group in groups)


def test_empty_and_bounded_evidence():
    bucket = _bucket()
    assert _observed_case_groups({"refs": {}, "tracks": {}, "coobserved": set()}) == []
    a = ("jira", "issue", "A")
    bucket["tracks"][a]["observations"] = [(float(i), f"event-{i:02d}") for i in range(20)]
    groups = _observed_case_groups(bucket)
    paired = next(group for group in groups if group["resource_count"] == 2)
    assert len(paired["evidence_event_ids"]) == 12
    assert paired["evidence_truncated"] is True
