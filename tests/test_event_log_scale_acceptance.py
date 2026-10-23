"""10M-event acceptance for the columnar event-log workspace (roadmap #326 B).

Opt-in (``OWG_SCALE_ACCEPTANCE=1``): it writes a ~0.5 GB CSV, imports it
through the same path as the dashboard upload and times the analysis queries.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import time

import pytest

from server import event_log_analytics as analytics
from server import event_log_cases as case_pages
from server import event_log_model_conformance as graph_comparison
from server import event_log_cohorts as cohorts
from server import event_log_workspace as workspace

EVENTS = int(os.environ.get("OWG_EVENT_LOG_SCALE_EVENTS", "10000000"))
QUERY_BUDGET_SECONDS = 30.0
# Events are one minute apart, so half the cases start before this instant.
MIDPOINT = (datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=EVENTS // 2)).isoformat()

pytestmark = pytest.mark.skipif(os.environ.get("OWG_SCALE_ACCEPTANCE") != "1",
                                reason="set OWG_SCALE_ACCEPTANCE=1 to run the 10M-event acceptance")


def test_ten_million_event_log_imports_and_answers_within_budget(tmp_path, monkeypatch):
    import duckdb

    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)
    source = tmp_path / "large.csv"
    # 10 events per case; every 7th case skips "Review" and every 5th repeats "Fix" (rework).
    duckdb.sql(f"""COPY (
        SELECT 'case-' || (i // 10) AS case_id,
               CASE i % 10 WHEN 0 THEN 'Receive' WHEN 1 THEN 'Validate'
                   WHEN 2 THEN CASE WHEN (i // 10) % 7 = 0 THEN 'Skip' ELSE 'Review' END
                   WHEN 3 THEN 'Approve' WHEN 4 THEN 'Fix'
                   WHEN 5 THEN CASE WHEN (i // 10) % 5 = 0 THEN 'Fix' ELSE 'Check' END
                   WHEN 6 THEN 'Invoice' WHEN 7 THEN 'Pay' WHEN 8 THEN 'Archive' ELSE 'Close' END AS activity,
               strftime(TIMESTAMP '2024-01-01' + to_seconds(i * 60), '%Y-%m-%dT%H:%M:%SZ') AS time,
               'user-' || (i % 50) AS who
        FROM range({EVENTS}) t(i)) TO '{source.as_posix()}' (HEADER, DELIMITER ',')""")

    started = time.perf_counter()
    with source.open("rb") as handle:
        log = workspace.import_log(handle, name="scale", source_format="csv", mapping={
            "case_column": "case_id", "activity_column": "activity", "timestamp_column": "time",
            "resource_column": "who"})
    import_seconds = time.perf_counter() - started
    assert log["events"] == EVENTS and log["rejected_events"] == 0

    from shared.process_model import ProcessModel
    labels=['Receive','Validate','Review','Approve','Fix','Check','Invoice','Pay','Archive','Close']
    design=ProcessModel(goal='Compare the declared invoice process',baseline_source='user_declared',
        baseline_coverage='user_declared_design',baseline_path=labels,
        activities=[{'id':label.lower(),'label':label} for label in labels],
        transitions=[{'source':a.lower(),'target':b.lower()} for a,b in zip(labels,labels[1:])],
        entry_activity_id='receive',terminal_activity_ids=['close'])
    reference={'workflow_id':'scale_graph','revision':1,'content_sha256':'a'*64,'title':'Scale graph',
               'saved_at':'2024-01-01T00:00:00Z','process_model':design.model_dump()}
    graph_spec=graph_comparison.SavedGraphQuery.model_validate({key:reference[key] for key in ('workflow_id','revision','content_sha256')})
    timings = {}
    for name, call in (
        ("saved_graph", lambda: graph_comparison.compare(log["log_id"],reference,graph_spec)),
        ("case_query", lambda: case_pages.cases(log["log_id"], {})),
        ("variant_cases", lambda: case_pages.cases(log["log_id"], {"path":["Receive", "Validate", "Skip", "Approve", "Fix", "Fix", "Invoice", "Pay", "Archive", "Close"]})),
        ("case_events", lambda: case_pages.events(log["log_id"], {"case_id":"case-0","limit":3})),
        ("summary", lambda: workspace.summary(log["log_id"])),
        ("variants", lambda: workspace.variants(log["log_id"])),
        ("directly_follows", lambda: workspace.directly_follows(log["log_id"])),
        ("activities", lambda: workspace.activities(log["log_id"])),
        ("kpis", lambda: analytics.kpis(log["log_id"], sla_seconds=600, automation_resources=["user-1"])),
        ("conformance", lambda: analytics.conformance(log["log_id"], {
            "format": "openworkgraph.reference-model.v1",
            "paths": [["Receive", "Validate", "Review", "Approve", "Fix", "Check", "Invoice", "Pay", "Archive", "Close"]]})),
        ("root_cause", lambda: analytics.root_cause(log["log_id"], "rework")),
        ("filtered_kpis", lambda: analytics.kpis(log["log_id"], cohort={"activity": "Skip"})),
        ("benchmark", lambda: cohorts.benchmark(log["log_id"], [{"started_before": MIDPOINT}, {"started_from": MIDPOINT}],
                                                sla_seconds=600)),
    ):
        began = time.perf_counter()
        result = call()
        timings[name] = time.perf_counter() - began
        if name == "saved_graph":
            cases=EVENTS//10
            expected=cases-len(range(0,cases,7))-len(range(0,cases,5))+len(range(0,cases,35))
            assert result["cases"]==result["analyzed_cases"]==cases and result["unknown_cases"]==0
            assert result["structural_match_cases"]==expected
        if name == "case_query":
            assert result["selected_cases"] == EVENTS // 10 and len(result["cases"]) == 30
        if name == "variant_cases":
            assert result["selected_cases"] == len(range(0, EVENTS // 10, 35))
        if name == "case_events":
            assert result["total_events"] == 10 and result["next_offset"] == 3 and len(result["events"]) == 3
        if name == "summary":
            assert result["cases"] == EVENTS // 10
        if name == "variants":
            assert result["distinct_variants"] == 4  # skip x rework combinations
        if name == "conformance":
            assert result["aligned_cases"] == EVENTS // 10
        if name == "filtered_kpis":
            assert result["cases"] == len(range(0, EVENTS // 10, 7))
        if name == "benchmark":
            assert sum(c["cases"] for c in result["cohorts"]) == EVENTS // 10 and result["overlapping_cases"] == 0
        if name == "root_cause":
            assert result["associations"][0]["feature"] == {"kind": "activity", "value": "Check"}
    print(f"\nimport {EVENTS:,} events: {import_seconds:.1f}s; queries: "
          + ", ".join(f"{k} {v:.1f}s" for k, v in timings.items()))
    assert max(timings.values()) < QUERY_BUDGET_SECONDS, timings
