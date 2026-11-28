"""Synthetic OCEL snapshot comparison with independent literal-set oracle."""
from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_relationship_comparison as comparison
from server import event_log_workspace as workspace


@pytest.fixture(autouse=True)
def local_store(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def document(side):
    types = {"o":"order","i":"item","j":"item","c":"item",
             ("x" if side=="left" else "y"):"item"}
    if side=="right":
        types["c"]="delivery"
    links = [("o","i",""),("o","i","shared"),("i","o","reverse"),("o","c","conflicting")]
    if side=="left":
        links += [("o","j","left"),("o","x","missing"),("o","o","self")]
    else:
        links += [("o","j","right"),("o","y","missing"),("j","o","new_direction")]
    return {"objectTypes":[{"name":t,"attributes":[]} for t in sorted(set(types.values()))],
            "eventTypes":[{"name":"Open","attributes":[]}],
            "objects":[{"id":oid,"type":t,"attributes":[],
                        "relationships":[{"objectId":target,"qualifier":q}
                                         for source,target,q in links if source==oid]}
                       for oid,t in types.items()],
            "events":[{"id":"open","type":"Open","time":"2026-01-01T00:00:00Z",
                       "attributes":[],"relationships":[{"objectId":"o","qualifier":""}]}]}


def import_doc(doc):
    return workspace.import_log(BytesIO(json.dumps(doc).encode()),
                                name="Snapshot",source_format="ocel")["log_id"]


def pins(log):
    return {k:comparison.pin(log)[k] for k in
            ("revision","relationships_sha256","object_index_sha256")}


def settings(left,right,**kwargs):
    return {"other_log_id":right,"same_namespace_declared":True,
            "left":pins(left),"right":pins(right),**kwargs}


def pair():
    return import_doc(document("left")),import_doc(document("right"))


def test_literal_union_disjoint_statuses_and_paged_evidence():
    l,r=pair()
    spec=settings(l,r,limit=2)
    report=comparison.compare(l,spec)
    assert report["assertion_status_counts"]=={
        "both":3,"left_only":2,"right_only":2,"left_endpoint_missing":1,
        "right_endpoint_missing":1,"endpoint_type_conflict":1}
    assert report["filtered_union_count"]==report["page_count"]==10
    assert report["whole_object_index_counts"]=={
        "both_same_type":3,"left_only":1,"right_only":1,"type_conflict":1}
    rows=[]
    while True:
        rows.extend(report["rows"])
        if report["next_offset"] is None:
            break
        nxt=comparison.compare(l,{**spec,"offset":report["next_offset"]})
        assert nxt["selection_sha256"]==report["selection_sha256"]
        report=nxt
    def edges(doc):
        return {(o["id"],e["objectId"],e["qualifier"])
                for o in doc["objects"] for e in o["relationships"]}
    left,right=edges(document("left")),edges(document("right"))
    assert {(row["source_id"],row["target_id"],row["qualifier"]) for row in rows}==left|right
    assert len(rows)==len(left|right)
    for row in rows:
        key=(row["source_id"],row["target_id"],row["qualifier"])
        assert row["in_left"]==(key in left) and row["in_right"]==(key in right)
    conflict=next(row for row in rows if row["status"]=="endpoint_type_conflict")
    assert conflict["left_target_type"]=="item" and conflict["right_target_type"]=="delivery"
    assert conflict["in_left"] and conflict["in_right"]
    assert not report["actual_relationship_changes_verified"]
    assert not report["execution_authorized"]


def test_empty_qualifier_type_and_status_filter_keep_literal_semantics():
    l,r=pair()
    empty=comparison.compare(l,settings(l,r,qualifier=""))
    assert empty["page_count"]==1 and empty["rows"][0]["qualifier"]==""
    filtered=comparison.compare(l,settings(l,r,target_type="delivery"))
    assert filtered["page_count"]==1
    assert filtered["rows"][0]["status"]=="endpoint_type_conflict"
    status=comparison.compare(l,settings(l,r,status="right_only"))
    assert status["page_count"]==2 and status["filtered_union_count"]==10
    assert all(row["status"]=="right_only" for row in status["rows"])
    assert status["selection_sha256"]!=empty["selection_sha256"]


def test_reversed_comparison_is_symmetric_without_inferred_temporal_order():
    l,r=pair()
    a=comparison.compare(l,settings(l,r))
    b=comparison.compare(r,settings(r,l))
    invert={"both":"both","left_only":"right_only","right_only":"left_only",
            "left_endpoint_missing":"right_endpoint_missing",
            "right_endpoint_missing":"left_endpoint_missing",
            "endpoint_type_conflict":"endpoint_type_conflict"}
    assert {invert[k]:v for k,v in a["assertion_status_counts"].items()}==b["assertion_status_counts"]


def test_zero_assertions_and_missing_endpoints_remain_distinct():
    doc=document("left")
    for obj in doc["objects"]:
        obj["relationships"]=[]
    l=import_doc(doc)
    r=import_doc(document("left"))
    report=comparison.compare(l,settings(l,r))
    assert report["assertion_status_counts"]["right_only"]==7
    assert report["left"]["retained_relationships"]==0
    other=import_doc(doc)
    empty=comparison.compare(l,settings(l,other))
    assert empty["filtered_union_count"]==0 and empty["rows"]==[]


def test_append_or_deletion_invalidates_pins_without_silent_refresh():
    l,r=pair()
    spec=settings(l,r)
    workspace.append_log(r,BytesIO(json.dumps(document("right")).encode()),source_format="ocel")
    with pytest.raises(workspace.EventLogWorkspaceError,match="snapshot changed"):
        comparison.compare(l,spec)
    fresh=settings(l,r)
    workspace.delete_log(r)
    with pytest.raises(workspace.EventLogWorkspaceError):
        comparison.compare(l,fresh)


@pytest.mark.parametrize("patch",[
    {"same_namespace_declared":False},{"same_namespace_declared":1},
    {"limit":201},{"offset":-1},{"limit":True},{"status":"removed"},
    {"source_type":""},{"qualifier":"x"*81},{"unexpected":True},
])
def test_strict_review_and_bounds(patch):
    l,r=pair()
    with pytest.raises(workspace.EventLogWorkspaceError):
        comparison.compare(l,settings(l,r,**patch))


def test_same_log_and_unreviewed_digest_refused():
    l,r=pair()
    with pytest.raises(workspace.EventLogWorkspaceError,match="distinct"):
        comparison.compare(l,settings(l,l))
    spec=settings(l,r)
    spec["left"]["relationships_sha256"]="0"*64
    with pytest.raises(workspace.EventLogWorkspaceError,match="snapshot changed"):
        comparison.compare(l,spec)


def test_real_http_auth_strict_payload_and_page_assets(tmp_path):
    code=r"""
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from test_event_log_relationship_comparison import document
with TestClient(app) as client:
    h={"Authorization":"OWG-Session "+local_auth.create_dashboard_session()}
    stored={**h,"X-OWG-Local-Storage-Confirmed":"true"}
    bearer={"Authorization":"Bearer "+local_auth.ensure_api_token()}
    ids=[]
    for side in ("left","right"):
        response=client.post("/v1/event-logs/import?name=Snapshot&format=ocel",json=document(side),headers=stored)
        assert response.status_code==200,response.text
        ids.append(response.json()["log_id"])
    base=lambda id:"/v1/event-logs/"+id+"/object-relationships"
    pins=[]
    for id in ids:
        assert client.get(base(id)+"/snapshot",headers=bearer).status_code in (401,403)
        p=client.get(base(id)+"/snapshot",headers=h)
        assert p.status_code==200,p.text
        assert p.headers["cache-control"]=="no-store"
        pins.append({k:p.json()[k] for k in ("revision","relationships_sha256","object_index_sha256")})
    spec={"other_log_id":ids[1],"same_namespace_declared":True,"left":pins[0],"right":pins[1]}
    url=base(ids[0])+"/compare"
    assert client.post(url,json=spec,headers=bearer).status_code in (401,403)
    result=client.post(url,json=spec,headers=h)
    assert result.status_code==200,result.text
    assert result.headers["cache-control"]=="no-store"
    assert result.json()["filtered_union_count"]==10
    pairs=client.post(url,json={**spec,"mode":"pairs"},headers=h)
    assert pairs.status_code==200,pairs.text
    assert pairs.json()["filtered_pair_union_count"]==8
    assert client.post(url,json={**spec,"mode":"pairs"},headers=bearer).status_code in (401,403)
    assert client.post(url,json={**spec,"mode":"pairs","qualifier":""},headers=h).status_code==422
    assert client.post(url,content=b'{"limit":1,"limit":2}',headers={**h,"Content-Type":"application/json"}).status_code==422
    assert client.post(url,json={**spec,"same_namespace_declared":False},headers=h).status_code==422
    page=client.get("/object-relationship-comparison")
    assert page.status_code==200 and "Content-Security-Policy" in page.headers
    assert client.get("/object-relationship-comparison.js").status_code==200
    topo=base(ids[0])+"/topology"
    body={"snapshot":pins[0],"confirm":True,"view":"bridges"}
    assert client.post(topo,json=body,headers=bearer).status_code in (401,403)
    result=client.post(topo,json=body,headers=h)
    assert result.status_code==200,result.text
    assert result.headers["cache-control"]=="no-store"
    assert result.json()["counts"]["bridge_pairs"]==4
    assert result.json()["counts"]["single_assertion_bridges"]==3
    assert client.post(topo,json={**body,"confirm":False},headers=h).status_code==422
    assert client.get("/object-topology-review").status_code==200
    assert client.get("/object-topology-review.js").status_code==200
"""
    env={**os.environ,"WORKFLOW_OBSERVER_DATA":str(tmp_path/"data"),
         "WORKFLOW_OBSERVER_AUTH_DIR":str(tmp_path/"auth"),
         "WORKFLOW_OBSERVER_CONFIG":str(tmp_path/"config.json")}
    p=subprocess.run([sys.executable,"-c","import sys;sys.path.insert(0,'tests');"+code],
                     cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=120)
    assert p.returncode==0,p.stdout+p.stderr


def test_directed_pair_counts_and_exact_qualifier_set_oracle():
    l,r=pair()
    spec=settings(l,r,mode="pairs",limit=2)
    report=comparison.compare(l,spec)
    assert report["pair_status_counts"]=={
        "same_qualifier_set":2,"different_qualifier_set":1,"left_pair_only":1,
        "right_pair_only":1,"left_endpoint_missing":1,"right_endpoint_missing":1,
        "endpoint_type_conflict":1}
    assert report["filtered_pair_union_count"]==8
    assert report["comparable_pairs"]==5 and report["uncertain_pairs"]==3
    assert report["comparable_pair_overlap"]==pytest.approx(3/5)
    assert report["equal_qualifier_set_share_of_common_comparable_pairs"]==pytest.approx(2/3)
    assert report["literal_assertion_counts"]=={"left":7,"right":7,"common":4,"left_only":3,"right_only":3}
    rows=[]
    while True:
        rows.extend(report["rows"])
        if report["next_offset"] is None:break
        nxt=comparison.compare(l,{**spec,"offset":report["next_offset"]})
        assert nxt["selection_sha256"]==report["selection_sha256"]
        report=nxt
    assert len(rows)==len({(x["source_id"],x["target_id"]) for x in rows})==8
    def sets(doc):
        result={}
        for o in doc["objects"]:
            for rel in o["relationships"]:
                result.setdefault((o["id"],rel["objectId"]),set()).add(rel["qualifier"])
        return result
    left,right=sets(document("left")),sets(document("right"))
    for row in rows:
        key=(row["source_id"],row["target_id"])
        a,b=left.get(key,set()),right.get(key,set())
        assert (row["left_qualifiers"],row["right_qualifiers"],row["common_qualifiers"],
                row["left_only_qualifiers"],row["right_only_qualifiers"])==(len(a),len(b),len(a&b),len(a-b),len(b-a))
    assert not report["actual_relationship_changes_verified"]


def test_pair_status_filter_does_not_change_population_and_exact_drilldown():
    l,r=pair()
    report=comparison.compare(l,settings(l,r,mode="pairs",pair_status="different_qualifier_set"))
    assert report["page_count"]==1 and report["filtered_pair_union_count"]==8
    row=report["rows"][0]
    assert (row["source_id"],row["target_id"])==("o","j")
    detail=comparison.compare(l,settings(l,r,source_id="o",target_id="j"))
    assert detail["page_count"]==2
    assert {x["qualifier"] for x in detail["rows"]}=={"left","right"}
    reverse=comparison.compare(l,settings(l,r,source_id="j",target_id="o"))
    assert reverse["page_count"]==1 and reverse["rows"][0]["qualifier"]=="new_direction"


def test_pair_mode_rejects_incomplete_qualifier_sets_and_invalid_mode():
    l,r=pair()
    for patch in ({"mode":"pairs","qualifier":""},{"mode":"pairs","status":"both"},
                  {"pair_status":"same_qualifier_set"},{"mode":"unknown"},{"source_id":""},
                  {"target_id":"x"*201}):
        with pytest.raises(workspace.EventLogWorkspaceError):
            comparison.compare(l,settings(l,r,**patch))


def test_pair_empty_and_reverse_symmetry():
    l,r=pair()
    a=comparison.compare(l,settings(l,r,mode="pairs"))
    b=comparison.compare(r,settings(r,l,mode="pairs"))
    swap={"left_pair_only":"right_pair_only","right_pair_only":"left_pair_only",
          "left_endpoint_missing":"right_endpoint_missing","right_endpoint_missing":"left_endpoint_missing"}
    assert {swap.get(k,k):v for k,v in a["pair_status_counts"].items()}==b["pair_status_counts"]
    e=comparison.compare(l,settings(l,r,mode="pairs",source_id="absent"))
    assert e["filtered_pair_union_count"]==0 and e["rows"]==[]
    assert e["comparable_pair_overlap"] is None
    assert e["equal_qualifier_set_share_of_common_comparable_pairs"] is None


def test_pair_mode_stale_pins_refused():
    l,r=pair()
    spec=settings(l,r,mode="pairs")
    workspace.append_log(r,BytesIO(json.dumps(document("right")).encode()),source_format="ocel")
    with pytest.raises(workspace.EventLogWorkspaceError,match="snapshot changed"):
        comparison.compare(l,spec)
