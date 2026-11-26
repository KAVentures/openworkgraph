"""Deadline sensitivity matches exact compliance semantics, including repeated A."""
from copy import deepcopy
from datetime import datetime, timedelta
from io import BytesIO
import json
import os
from pathlib import Path
import random
import subprocess
import sys

import duckdb
import pytest

from server import event_log_compliance as compliance
from server import event_log_domain_pack_deadlines as deadlines
from server import event_log_domain_packs as packs
from server import event_log_query as query
from server import event_log_workspace as workspace
from order_to_cash_fixture import MAPPING, NAMES, TYPES, order_to_cash

PACK = "order_to_cash"


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def load():
    return workspace.import_log(BytesIO(json.dumps(order_to_cash()).encode()),
                                name="o2c", source_format="ocel")["log_id"]


def installed():
    log = load()
    saved = packs.install(log, PACK, {"version": "1.0.0", "mapping": deepcopy(MAPPING)})
    return log, saved


def test_exact_order_deadlines_and_no_mutation():
    log, saved = installed()
    before = query.list_metrics(log)
    spec = {"parameter": "invoice_within_days", "values": [20, 2, 3, 14]}
    result = deadlines.review(log, PACK, spec)
    assert result["basis"]["baseline_value"] == 14
    assert result["basis"]["parameter_unit"] == "days"
    assert result["configuration_modified"] is False
    assert result["policy_compliance_verified"] is False
    assert len(result["rules"]) == 1
    rule = result["rules"][0]
    assert rule["case_type"] == TYPES["order"]
    assert rule["baseline"]["activated_cases"] == 20
    assert rule["baseline"]["violating_cases"] == 2
    assert rule["baseline"]["missing_response_cases"] == 0
    values = {r["value"]: r for r in rule["alternatives"]}
    assert [values[k]["violating_cases"] for k in (2, 3, 14, 20)] == [20, 2, 2, 0]
    assert values[2]["newly_violating_cases"] == 18
    assert values[20]["newly_satisfied_cases"] == 2
    assert values[14]["violation_count_change"] == 0
    assert result == deadlines.review(log, PACK, {**spec, "values": list(reversed(spec["values"]))})
    assert packs.installed(log)["installs"][0]["installation_token"] == saved["installation_token"]
    assert query.list_metrics(log) == before


def evidence_request(result, spec, value, state, after=None, limit=1):
    return {**spec, "expected_basis_sha256": result["basis_sha256"],
            "evidence": {"rule_index": result["rules"][0]["rule_index"], "value": value,
                         "state": state, "after": after, "limit": limit}}


def test_missing_responses_and_case_pagination():
    log, _ = installed()
    spec = {"parameter": "payment_terms_days", "values": [5, 22, 60]}
    result = deadlines.review(log, PACK, spec)
    r = result["rules"][0]
    assert r["baseline"]["violating_cases"] == 1
    alternatives = {a["value"]: a for a in r["alternatives"]}
    assert alternatives[5]["missing_response_cases"] == 1
    assert alternatives[5]["late_complete_response_cases"] == 17
    assert alternatives[5]["newly_violating_cases"] == 17
    assert alternatives[60]["violating_cases"] == 1
    missing = deadlines.review(log, PACK, evidence_request(result, spec, 60, "missing_response"))
    assert missing["total"] == 1 and missing["cases"][0]["case_id"] == "bd9"
    assert missing["cases"][0]["unanswered_occurrences"] == 1
    assert missing["next_after"] is None
    ids, after = [], None
    while True:
        page = deadlines.review(log, PACK, evidence_request(result, spec, 5, "newly_violating", after, 4))
        assert page["total"] == 17 and page["basis_sha256"] == result["basis_sha256"]
        ids += [x["case_id"] for x in page["cases"]]
        after = page["next_after"]
        if after is None:
            break
    assert ids == sorted("bd" + str(k) for k in range(20) if k not in (3, 9, 13))


def test_review_pin_refuses_changed_installation():
    log, _ = installed()
    spec = {"parameter": "invoice_within_days", "values": [20]}
    result = deadlines.review(log, PACK, spec)
    packs.install(log, PACK, {"version": "1.0.0", "mapping": deepcopy(MAPPING),
                              "parameters": {"invoice_within_days": 10}})
    with pytest.raises(workspace.EventLogWorkspaceError, match="retained source changed"):
        deadlines.review(log, PACK, evidence_request(result, spec, 20, "newly_satisfied"))


def test_review_pin_refuses_source_append():
    log, _ = installed()
    spec = {"parameter": "invoice_within_days", "values": [20]}
    result = deadlines.review(log, PACK, spec)
    doc = order_to_cash()
    for obj in doc["objects"]:
        obj["id"] = "new-" + obj["id"]
    for event in doc["events"]:
        event["id"] = "new-" + event["id"]
        for link in event["relationships"]:
            link["objectId"] = "new-" + link["objectId"]
    workspace.append_log(log, BytesIO(json.dumps(doc).encode()), source_format="ocel")
    with pytest.raises(workspace.EventLogWorkspaceError, match="retained source changed"):
        deadlines.review(log, PACK, evidence_request(result, spec, 20, "newly_satisfied"))


@pytest.mark.parametrize("spec", [
    {"parameter": "invoice_within_days", "values": [True]},
    {"parameter": "invoice_within_days", "values": [float("nan")]},
    {"parameter": "invoice_within_days", "values": [0]},
    {"parameter": "invoice_within_days", "values": [1, 1]},
    {"parameter": "invoice_within_days", "values": [1, 1.00000001]},
    {"parameter": "invoice_within_days", "values": list(range(1, 10))},
    {"parameter": "unknown", "values": [1]},
    {"parameter": "invoice_within_days", "values": [1], "extra": True},
])
def test_invalid_requests_are_refused(spec):
    log, _ = installed()
    with pytest.raises(workspace.EventLogWorkspaceError):
        deadlines.review(log, PACK, spec)


def test_no_partial_results_over_source_budget(monkeypatch):
    log, _ = installed()
    monkeypatch.setattr(deadlines, "MAX_SOURCE_ROWS", 1)
    with pytest.raises(workspace.EventLogWorkspaceError, match="source-row budget"):
        deadlines.review(log, PACK, {"parameter": "invoice_within_days", "values": [20]})


def test_window_profile_matches_existing_rule_and_independent_oracle():
    rng = random.Random(138)
    conn = duckdb.connect()
    try:
        conn.execute("CREATE TABLE ce(case_id VARCHAR,activity VARCHAR,resource VARCHAR,ts TIMESTAMP,event_id VARCHAR)")
        cases = {}
        start = datetime(2026, 1, 1)
        for k in range(150):
            trace = sorted([(rng.randrange(7), str(j).zfill(3), rng.choice("ABX"))
                            for j in range(rng.randrange(1, 12))])
            cases[str(k)] = trace
            conn.executemany("INSERT INTO ce VALUES (?,?,?,?,?)",
                             [(str(k), a, "", start+timedelta(seconds=t), e) for t,e,a in trace])
        deadlines._profile(conn, "A", "B")
        worst = {}
        for key, trace in cases.items():
            gaps = [next((u-t for u,_,b in trace[i+1:] if b=="B"), None)
                    for i,(t,_,a) in enumerate(trace) if a=="A"]
            worst[key] = (len(gaps), sum(g is None for g in gaps), max((g for g in gaps if g is not None), default=None))
        assert {k:(n,m,w) for k,n,m,w in conn.execute(
            "SELECT case_id,triggers,missing,worst_seconds FROM deadline_profile").fetchall()} == worst
        for threshold in (1, 2, 3, 6, 20):
            row = deadlines._counts(conn, 3, threshold)
            existing, _ = compliance.evaluate_rule(conn, compliance.Within(template="response_within", a="A", b="B", seconds=threshold))
            assert row["violating_cases"] == existing["violating_cases"]
            assert row["activated_cases"] == existing["activated_cases"]
            assert row["satisfied_cases"] == existing["satisfied_cases"]
            assert row["missing_response_cases"] == sum(m>0 for _,m,_ in worst.values())
            assert row["newly_satisfied_cases"] == sum(n>0 and m==0 and 3<w<=threshold for n,m,w in worst.values())
            assert row["newly_violating_cases"] == sum(n>0 and m==0 and threshold<w<=3 for n,m,w in worst.values())
    finally:
        conn.close()


def test_http_owner_scope_and_strict_json(tmp_path):
    code = r"""
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from test_event_log_domain_pack_deadlines import installed,PACK
with TestClient(app) as c:
    h={"Authorization":"OWG-Session "+local_auth.create_dashboard_session()}
    bearer={"Authorization":"Bearer "+local_auth.ensure_api_token()}
    log,_=installed()
    url="/v1/event-logs/"+log+"/domain-packs/"+PACK+"/deadline-sensitivity"
    spec={"parameter":"invoice_within_days","values":[20]}
    assert c.post(url,json=spec,headers=bearer).status_code in (401,403)
    r=c.post(url,json=spec,headers=h)
    assert r.status_code==200,r.text
    assert r.headers["cache-control"]=="no-store"
    assert r.json()["configuration_modified"] is False
    assert c.post(url,content=b'{"parameter":"x","parameter":"y","values":[20]}',headers={**h,"Content-Type":"application/json"}).status_code==422
"""
    env={**os.environ,"WORKFLOW_OBSERVER_DATA":str(tmp_path/"data"),
         "WORKFLOW_OBSERVER_AUTH_DIR":str(tmp_path/"auth"),
         "WORKFLOW_OBSERVER_CONFIG":str(tmp_path/"config.json")}
    result=subprocess.run([sys.executable,"-c","import sys;sys.path.insert(0,'tests');"+code],
        cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stdout+result.stderr
