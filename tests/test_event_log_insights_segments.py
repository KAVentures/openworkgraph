"""Insights point at case-attribute groups (values and numeric quartiles) whose cases take much longer."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest

from server import event_log_case_attributes as case_attributes
from server import event_log_cohorts as cohorts
from server import event_log_derive as derived_logs
from server import event_log_insights as insights
from server import event_log_workspace as workspace


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _iso(t):
    return t.isoformat().replace("+00:00", "Z")


def _cases(log, cohort, case_type=None):
    def run(conn):
        cohorts.restrict(conn, cohorts.parse_cohort(cohort), log)
        return {"n": conn.execute("SELECT count(DISTINCT case_id) FROM ce").fetchone()[0]}
    return workspace._query(log, case_type, run)["n"]


def test_numeric_quartile_segment_with_exact_filter():
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    objects, events = [], []
    for i in range(40):
        objects.append({"id": f"o{i}", "type": "order",
                        "attributes": [{"name": "amount", "time": _iso(start), "value": 100 + i * 37}]})
        t = start + timedelta(days=i)
        took = timedelta(days=10 if i >= 30 else 1)
        for k, (activity, at) in enumerate([("Create", t), ("Ship", t + took)]):
            events.append({"id": f"e{i}-{k}", "type": activity, "time": _iso(at), "attributes": [],
                           "relationships": [{"objectId": f"o{i}", "qualifier": ""}]})
    doc = {"objectTypes": [{"name": "order", "attributes": []}],
           "eventTypes": [{"name": n, "attributes": []} for n in ("Create", "Ship")], "objects": objects, "events": events}
    log = workspace.import_log(BytesIO(json.dumps(doc).encode()), name="o", source_format="ocel")["log_id"]
    r = insights.insights(log, {"case_type": "order", "limit": 30})
    segs = [f for f in r["findings"] if f["kind"] == "attribute_segment"]
    assert segs and "amount ≥" in segs[0]["title"] and "10.0× as long" in segs[0]["title"]
    top = segs[0]
    assert top["cases"] == 10 and set(top["cohort"]) == {"attribute_range"}
    assert _cases(log, top["cohort"], "order") == top["cases"]
    assert top["analysis"] == "root-cause" and "not a cause" in top["caveat"]
    assert len(segs) <= insights.MAX_SEGMENTS


def test_categorical_segment_from_a_joined_case_table(tmp_path):
    rows = ["case,activity,time"]
    for i in range(12):
        day = 1 + i
        rows += [f"c{i},Receive,2024-03-{day:02d}T08:00:00Z",
                 f"c{i},Pay,2024-03-{day:02d}T{'20' if i % 3 == 0 else '09'}:00:00Z"]
    log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="t", source_format="csv",
                               mapping={"case_column": "case", "activity_column": "activity", "timestamp_column": "time"})["log_id"]
    table = tmp_path / "regions.csv"
    table.write_text("id,Region,Batch\n" + "\n".join(
        f"c{i},{'APAC' if i % 3 == 0 else 'EU'},{'early' if i < 9 else 'later'}" for i in range(12)) + "\n")
    case_attributes.add(log, table, id_column="id", columns=["Region", "Batch"])
    r = insights.insights(log, {"limit": 30})
    seg = next(f for f in r["findings"] if f["kind"] == "attribute_segment")
    assert seg["cohort"] == {"attribute": "Region=APAC"} and seg["cases"] == 4
    assert seg["numbers"]["median_seconds_with"] == 12 * 3600 and seg["numbers"]["median_seconds_without"] == 3600
    assert _cases(log, seg["cohort"]) == 4
    report = insights.insights(log, {"cohort": {"attribute": "Batch=early"}, "limit": 30})
    association = next(f for f in report["findings"] if f["kind"] == "attribute_segment")
    selection = {"all_of": [report["cohort"], association["cohort"]]}
    assert _cases(log, selection) == association["cases"] == 3
    assert cohorts.describe(cohorts.parse_cohort(selection)) == selection
    copied = derived_logs.derive(log, {"name": "Selected APAC", "cohort": selection})
    assert copied["derived_from"]["cases_matched"] == 3
    assert copied["copied"]["events"] == 6
    for parts in ([], [{}, {"label": "empty"}], [{"all_of": []}, selection["all_of"][0]], [{}] * 5):
        with pytest.raises(workspace.EventLogWorkspaceError, match="all_of"):
            cohorts.parse_cohort({"all_of": parts})

    # No attributes, no segment findings.
    plain = workspace.import_log(BytesIO("\n".join(rows).encode()), name="p", source_format="csv",
                                 mapping={"case_column": "case", "activity_column": "activity", "timestamp_column": "time"})["log_id"]
    assert not [f for f in insights.insights(plain, {"limit": 30})["findings"] if f["kind"] == "attribute_segment"]
