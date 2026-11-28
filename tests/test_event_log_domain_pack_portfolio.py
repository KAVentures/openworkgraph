"""Portfolio benchmark regression, using the existing real CSV/OCEL fixtures."""
from copy import deepcopy
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_domain_packs as packs
from server import event_log_domain_pack_benchmark as benchmark
from server import event_log_domain_pack_portfolio as portfolio
from server import event_log_workspace as workspace
from test_event_log_domain_pack_benchmark import installed, PACK
from service_management_fixture import incidents, MAPPING, CSV_MAPPING


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def preview(a, *others):
    return portfolio.preflight(a, PACK, {"other_log_ids": list(others)})


def compare(a, pin, *others):
    return portfolio.compare(a, PACK, {"other_log_ids": list(others),
                                      "expected_preflight_sha256": pin["preflight_sha256"]})


def test_portfolio_matches_pairwise_results_and_computes_once(monkeypatch):
    a, _ = installed()
    b, _ = installed(rename=True)
    c, _ = installed()
    pin = preview(a, b, c)
    assert pin["ready_to_compare"]
    assert pin == preview(a, c, b)
    assert pin["identical_content_pairs"] == [
        {"left_log_id": a, "right_log_id": c}]
    expected = {}
    for other in (b, c):
        p = benchmark.preflight(a, PACK, {"other_log_id": other})
        expected[other] = benchmark.compare(a, PACK, {"other_log_id": other,
                                                      "expected_preflight_sha256": p["preflight_sha256"]})
    original = packs.report
    calls = []
    def counted(log, pack):
        calls.append(log)
        return original(log, pack)
    monkeypatch.setattr(packs, "report", counted)
    result = compare(a, pin, c, b)
    assert calls == [a, *sorted([b, c])]
    assert result["source_reports_computed"] == 3
    for section, pair_key in (("metrics", "metrics"), ("compliance", "case_rules"),
                              ("object_rules", "object_rules")):
        for index, row in enumerate(result[section]):
            assert row["definition_index"] == index
            for j, other in enumerate(calls[1:]):
                pair = expected[other][pair_key][index]
                assert row["values"][0] == pair["baseline"]
                assert row["values"][j+1] == pair["comparison"]
                delta = row["baseline_comparisons"][j]
                assert delta["log_id"] == other
                for field in ("comparable", "not_comparable_reason", "changes"):
                    assert delta[field] == pair[field]
    assert "INC0003" not in json.dumps(result)
    assert result["pooled_populations_reported"] is False
    assert result["raw_case_identifiers_returned"] is False
    assert result["disjoint_populations_verified"] is False
    assert result["execution_authorized"] is False
    assert compare(a, pin, b, c)["report_sha256"] == result["report_sha256"]


def test_optional_missing_roles_remain_unavailable_per_source():
    a, _ = installed()
    b, spec = installed(rename=True)
    c, _ = installed()
    del spec["mapping"]["activities"]["reopen"]
    packs.install(b, PACK, spec)
    pin = preview(a, b, c)
    result = compare(a, pin, b, c)
    index = [s["log_id"] for s in result["sources"]].index(b)
    rows = result["metrics"] + result["compliance"]
    omitted = [r for r in rows if r["values"][index] is None]
    assert omitted
    for row in omitted:
        delta = row["baseline_comparisons"][index-1]
        assert delta["not_comparable_reason"] == "unmapped_definition"
        assert delta["changes"] is None
        assert row["mapped_source_count"] == 2


def test_incompatible_source_blocks_whole_portfolio():
    a, _ = installed()
    b, _ = installed(rename=True)
    c, spec = installed()
    p = packs.definitions(PACK)["parameters"][0]
    packs.install(c, PACK, {**spec, "parameters": {p["name"]: p["default"]+1}})
    pin = preview(a, b, c)
    assert not pin["ready_to_compare"]
    assert pin["blockers"] == [{"log_id": c, "reasons": ["different_parameter_values"]}]
    with pytest.raises(workspace.EventLogWorkspaceError, match="same pack version and parameters"):
        compare(a, pin, b, c)


def test_all_sources_pinned_for_report():
    a, _ = installed()
    b, _ = installed(rename=True)
    c, spec = installed()
    pin = preview(a, b, c)
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            conn.execute("UPDATE events SET resource='changed' WHERE log_id=?", [c])
        finally:
            conn.close()
    with pytest.raises(workspace.EventLogWorkspaceError, match="source changed"):
        compare(a, pin, b, c)
    pin = preview(a, b, c)
    packs.install(c, PACK, spec)
    with pytest.raises(workspace.EventLogWorkspaceError, match="source changed"):
        compare(a, pin, b, c)


@pytest.mark.parametrize("spec", [
    {"other_log_ids": []},
    {"other_log_ids": ["one"]},
    {"other_log_ids": ["one", "one"]},
    {"other_log_ids": ["base", "one"]},
    {"other_log_ids": ["one", 2]},
    {"other_log_ids": [str(i) for i in range(8)]},
    {"other_log_ids": ["one", "two"], "extra": True},
])
def test_bad_selections_refused_before_source_access(spec):
    with pytest.raises(workspace.EventLogWorkspaceError):
        portfolio.preflight("base", PACK, spec)


def test_missing_installation_and_total_work_bound(monkeypatch):
    a, _ = installed()
    b, _ = installed(rename=True)
    c = workspace.import_log(BytesIO(incidents().encode()), name="uninstalled",
                             source_format="csv", mapping=CSV_MAPPING)["log_id"]
    with pytest.raises(workspace.EventLogWorkspaceError, match="Install"):
        preview(a, b, c)
    packs.install(c, PACK, {"version": "1.0.0", "mapping": deepcopy(MAPPING)})
    monkeypatch.setattr(portfolio, "MAX_SOURCE_ROWS", 1)
    with pytest.raises(workspace.EventLogWorkspaceError, match="source-row budget"):
        preview(a, b, c)


def test_shared_benchmark_guard():
    assert benchmark._GUARD.acquire(blocking=False)
    try:
        with pytest.raises(workspace.EventLogWorkspaceError, match="Another domain benchmark"):
            preview("base", "one", "two")
    finally:
        benchmark._GUARD.release()


def test_dashboard_only_strict_http(tmp_path):
    code = r"""
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from test_event_log_domain_pack_benchmark import installed, PACK
a,_=installed();b,_=installed(rename=True);c,_=installed()
url=f"/v1/event-logs/{a}/domain-packs/{PACK}/portfolio"
spec={"other_log_ids":[b,c]}
with TestClient(app) as client:
    h={"Authorization":"OWG-Session "+local_auth.create_dashboard_session()}
    bearer={"Authorization":"Bearer "+local_auth.ensure_api_token()}
    assert client.post(url+"/preview",json=spec,headers=bearer).status_code in (401,403)
    r=client.post(url+"/preview",json=spec,headers=h)
    assert r.status_code==200,r.text
    req={**spec,"expected_preflight_sha256":r.json()["preflight_sha256"]}
    assert client.post(url,json=req,headers=bearer).status_code in (401,403)
    r=client.post(url,json=req,headers=h)
    assert r.status_code==200,r.text
    assert r.headers["cache-control"]=="no-store"
    assert r.json()["source_reports_computed"]==3
    assert client.post(url,json={**req,"expected_preflight_sha256":"0"*64},headers=h).status_code==422
    assert client.post(url+"/preview",content=b'{"other_log_ids":[],"other_log_ids":[]}',headers={**h,"Content-Type":"application/json"}).status_code==422
"""
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "WORKFLOW_OBSERVER_DATA": str(tmp_path/"data"),
           "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path/"auth"),
           "WORKFLOW_OBSERVER_CONFIG": str(tmp_path/"config.json"),
           "PYTHONPATH": str(root/"tests")+os.pathsep+str(root)}
    done = subprocess.run([sys.executable, "-c", code], cwd=root, env=env,
                          text=True, capture_output=True, timeout=120)
    assert done.returncode == 0, done.stdout+done.stderr
