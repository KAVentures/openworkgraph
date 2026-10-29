"""Persistent event-log quality and mapping reports (#370 R6)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_quality as quality
from server import event_log_workspace as workspace

ROOT = Path(__file__).resolve().parents[1]
MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time",
           "resource_column": "who", "attribute_columns": ["region", "amount"]}
BAD = "\n".join([
    "case,activity,time,who,region,amount",
    "c1,Receive,2024-01-01T09:00:00Z,ann,north,10",
    "c1,Check,2024-01-01T10:00:00Z,,north,10",            # missing resource
    "c1,Check,2024-01-01T10:00:00Z,,north,10",            # exact duplicate (and a tie)
    "c1,Close,2024-01-01T10:00:00Z,bob,,",                 # tie with the checks, empty attributes
    "c2,Receive,2024-01-02T09:00:00Z,ann,south,5",
    "c3,Receive,2099-01-01T09:00:00Z,ann,south,5",         # dated after import; single-event case
    ",Receive,2024-01-03T09:00:00Z,ann,south,5",           # missing case
    "c4,,2024-01-03T09:00:00Z,ann,south,5",                # missing activity
    "c4,Receive,,ann,south,5",                             # missing time
    "c4,Receive,2024-01-03 09:00:00,ann,south,5",          # no UTC offset
    "c4,Receive,2024-13-45T99:00:00Z,ann,south,5",         # offset but unparseable
]) + "\n"


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _import(text=BAD, mapping=MAPPING, fmt="csv"):
    return workspace.import_log(BytesIO(text.encode()), name="q", source_format=fmt, mapping=mapping)


def _without_digest_values(value):
    """Inspect the disclosure surface without treating SHA-256 hex as plaintext.

    Short source identifiers can appear by chance inside a 64-digit digest.
    Only known digest *fields* are masked; unexpected source text in any other
    field must remain visible to the counts-only assertion.
    """
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if key.endswith(("sha256", "sha256_v2")):
                assert isinstance(item, str) and len(item) == 64
                assert all(char in "0123456789abcdef" for char in item)
                cleaned[key] = "<sha256>"
            else:
                cleaned[key] = _without_digest_values(item)
        return cleaned
    if isinstance(value, list):
        return [_without_digest_values(item) for item in value]
    return value


def test_counts_only_assertion_masks_only_digest_fields():
    # A random digest containing an event identifier is not a plaintext leak;
    # the same identifier in an unexpected metadata field still is.
    sample = {"content_sha256_v2": "c4" + "0" * 62,
              "diagnostics": {"unexpected_sample": "north"}}
    checked = _without_digest_values(sample)
    assert checked["content_sha256_v2"] == "<sha256>"
    assert checked["diagnostics"]["unexpected_sample"] == "north"


def test_bad_data_produces_reason_counts_and_sample_free_diagnostics():
    made = _import()
    assert made["events"] == 6 and made["rejected_events"] == 5
    assert made["quality"]["rejected_by_reason"] == {
        "missing_case": 1, "missing_activity": 1, "missing_timestamp": 1,
        "timestamp_without_utc_offset": 1, "unparseable_timestamp": 1}
    report = quality.quality(made["log_id"])["reports"][0]
    rows, d = report["rows"], report["diagnostics"]
    assert rows["rejected_rows_retained"] is False and rows["reason_counts_can_overlap"] is True
    assert d["duplicate_events"] == 1
    assert d["events_sharing_a_timestamp_in_a_case"] == 3 and d["cases_with_shared_timestamps"] == 1
    assert "event identifier" in d["tied_order"]
    assert d["events_without_resource"] == 2 and d["events_without_case"] == 0
    assert d["events_dated_after_import"] == 1
    assert d["case_notions"] == [{"object_type": "case", "cases": 3, "single_event_cases": 2}]
    coverage = {a["attribute"]: a for a in d["attribute_coverage"]}
    assert coverage["region"] == {"attribute": "region", "events_with_value": 5, "events_without_value": 1}
    assert report["completeness"]["historical_completeness"] == "unknown"
    serialized = str(_without_digest_values(report))
    assert "north" not in serialized and "ann" not in serialized and "c4" not in serialized  # counts only



def test_future_event_reference_is_explicit_and_report_anchored():
    made = _import()
    result = quality.quality(made["log_id"])
    report = result["reports"][0]
    saved = report["diagnostics"]
    assert saved["future_event_reference"] == {
        "at": report["created_at"], "basis": "report_created"
    }
    assert saved["events_dated_after_reference"] == saved["events_dated_after_import"] == 1

    current = result["current"]["diagnostics"]
    assert current["future_event_reference"]["basis"] == "current_query"
    assert current["events_dated_after_reference"] == current["events_dated_after_import"] == 1
    assert current["future_event_reference"]["at"].endswith("Z")
    assert set(current["future_event_reference"]) == {"at", "basis"}


def test_future_event_reference_uses_supplied_utc_instant_not_wall_clock():
    made = _import()
    conn = workspace._connect()
    try:
        before = quality.diagnostics(conn, made["log_id"],
                                     reference_at=datetime(2098, 12, 31, tzinfo=timezone.utc))
        after = quality.diagnostics(conn, made["log_id"],
                                    reference_at=datetime(2100, 1, 1, tzinfo=timezone.utc))
        assert before["events_dated_after_reference"] == 1
        assert after["events_dated_after_reference"] == 0
        assert before["events_dated_after_import"] == 1  # compatibility alias
        assert after["events_dated_after_import"] == 0
        assert after["future_event_reference"] == {
            "at": "2100-01-01T00:00:00Z", "basis": "current_query"
        }
        # The comparison uses UTC even when supplied in another timezone.
        shifted = quality.diagnostics(conn, made["log_id"],
                                      reference_at=datetime(2100, 1, 1, 1,
                                                            tzinfo=timezone(timedelta(hours=1))))
        assert shifted["events_dated_after_reference"] == 0
        assert shifted["future_event_reference"]["at"] == "2100-01-01T00:00:00Z"
        with pytest.raises(ValueError, match="timezone-aware"):
            quality.diagnostics(conn, made["log_id"], reference_at=datetime(2100, 1, 1))
    finally:
        conn.close()



def test_current_resource_presence_counts_legacy_unicode_whitespace_without_mutation():
    """Current diagnostics use streaming-reader presence semantics for old rows."""
    conn = workspace._connect()
    try:
        stamp = datetime(2024, 1, 1)
        resources = [
            ("b1:e-1", "   "),
            ("b1:e-2", "\u00a0\u2003"),  # non-ASCII spaces are also missing
            ("b1:e-3", "\u2009worker\u00a0"),  # padded nonblank ID remains supplied
            ("b1:e-4", ""),
            ("b2:e-1", "\u3000"),
            ("b2:e-2", "worker"),
            ("b2:e-3", " \u202f "),
            ("b2:e-4", "0"),
        ]
        conn.executemany("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)", [
            ("log_legacy_resource", event_id, "Review", stamp, resource, "{}")
            for event_id, resource in resources
        ])
        current = quality.diagnostics(conn, "log_legacy_resource")
        batch_one = quality.diagnostics(conn, "log_legacy_resource", "b1:")
        batch_two = quality.diagnostics(conn, "log_legacy_resource", "b2:")
        stored = conn.execute(
            "SELECT event_id, resource FROM events WHERE log_id = ? ORDER BY event_id",
            ["log_legacy_resource"]).fetchall()
    finally:
        conn.close()

    # The streaming reader uses str.strip() only to determine whether an ID
    # is present; it preserves all bytes of nonblank identifiers.
    assert sum(not value.strip() for _, value in resources) == 5
    assert current["events"] == 8
    assert current["events_without_resource"] == 5
    assert batch_one["events"] == 4 and batch_one["events_without_resource"] == 3
    assert batch_two["events"] == 4 and batch_two["events_without_resource"] == 2
    assert stored == resources  # no normalization or migration of historical rows
    assert "worker" not in str(current) and "\u2009" not in str(current)



def test_attribute_coverage_excludes_explicit_json_null_and_empty_but_keeps_false_and_zero():
    """JSON null and empty strings are absent; false and zero are valid values."""
    conn = workspace._connect()
    try:
        stamp = datetime(2024, 1, 1)
        conn.executemany("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)", [
            ("log_coverage", "event-1", "Review", stamp, "", 
             '{"nullable":null,"blank":"","flag":false,"count":0}'),
            ("log_coverage", "event-2", "Done", stamp + timedelta(seconds=1), "",
             '{"nullable":"known","blank":"filled"}'),
        ])
        result = quality.diagnostics(conn, "log_coverage")
    finally:
        conn.close()
    coverage = {item["attribute"]: item for item in result["attribute_coverage"]}
    assert result["events"] == 2
    for attribute in ("nullable", "blank", "flag", "count"):
        assert coverage[attribute] == {
            "attribute": attribute, "events_with_value": 1, "events_without_value": 1
        }



def test_attribute_coverage_counts_distinct_events_with_duplicate_json_keys():
    """Repeated keys in supplied JSON must not inflate per-event coverage."""
    conn = workspace._connect()
    try:
        stamp = datetime(2024, 1, 1)
        conn.executemany("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)", [
            ("log_repeated_keys", "e-1", "Review", stamp, "", '{"tag":"a","tag":"b"}'),
            ("log_repeated_keys", "e-2", "Review", stamp + timedelta(seconds=1), "",
             '{"tag":"","tag":"filled"}'),
            ("log_repeated_keys", "e-3", "Review", stamp + timedelta(seconds=2), "",
             '{"tag":null,"tag":""}'),
            ("log_repeated_keys", "e-4", "Review", stamp + timedelta(seconds=3), "", "{}"),
        ])
        result = quality.diagnostics(conn, "log_repeated_keys")
    finally:
        conn.close()

    assert result["events"] == 4
    assert result["attribute_coverage_total_keys"] == 1
    assert result["attribute_coverage_truncated"] is False
    assert result["attribute_coverage"] == [
        {"attribute": "tag", "events_with_value": 2, "events_without_value": 2}
    ]
    # The report exposes only the key and counts, never supplied values.
    assert "filled" not in str(result) and '"a"' not in str(result)


def test_attribute_coverage_reports_omitted_keys_without_exposing_values():
    """A capped top-32 list must not imply the other source keys were absent."""
    conn = workspace._connect()
    try:
        stamp = datetime(2024, 1, 1)
        many = {f"k_{i:02d}": i for i in range(35)}
        conn.executemany("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)", [
            ("log_many_keys", "e-1", "Review", stamp, "", json.dumps(many)),
            ("log_many_keys", "e-2", "Done", stamp + timedelta(seconds=1), "",
             json.dumps({"k_33": False, "k_34": 0})),
            ("log_exact_cap", "e-1", "Review", stamp, "",
             json.dumps({f"a_{i:02d}": i for i in range(32)})),
            ("log_no_keys", "e-1", "Review", stamp, "", "{}"),
        ])
        many_report = quality.diagnostics(conn, "log_many_keys")
        at_cap = quality.diagnostics(conn, "log_exact_cap")
        empty = quality.diagnostics(conn, "log_no_keys")
    finally:
        conn.close()

    rows = many_report["attribute_coverage"]
    names = [row["attribute"] for row in rows]
    assert many_report["events"] == 2
    assert many_report["attribute_coverage_total_keys"] == 35
    assert many_report["attribute_coverage_truncated"] is True
    assert len(rows) == quality.MAX_ATTRIBUTES_REPORTED == 32
    assert names[:2] == ["k_33", "k_34"]
    assert names[2:] == [f"k_{i:02d}" for i in range(30)]
    assert rows[0]["events_with_value"] == 2
    assert rows[1]["events_with_value"] == 2
    assert rows[2]["events_with_value"] == 1
    assert rows[2]["events_without_value"] == 1
    assert "k_30" not in names and "k_31" not in names and "k_32" not in names
    # Source values never appear in these counts-only summaries.
    assert "Review" not in str(many_report) and "Done" not in str(many_report)

    assert at_cap["attribute_coverage_total_keys"] == 32
    assert at_cap["attribute_coverage_truncated"] is False
    assert len(at_cap["attribute_coverage"]) == 32
    assert empty["attribute_coverage_total_keys"] == 0
    assert empty["attribute_coverage_truncated"] is False
    assert empty["attribute_coverage"] == []


def test_duplicate_diagnostic_requires_matching_attributes_and_qualified_links():
    """Coincident activity/time/case is not an exact duplicate of stored content."""
    conn = workspace._connect()
    try:
        stamp = datetime(2024, 1, 1)
        conn.execute("INSERT INTO objects VALUES (?, ?, ?, ?)",
                     ["log_duplicates", "case-1", "case", "{}"])
        conn.executemany("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)", [
            ("log_duplicates", "e-1", "Review", stamp, "r", '{"channel":"web"}'),
            ("log_duplicates", "e-2", "Review", stamp, "r", '{"channel":"phone"}'),
            ("log_duplicates", "e-3", "Review", stamp, "r", '{"channel":"web"}'),
            ("log_duplicates", "e-4", "Review", stamp, "r", '{"channel":"web"}'),
        ])
        conn.executemany("INSERT INTO event_objects VALUES (?, ?, ?, ?)", [
            ("log_duplicates", "e-1", "case-1", "primary"),
            ("log_duplicates", "e-2", "case-1", "primary"),
            ("log_duplicates", "e-3", "case-1", "secondary"),
            ("log_duplicates", "e-4", "case-1", "primary"),
        ])
        result = quality.diagnostics(conn, "log_duplicates")
        assert result["events"] == 4
        assert result["events_sharing_a_timestamp_in_a_case"] == 4
        assert result["duplicate_events"] == 1  # only e-1 and e-4 match
        assert "phone" not in str(result) and "primary" not in str(result)
        conn.execute("UPDATE events SET attributes = ? WHERE log_id = ? AND event_id = ?",
                     ['{"channel":"other"}', "log_duplicates", "e-4"])
        assert quality.diagnostics(conn, "log_duplicates")["duplicate_events"] == 0
    finally:
        conn.close()



def test_qualified_links_do_not_inflate_case_sizes_or_shared_timestamp_ties():
    """Multiple qualifiers for one event-object pair still represent one event."""
    conn = workspace._connect()
    try:
        stamp = datetime(2024, 1, 1)
        conn.execute("INSERT INTO objects VALUES (?, ?, ?, ?)",
                     ["log_qualifiers", "case-1", "case", "{}"])
        conn.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)",
                     ["log_qualifiers", "e-1", "Start", stamp, "", "{}"])
        conn.executemany("INSERT INTO event_objects VALUES (?, ?, ?, ?)", [
            ("log_qualifiers", "e-1", "case-1", "primary"),
            ("log_qualifiers", "e-1", "case-1", "secondary"),
        ])
        one = quality.diagnostics(conn, "log_qualifiers")
        assert one["events"] == 1
        assert one["case_notions"] == [
            {"object_type": "case", "cases": 1, "single_event_cases": 1}]
        assert one["events_sharing_a_timestamp_in_a_case"] == 0
        assert one["cases_with_shared_timestamps"] == 0

        # A second event at another time changes case size, not tied order.
        conn.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)",
                     ["log_qualifiers", "e-2", "Review", stamp + timedelta(hours=1), "", "{}"])
        conn.execute("INSERT INTO event_objects VALUES (?, ?, ?, ?)",
                     ["log_qualifiers", "e-2", "case-1", "primary"])
        two = quality.diagnostics(conn, "log_qualifiers")
        assert two["case_notions"][0]["single_event_cases"] == 0
        assert two["events_sharing_a_timestamp_in_a_case"] == 0

        # Two distinct events at the same time do constitute a tie.
        conn.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)",
                     ["log_qualifiers", "e-3", "Finish", stamp + timedelta(hours=1), "", "{}"])
        conn.executemany("INSERT INTO event_objects VALUES (?, ?, ?, ?)", [
            ("log_qualifiers", "e-3", "case-1", "primary"),
            ("log_qualifiers", "e-3", "case-1", "secondary"),
        ])
        three = quality.diagnostics(conn, "log_qualifiers")
        assert three["events_sharing_a_timestamp_in_a_case"] == 2
        assert three["cases_with_shared_timestamps"] == 1
        assert three["case_notions"] == [
            {"object_type": "case", "cases": 1, "single_event_cases": 0}]
        assert three["duplicate_events"] == 0
        assert "primary" not in str(three) and "secondary" not in str(three)
    finally:
        conn.close()


def test_timestamp_ties_count_distinct_events_across_multiple_case_objects():
    """One event tied in two case notions is one event, not two occurrences."""
    conn = workspace._connect()
    try:
        stamp = datetime(2024, 1, 1)
        later = stamp + timedelta(minutes=1)
        conn.executemany("INSERT INTO objects VALUES (?, ?, ?, ?)", [
            ("log_multi_ties", "order-1", "order", "{}"),
            ("log_multi_ties", "invoice-1", "invoice", "{}"),
            ("log_multi_ties", "order-2", "order", "{}"),
        ])
        conn.executemany("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)", [
            ("log_multi_ties", "e-1", "Open", stamp, "", "{}"),
            ("log_multi_ties", "e-2", "Check", stamp, "", "{}"),
            ("log_multi_ties", "e-3", "Pack", later, "", "{}"),
        ])
        conn.executemany("INSERT INTO event_objects VALUES (?, ?, ?, ?)", [
            ("log_multi_ties", "e-1", "order-1", "primary"),
            ("log_multi_ties", "e-1", "order-1", "secondary"),
            ("log_multi_ties", "e-1", "invoice-1", "primary"),
            ("log_multi_ties", "e-2", "order-1", "primary"),
            ("log_multi_ties", "e-2", "invoice-1", "primary"),
            ("log_multi_ties", "e-3", "order-2", "primary"),
        ])
        first = quality.diagnostics(conn, "log_multi_ties")
        assert first["events"] == 3
        assert first["events_sharing_a_timestamp_in_a_case"] == 2
        assert first["cases_with_shared_timestamps"] == 2

        # A second, independent tie adds two new events and one affected case.
        conn.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)",
                     ["log_multi_ties", "e-4", "Ship", later, "", "{}"])
        conn.execute("INSERT INTO event_objects VALUES (?, ?, ?, ?)",
                     ["log_multi_ties", "e-4", "order-2", "primary"])
        second = quality.diagnostics(conn, "log_multi_ties")
        assert second["events"] == 4
        assert second["events_sharing_a_timestamp_in_a_case"] == 4
        assert second["cases_with_shared_timestamps"] == 3
        # No source event IDs or object IDs leak into the counts-only diagnostic.
        assert "order-1" not in str(second) and "invoice-1" not in str(second)
    finally:
        conn.close()


def test_events_without_case_counts_unlinked_events_not_missing_literal_case_type():
    """OCEL object types are case notions; only absent object links count as unlinked."""
    conn = workspace._connect()
    try:
        stamp = datetime(2024, 1, 1)
        conn.executemany("INSERT INTO objects VALUES (?, ?, ?, ?)", [
            ("log_unlinked", "order-1", "order", "{}"),
            ("log_unlinked", "case-1", "case", "{}"),
        ])
        conn.executemany("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)", [
            ("log_unlinked", "e-order", "Open", stamp, "", "{}"),
            ("log_unlinked", "e-orphan", "Check", stamp, "", "{}"),
            ("log_unlinked", "e-case", "Close", stamp, "", "{}"),
        ])
        conn.executemany("INSERT INTO event_objects VALUES (?, ?, ?, ?)", [
            ("log_unlinked", "e-order", "order-1", "primary"),
            ("log_unlinked", "e-case", "case-1", "primary"),
        ])
        result = quality.diagnostics(conn, "log_unlinked")
        assert result["events"] == 3
        # The order-only event is linked even though it has no 'case' object.
        # Preserve the legacy API key, but its meaning is unlinked events.
        assert result["events_without_case"] == 1
        assert {row["object_type"]: (row["cases"], row["single_event_cases"])
                for row in result["case_notions"]} == {"order": (1, 1), "case": (1, 1)}
        assert "order-1" not in str(result) and "e-orphan" not in str(result)
    finally:
        conn.close()


def test_same_file_and_mapping_reproduce_every_digest_and_mapping_changes_are_visible():
    first, second = _import(), _import()
    a, b = (quality.quality(m["log_id"])["reports"][0] for m in (first, second))
    for key in ("source_sha256", "content_sha256", "content_sha256_v2"):
        assert a[key] == b[key]
    assert a["transform"] == b["transform"] and len(a["content_sha256"]) == 64
    assert a["transform"]["mapping"]["mapping_version"] == quality.MAPPING_VERSION
    reordered = _import(mapping={**MAPPING, "attribute_columns": ["amount", "region"]})
    c = quality.quality(reordered["log_id"])["reports"][0]
    assert c["source_sha256"] == a["source_sha256"]
    assert c["transform"]["transform_sha256"] != a["transform"]["transform_sha256"]
    assert c["content_sha256"] != a["content_sha256"]     # attributes are stored in the declared order
    fewer = _import(mapping={k: v for k, v in MAPPING.items() if k != "attribute_columns"})
    assert quality.quality(fewer["log_id"])["reports"][0]["diagnostics"]["attribute_coverage"] == []


def test_appended_batches_get_their_own_report_and_whole_log_diagnostics_move():
    made = _import()
    log = made["log_id"]
    newer = "case,activity,time,who,region,amount\nc2,Close,2024-01-02T12:00:00Z,bob,south,5\n" \
            "c1,Receive,2024-01-01T09:00:00Z,ann,north,10\n"                       # second row already held
    appended = workspace.append_log(log, BytesIO(newer.encode()), source_format="csv", mapping=MAPPING)
    assert appended["quality"]["batch"] == 1
    result = quality.quality(log)
    assert [r["batch"] for r in result["reports"]] == [0, 1]
    batch = result["reports"][1]
    assert batch["kind"] == "append" and batch["rows"]["accepted"] == 1
    assert batch["rows"]["duplicates_skipped_on_append"] == 1 and batch["diagnostics"]["events"] == 1
    assert result["current"]["diagnostics"]["events"] == 7 and result["batches_without_report"] == []
    assert result["current"]["content_sha256"] != result["reports"][0]["content_sha256"]
    assert result["current"]["content_sha256_v2"] != result["reports"][0]["content_sha256_v2"]


def test_xes_reports_total_rejections_without_inventing_reasons_and_delete_removes_reports():
    xes = ('<log xmlns="http://www.xes-standard.org/"><trace><string key="concept:name" value="t1"/>'
           '<event><string key="concept:name" value="A"/><date key="time:timestamp" value="2024-01-01T00:00:00Z"/></event>'
           '<event><string key="concept:name" value="B"/></event></trace></log>')
    made = _import(xes, mapping=None, fmt="xes")
    report = quality.quality(made["log_id"])["reports"][0]
    assert report["rows"]["rejected"] == 1 and report["rows"]["rejected_by_reason"] is None
    assert "not broken down" in report["rows"]["rejected_by_reason_note"]
    assert "XES" in report["transform"]["mapping"]["standard"]
    workspace.delete_log(made["log_id"])
    conn = workspace._connect()
    try:
        assert conn.execute("SELECT count(*) FROM log_quality").fetchone()[0] == 0
    finally:
        conn.close()
    with pytest.raises(workspace.EventLogWorkspaceError):
        quality.quality(made["log_id"])


def test_a_failed_report_never_undoes_the_import_and_is_disclosed(monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("report storage failed")
    working = quality.record
    monkeypatch.setattr(quality, "record", broken)
    made = _import()
    assert made["events"] == 6 and made["quality"] is None
    monkeypatch.setattr(quality, "record", working)   # undo() would also reset the data directory
    result = quality.quality(made["log_id"])
    assert result["reports"] == [] and result["batches_without_report"] == [0]
    assert result["current"]["diagnostics"]["events"] == 6


def test_real_http_route_requires_a_dashboard_session(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
doc = b"case,activity,time,lifecycle\n1,A,2024-01-01T00:00:00Z,start\n1,B,2024-01-01T00:00:00Z,complete\n2,A,2024-01-01,start\n"
with TestClient(app) as client:
    headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    made = client.post("/v1/event-logs/import?format=csv&name=x&case_column=case&activity_column=activity"
                       "&timestamp_column=time&attribute_column=lifecycle", content=doc,
                       headers={**headers, "x-owg-local-storage-confirmed": "true"})
    assert made.status_code == 200 and made.json()["quality"]["rejected_by_reason"]["timestamp_without_utc_offset"] == 1
    path = "/v1/event-logs/" + made.json()["log_id"] + "/quality"
    assert client.get(path, headers={"Authorization": "Bearer " + local_auth.ensure_api_token()}).status_code in (401, 403)
    result = client.get(path, headers=headers)
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store", result.text
    body = result.json()
    assert body["reports"][0]["diagnostics"]["events_sharing_a_timestamp_in_a_case"] == 2
    assert body["reports"][0]["transform"]["mapping"]["attribute_columns"] == ["lifecycle"]
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


def test_v2_content_digest_distinguishes_delimiter_collisions_without_changing_v1():
    """A separator in source-owned identity/activity must not alias another row."""
    import duckdb

    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE TABLE events (log_id VARCHAR, event_id VARCHAR, activity VARCHAR, "
                     "ts TIMESTAMP, resource VARCHAR, attributes VARCHAR)")
        conn.execute("CREATE TABLE event_objects (log_id VARCHAR, event_id VARCHAR, "
                     "object_id VARCHAR, qualifier VARCHAR)")
        timestamp = "2024-01-01 00:00:00"
        conn.executemany("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)", [
            ("left", "a\x1fb", "c", timestamp, "r", "{}"),
            ("right", "a", "b\x1fc", timestamp, "r", "{}"),
        ])
        # v1 joins adjacent untrusted fields with the same byte that may occur
        # inside either field. Preserve it for historical references, but do
        # not rely on it as a collision-resistant encoding of field boundaries.
        assert quality.content_digest(conn, "left") == quality.content_digest(conn, "right")
        assert quality.content_digest_v2(conn, "left") != quality.content_digest_v2(conn, "right")
        assert quality.content_digest_v2(conn, "left") == quality.content_digest_v2(conn, "left")
        conn.execute("INSERT INTO event_objects VALUES (?, ?, ?, ?)", ["left", "a\x1fb", "o", None])
        assert quality.content_digest_v2(conn, "left") != quality.content_digest_v2(conn, "right")
    finally:
        conn.close()


def test_v2_digest_is_additive_to_saved_quality_and_current_report():
    made = _import()
    report = quality.quality(made["log_id"])
    assert len(report["reports"][0]["content_sha256_v2"]) == 64
    assert report["reports"][0]["content_sha256_v2"] == report["current"]["content_sha256_v2"]
    assert report["reports"][0]["content_sha256"] == report["current"]["content_sha256"]
    assert report["reports"][0]["content_sha256_v2"] != report["reports"][0]["content_sha256"]


@pytest.mark.skipif(not os.environ.get("OWG_SCALE_ACCEPTANCE"), reason="scale acceptance runs on demand")
def test_one_million_events_report_within_budget():
    import time
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    rows = ["case,activity,time,who"]
    for c in range(200_000):
        for s in range(5):
            rows.append(f"c{c},S{s},{(start + timedelta(seconds=c * 10 + s // 2)).isoformat()},r{c % 50}")
    text = "\n".join(rows) + "\n"
    started = time.perf_counter()
    made = workspace.import_log(BytesIO(text.encode()), name="big", source_format="csv",
                                mapping={"case_column": "case", "activity_column": "activity",
                                         "timestamp_column": "time", "resource_column": "who"})
    assert time.perf_counter() - started < 60
    assert made["quality"]["events_sharing_a_timestamp_in_a_case"] == 200_000 * 4
