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
    assert r["withheld"]["min_cases"] == 5 and "c0" not in repr(r)


def test_compliance_rates_with_case_ids_only_at_cases_level():
    log = _log()
    sharing.set_share(log, "aggregates")
    rules = [{"template": "response", "a": "Pay", "b": "Ship"},
             {"template": "segregation_of_duties", "a": "Check", "b": "Pay"}]
    r = sharing.compliance(log, rules)
    response, sod = r["results"]
    assert (response["activated_cases"], response["violating_cases"]) == (15, 3)
    assert sod["violating_cases"] == 2 and r["case_ids_returned"] is False
    assert "violating_case_examples" not in response and "ann" not in repr(r)
    sharing.set_share(log, "cases")
    full = sharing.compliance(log, rules)
    assert full["case_ids_returned"] is True
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
    assert r["case_lines_returned"] is False and "c0" not in repr(r)
    chosen = sharing.performance_spectrum(log, segments=["Order->Cancel"], bins=4)
    assert [(s["from"], s["occurrences"]) for s in chosen["segments"]] == [("Order", 9)]
    with pytest.raises(EventLogWorkspaceError):
        sharing.performance_spectrum(log, segments=["Ship->Order"])


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
    assert c.status_code==200 and c.json()['results'][0]['violating_cases']==4, c.text
    assert client.post(base+'/compliance',json={"rules":[],"x":1},headers=bearer).status_code==422
    s=client.get(base+'/performance-spectrum?segment=A->B&bins=2',headers=bearer)
    assert s.status_code==200 and s.json()['segments'][0]['occurrences']==10, s.text
    assert client.get(base+'/trace-clusters?k=13',headers=bearer).status_code==422
    assert client.post(base+'/compliance',json=body).status_code in (401,403)
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)
