"""Agents can read trace clusters, check compliance rules and read the performance spectrum of a shared log,
with small clusters, variants and segments withheld and case IDs only at the cases level."""
from __future__ import annotations

import os
import subprocess
import sys
from io import BytesIO

import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time",
           "resource_column": "who"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path / "auth"))
    (tmp_path / "auth").mkdir()
    monkeypatch.delenv("OWG_PROCESS_INTELLIGENCE_TOOLS", raising=False)


def _log():
    """24 cases, a day apart: 12 Order>Check>Pay>Ship, 9 Order>Cancel>Refund, 3 Order>Check>Pay (no Ship).
    Pay is done by the same person as Check in cases c0 and c1."""
    rows = ["case,activity,time,who"]
    for i in range(24):
        steps = (["Order", "Check", "Pay", "Ship"] if i < 12 else
                 ["Order", "Cancel", "Refund"] if i < 21 else ["Order", "Check", "Pay"])
        for j, a in enumerate(steps):
            who = "ann" if a == "Check" or (a == "Pay" and i < 2) else f"r{j}"
            rows.append(f"c{i},{a},2024-02-{1 + i:02d}T{8 + j * (1 + i % 3):02d}:00:00Z,{who}")
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="k", source_format="csv", mapping=MAPPING)["log_id"]


def test_trace_clusters_withhold_small_groups_and_cohorts():
    log = _log()
    with pytest.raises(sharing.NotShared):
        sharing.trace_clusters(log)
    sharing.set_share(log, "aggregates")
    r = sharing.trace_clusters(log, k=2)
    assert r["k"] == 2 and r["format"] == sharing.FORMAT
    assert all(c["cases"] >= 5 and "cohort" not in c for c in r["clusters"])
    assert all(v["cases"] >= 5 for c in r["clusters"] for v in c["top_variants"])
    assert sum(c["top_variants_withheld"] for c in r["clusters"]) >= 1   # the 3-case variant
    assert r["withheld"]["min_cases"] == 5 and "'c0'" not in repr(r)


def test_compliance_rates_with_case_ids_only_at_cases_level():
    log = _log()
    sharing.set_share(log, "aggregates")
    rules = [{"template": "response", "a": "Pay", "b": "Ship"},
             {"template": "segregation_of_duties", "a": "Check", "b": "Pay"},
             {"template": "response", "a": "Order", "b": "Check"}]
    r = sharing.compliance(log, rules)
    response, sod, checked = r["results"]
    # 3 and 2 violating cases would describe a handful of cases: withheld at the aggregates level.
    assert response["withheld"] and response["violating_cases"] is None and response["violation_rate"] is None
    assert sod["withheld"] and sod["activated_cases"] is None and r["withheld"]["rules"] == 2
    assert (checked["activated_cases"], checked["violating_cases"]) == (24, 9) and "withheld" not in checked
    assert r["rules_with_violations"] == 1 and r["case_ids_returned"] is False
    assert "violating_case_examples" not in checked and "ann" not in repr(r)
    sharing.set_share(log, "cases")
    full = sharing.compliance(log, rules)
    assert full["case_ids_returned"] is True and full["withheld"]["rules"] == 0
    assert (full["results"][0]["activated_cases"], full["results"][0]["violating_cases"]) == (15, 3)
    assert {e["case_id"] for e in full["results"][1]["violating_case_examples"]} == {"c0", "c1"}
    for bad in ([], None, [{"template": "bogus", "a": "x"}]):
        with pytest.raises(EventLogWorkspaceError):
            sharing.compliance(log, bad)


def test_performance_spectrum_without_case_lines():
    log = _log()
    sharing.set_share(log, "cases")
    r = sharing.performance_spectrum(log, top=12, bins=4)
    names = {(s["from"], s["to"]) for s in r["segments"]}
    assert ("Order", "Check") in names and ("Pay", "Ship") in names
    assert all(s["occurrences"] >= 5 and "lines" not in s and "max" not in s["duration_seconds"] for s in r["segments"])
    assert r["case_lines_returned"] is False and "'c0'" not in repr(r)
    chosen = sharing.performance_spectrum(log, segments=["Order->Cancel"], bins=4)
    assert [(s["from"], s["occurrences"]) for s in chosen["segments"]] == [("Order", 9)]
    with pytest.raises(EventLogWorkspaceError):
        sharing.performance_spectrum(log, segments=["Ship->Order"])


def test_rare_activity_contrast_hidden_even_in_large_cluster(monkeypatch):
    log = _log()
    sharing.set_share(log, "aggregates")
    from server import event_log_trace_clusters as clustering
    original = clustering.clusters

    def with_rare_contrast(*args, **kwargs):
        result = original(*args, **kwargs)
        if result["clusters"]:
            result["clusters"][0]["more_common_activities"].append({
                "activity": "rare-private-activity", "cases_in_cluster": 1, "cases_in_log": 1,
                "case_share_in_cluster": 0.1, "case_share_in_log": 0.04,
                "lift": 2.5, "difference": 0.06,
            })
        return result

    monkeypatch.setattr(clustering, "clusters", with_rare_contrast)
    result = sharing.trace_clusters(log, k=2)
    assert "rare-private-activity" not in repr(result)
    for cluster in result["clusters"]:
        for contrast in cluster["more_common_activities"] + cluster["less_common_activities"]:
            assert "cases_in_cluster" not in contrast and "cases_in_log" not in contrast


def test_spectrum_requires_distinct_case_support_and_suppresses_small_bins():
    import csv
    import io
    from io import BytesIO
    table = io.StringIO()
    writer = csv.writer(table)
    writer.writerow(["case", "activity", "time", "who"])
    # One case repeats A->B six times. Occurrence count is high, case count is one.
    for i in range(6):
        writer.writerow(["repeat", "A", f"2024-01-01T{2 * i:02d}:00:00Z", "a"])
        writer.writerow(["repeat", "B", f"2024-01-01T{2 * i+1:02d}:00:00Z", "a"])
    # Six independent X->Y cases, distributed across days (sparse time bins).
    for i in range(6):
        writer.writerow([f"c{i}", "X", f"2024-01-0{i+2}T08:00:00Z", "a"])
        writer.writerow([f"c{i}", "Y", f"2024-01-0{i+2}T09:00:00Z", "a"])
    log = workspace.import_log(BytesIO(table.getvalue().encode()), name="sparse",
        source_format="csv", mapping=MAPPING)["log_id"]
    sharing.set_share(log, "aggregates")
    rare = sharing.performance_spectrum(log, segments=["A->B"], bins=4)
    assert rare["segments"] == [] and rare["withheld"]["segments"] == 1
    result = sharing.performance_spectrum(log, segments=["X->Y"], bins=4)
    assert len(result["segments"]) == 1
    assert result["segments"][0]["bins"] == []
    assert result["segments"][0]["bins_withheld_for_small_cells"] is True
    assert result["time_origin"] is None and result["bin_seconds"] is None
    assert "log" not in result and result["case_lines_returned"] is False
    sharing.set_share(log, "cases")
    full = sharing.performance_spectrum(log, segments=["X->Y"], bins=4)
    assert len(full["segments"][0]["bins"]) == 4
    assert "lines" not in full["segments"][0]



def test_agent_routes(tmp_path):
    code = r'''
from io import BytesIO
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, event_log_workspace as workspace, event_log_agent_sharing as sharing
rows = ["case,activity,time"] + [f"c{i},{a},2024-03-{1+i:02d}T0{j}:00:00Z" for i in range(10) for j, a in enumerate("AB" + ("C" if i < 6 else "D"))]
log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="q", source_format="csv",
    mapping={"case_column":"case","activity_column":"activity","timestamp_column":"time"})["log_id"]
with TestClient(app) as client:
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    base='/v1/process-intelligence/'+log
    assert client.get(base+'/trace-clusters',headers=bearer).status_code==404
    sharing.set_share(log,'aggregates')
    r=client.get(base+'/trace-clusters?k=2',headers=bearer)
    assert r.status_code==200 and r.headers['cache-control']=='no-store', r.text
    assert sorted(c['cases'] for c in r.json()['clusters'])==[6], r.json()
    body={"rules":[{"template":"existence","a":"C","min":1}]}
    c=client.post(base+'/compliance',json=body,headers=bearer)
    assert c.status_code==200 and c.json()['results'][0]['withheld'] is True, c.text   # 4 violating cases
    assert client.post(base+'/compliance',json={"rules":[],"x":1},headers=bearer).status_code==422
    s=client.get(base+'/performance-spectrum?segment=A->B&bins=2',headers=bearer)
    assert s.status_code==200 and s.json()['segments'][0]['occurrences']==10, s.text
    assert client.get(base+'/trace-clusters?k=13',headers=bearer).status_code==422
    assert client.post(base+'/compliance',json=body).status_code in (401,403)
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)
