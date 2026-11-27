"""Exact organization repository portfolio accounting, paging and isolation."""
from collections import Counter
from copy import deepcopy
from itertools import combinations
import json
import random

import pytest
from pydantic import ValidationError

from shared import process_hierarchy_portfolio as core
from gateway.process_hierarchy_portfolio import RepositoryPortfolio
from gateway.process_hierarchies import ProcessHierarchies,namespace
from gateway.process_collaboration import ProcessCollaborationError as Error, ProcessArchiveWrite, ProcessDeleteWrite
from test_process_collaboration import store, person, write, grant, review
from test_process_hierarchies import process,pin,hierarchy,save_body


def report(store,**kwargs):
    return RepositoryPortfolio(store).report(person(),core.Query(**kwargs))


def test_real_catalog_coverage_reuse_and_mixed_revisions(store):
    maps=ProcessHierarchies(store)
    a,b,c,d,unused=[process(store,title) for title in ("Root A","Root B","Reusable","Parent","Unmapped")]
    wa,wb,wc,wd,wu=[r["workflow_id"] for r in (a,b,c,d,unused)]
    h=hierarchy(a,c,d)
    h["nodes"].append({"id":"nested","parent_id":"n2","process":pin(c)})
    maps.save(person(),wa,save_body(h))
    c2=store.save(person(),write("Reusable latest",1),wc)["process"]
    maps.save(person(),wb,save_body(hierarchy(b,c2,category="Finance",capability="Case management")))
    r=report(store)
    s=r["summary"]
    assert s["catalog_processes"]==5 and s["saved_maps"]==2
    assert s["declared_occurrences"]==6 and s["unique_referenced_processes"]==4
    assert s["distinct_exact_references"]==5
    assert s["active_catalog_processes_without_any_map_occurrence"]==1
    assert s["active_catalog_processes_without_own_root_map"]==3
    assert s["reused_across_maps_processes"]==1 and s["repeated_within_map_processes"]==1
    assert s["multiple_pin_processes"]==1 and s["overlapping_map_pairs"]==1
    assert s["reference_states"]=={"current":4,"revision_changed":2}
    assert s["maps_needing_reference_review"]==2
    rows={x["workflow_id"]:x for x in r["page"]["rows"]}
    assert rows[wc]["occurrences"]==3 and rows[wc]["map_count"]==2
    assert rows[wc]["current_title"]=="Reusable latest"
    assert rows[wc]["available_pinned_titles"]==["Reusable","Reusable latest"]
    assert rows[wc]["distinct_pins"]==2
    assert rows[wu]["occurrences"]==0 and rows[wu]["current_title"] is None
    assert [x["workflow_id"] for x in report(store,filter="unmapped")["page"]["rows"]]==[wu]
    evidence=report(store,view="occurrences",focus_workflow_id=wc,expected_fingerprint=r["fingerprint"])
    assert evidence["page"]["matching_rows"]==3
    assert {tuple(x["workflow_path"]) for x in evidence["page"]["rows"]}=={(wa,wc),(wa,wd,wc),(wb,wc)}
    assert len({x["content_sha256"] for x in evidence["page"]["rows"]})==2
    overlap=report(store,view="overlaps")["page"]["rows"][0]
    assert overlap["shared_process_count"]==1 and overlap["union_process_count"]==4
    assert overlap["jaccard_fraction"]=="1/4"
    categories=report(store,view="categories")["page"]["rows"]
    assert {x["label"]:x["unique_processes"] for x in categories}=={"Customer service":3,"Finance":2}
    capability=report(store,view="capabilities")["page"]["rows"][0]
    assert capability["unique_processes"]==4 and capability["occurrences"]==6
    assert r["complete_bounded_declared_scan"] and not r["benefit_rollup_included"]


def test_empty_catalog_and_unmapped_are_not_silently_counted_as_referenced(store):
    r=report(store)
    assert r["summary"]["saved_maps"]==0 and r["summary"]["unique_referenced_processes"]==0
    x=process(store,"Unmapped")
    r=report(store)
    assert r["summary"]["active_catalog_processes_without_any_map_occurrence"]==1
    assert r["summary"]["unique_referenced_processes"]==0
    assert r["page"]["rows"][0]["workflow_id"]==x["workflow_id"]
    assert not r["page"]["rows"][0]["is_map_root"]


def test_exact_paging_and_fresh_export_refusal_after_review_change(store):
    maps=ProcessHierarchies(store)
    root=process(store);child=process(store,"Child")
    wid=root["workflow_id"];cid=child["workflow_id"]
    maps.save(person(),wid,save_body(hierarchy(root,child)))
    first=report(store,view="occurrences",limit=1)
    assert first["page"]["next_offset"]==1
    next_page=report(store,view="occurrences",limit=1,offset=1,expected_fingerprint=first["fingerprint"])
    assert next_page["page"]["rows"][0]!=first["page"]["rows"][0]
    assert next_page["page"]["next_offset"] is None
    with pytest.raises(Error,match="first portfolio page"):
        report(store,offset=1)
    service=RepositoryPortfolio(store)
    with pytest.raises(Error,match="Review the exact"):
        service.report(person(),core.Query(),exporting=True)
    export=service.report(person(),core.Query(expected_fingerprint=first["fingerprint"]),exporting=True)
    assert export["fingerprint"]==first["fingerprint"]
    grant(store,cid,members=[{"actor_id":"bob","role":"reviewer"}])
    store.review(person("bob"),cid,review(child))
    with pytest.raises(Error,match="basis changed"):
        service.report(person(),core.Query(expected_fingerprint=first["fingerprint"]),exporting=True)
    fresh=report(store,view="occurrences",focus_workflow_id=cid)
    assert fresh["page"]["rows"][0]["model_review"]=="reviewed_design"
    assert not fresh["page"]["rows"][0]["needs_review"]


def test_archived_and_deleted_child_diagnostics_minimize_unavailable_labels(store):
    maps=ProcessHierarchies(store)
    root=process(store);child=process(store,"PRIVATE_CHILD_TITLE")
    wid=root["workflow_id"];cid=child["workflow_id"]
    maps.save(person(),wid,save_body(hierarchy(root,child)))
    store.archive(person(),cid,ProcessArchiveWrite(expected_access_revision=1,archived=True,user_confirmed=True))
    archived=report(store,view="occurrences",focus_workflow_id=cid)
    assert archived["page"]["rows"][0]["reference_state"]=="archived"
    store.delete(person(),cid,ProcessDeleteWrite(expected_revision=1,expected_access_revision=2,user_confirmed=True))
    r=report(store,view="occurrences",focus_workflow_id=cid)
    assert r["summary"]["unavailable_referenced_processes"]==1
    assert r["page"]["rows"][0]["reference_state"]=="unavailable"
    assert "PRIVATE_CHILD_TITLE" not in json.dumps(r)
    p=report(store,filter="unavailable")["page"]["rows"][0]
    assert p["workflow_id"]==cid and not p["available_pinned_titles"]
    store.archive(person(),wid,ProcessArchiveWrite(expected_access_revision=1,archived=True,user_confirmed=True))
    assert report(store,view="maps",filter="archived")["summary"]["archived_root_maps"]==1


def test_tenant_scope_and_counts_only_audit(store):
    maps=ProcessHierarchies(store);root=process(store,"INTERNAL_MAP_TITLE")
    wid=root["workflow_id"];h=hierarchy(root,title="SECRET_MAP_TITLE")
    maps.save(person(),wid,save_body(h))
    foreign=process(store,"Other org",person(org="other"))
    service=RepositoryPortfolio(store)
    other=service.report(person(org="other"),core.Query())
    assert other["summary"]["catalog_processes"]==1 and other["summary"]["saved_maps"]==0
    assert other["page"]["rows"][0]["workflow_id"]==foreign["workflow_id"]
    assert "SECRET_MAP_TITLE" not in json.dumps(other)
    with pytest.raises(Error):service.report(person(scopes=frozenset()),core.Query())
    same=service.report(person("reader",scopes=frozenset({"org:process:read"})),core.Query(view="maps"))
    assert same["page"]["rows"][0]["title"]=="SECRET_MAP_TITLE"
    with store.db.connect() as c:
        audits=store.db._execute(c,"SELECT details_json FROM audit_log WHERE action LIKE ?",("process.hierarchy.portfolio.%",)).fetchall()
    assert audits
    assert all("SECRET_MAP_TITLE" not in raw and "workflow_path" not in raw and "actor_id" not in raw for raw, in audits)


def test_corrupt_map_refuses_entire_scan_and_bound_refuses_partial_totals(store,monkeypatch):
    root=process(store);child=process(store,"Child")
    wid=root["workflow_id"]
    ProcessHierarchies(store).save(person(),wid,save_body(hierarchy(root,child)))
    monkeypatch.setattr(core,"MAX_REFERENCES",1)
    with pytest.raises(Error,match="bounded reference scan"):report(store)
    monkeypatch.setattr(core,"MAX_REFERENCES",1000)
    monkeypatch.setattr(core,"MAX_CATALOG",1)
    with pytest.raises(Error,match="bounded catalog scan"):report(store)
    monkeypatch.setattr(core,"MAX_CATALOG",1000)
    with store.db.connect() as c:
        store.db._execute(c,"UPDATE workflow_knowledge SET payload=? WHERE owner=? AND workflow_id=?",
                          ('{"broken":true}',namespace("acme"),wid))
    with pytest.raises(Error,match="integrity"):report(store)


def test_audit_failure_withholds_result(store,monkeypatch):
    process(store)
    def unavailable(**kwargs):raise RuntimeError("audit failure")
    monkeypatch.setattr(store.db,"audit",unavailable)
    with pytest.raises(RuntimeError,match="audit failure"):report(store)


def test_randomized_occurrence_and_set_oracle():
    rng=random.Random(741)
    for _ in range(120):
        ids=[f"p{i}" for i in range(12)]
        catalog={k:{"archived":rng.choice([True,False])} for k in ids}
        artifacts=[];resolutions={};expected=Counter();members={};pins={}
        for i in range(rng.randint(0,8)):
            root=ids[i];children=rng.sample([x for x in ids if x!=root],rng.randint(0,5))
            nodes=[]
            for j,k in enumerate([root,*children]):
                revision=rng.randint(1,3)
                ref={"workflow_id":k,"revision":revision,"content_sha256":str(revision)*64}
                nodes.append({"id":"root" if j==0 else f"n{j}","parent_id":None if j==0 else "root","process":ref})
                resolutions[core.ref_key(ref)]={**ref,"state":"current","available":True,
                   "title":k,"latest_revision":revision,"independent_model_review":"reviewed_design",
                   "active_latest_independently_reviewed_model":True}
                expected[k]+=1;pins.setdefault(k,set()).add(revision)
            members[root]=set([root,*children])
            artifacts.append({"root_workflow_id":root,"revision":1,"content_sha256":"a"*64,
                "hierarchy":{"title":root,"category":"c","capability":"cap","nodes":nodes}})
        p=core.build("org",catalog,artifacts,resolutions)
        s=p["summary"]
        assert s["declared_occurrences"]==sum(expected.values())
        assert s["unique_referenced_processes"]==len(expected)
        assert s["active_catalog_processes_without_any_map_occurrence"]==sum(not catalog[k]["archived"] and k not in expected for k in ids)
        assert s["multiple_pin_processes"]==sum(len(v)>1 for v in pins.values())
        assert s["reused_across_maps_processes"]==sum(v>1 for v in expected.values())
        overlap={(a,b):len(members[a]&members[b]) for a,b in combinations(sorted(members),2) if members[a]&members[b]}
        assert {(r["left_root"],r["right_root"]):r["shared_process_count"] for r in p["data"]["overlaps"]}==overlap
        assert p["fingerprint"]==core.build("org",catalog,list(reversed(artifacts)),resolutions)["fingerprint"]


@pytest.mark.parametrize("value",[{"limit":True},{"offset":-1},{"limit":51},{"view":"unknown"},{"execute":True}])
def test_strict_query(value):
    with pytest.raises(ValidationError):core.Query.model_validate(value)


def test_invalid_filter_and_unknown_focus_refused(store):
    process(store)
    with pytest.raises(Error,match="unsupported filter"):report(store,view="overlaps",filter="unmapped")
    with pytest.raises(Error,match="not part"):report(store,focus_workflow_id="unavailable")
