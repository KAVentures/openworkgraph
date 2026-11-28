from random import Random

from shared.ocel_topology import analyze


def components(nodes, edges):
    unseen=set(nodes); result=[]
    while unseen:
        frontier=[min(unseen)]; group=set(frontier); unseen.difference_update(group)
        while frontier:
            u=frontier.pop()
            fresh={v for a,b in edges for v in ([b] if a==u else [a] if b==u else []) if v in unseen}
            group.update(fresh);unseen.difference_update(fresh);frontier.extend(fresh)
        result.append(group)
    return result


def test_random_oracle():
    rng=Random(1702)
    for _ in range(600):
        n=rng.randrange(0,11); nodes=[str(i) for i in range(n)]; assertions=[]
        for a in nodes:
            for b in nodes:
                if rng.random()<.22:
                    assertions.append((a,b,'x'))
                    if rng.random()<.3:assertions.append((a,b,'y'))
        result=analyze([(a,'type') for a in nodes],assertions)
        edges={tuple(sorted((a,b))) for a,b,_ in assertions if a!=b}
        groups=components(nodes,edges)
        cuts={}; bridges={}
        for group in groups:
            for u in group:
                parts=components(group-{u},{e for e in edges if u not in e})
                if len(parts)>1:cuts[u]=sum(len(p)*len(q) for i,p in enumerate(parts) for q in parts[i+1:])
            for edge in edges:
                if set(edge)<=group:
                    parts=components(group,edges-{edge})
                    if len(parts)>1:bridges[edge]=len(parts[0])*len(parts[1])
        assert {r['object_id']:r['disconnected_surviving_pairs'] for r in result['objects']}==cuts
        assert {(r['left_id'],r['right_id']):r['disconnected_pairs'] for r in result['bridges']}==bridges
        assert result['counts']['weak_components']==len(groups)
        assert result['counts']['literal_assertions']==len(assertions)
        assert result['counts']['projected_pairs']==len(edges)
        for b in result['bridges']:
            assert b['assertions_in_pair']==sum({a,c}=={b['left_id'],b['right_id']} for a,c,_ in assertions)


def test_long_chain():
    n=20000
    r=analyze([(str(i),'x') for i in range(n)],[(str(i),str(i+1),'q') for i in range(n-1)])
    assert r['counts']['bridge_pairs']==n-1
    assert r['counts']['articulation_objects']==n-2
    assert r['bridges'][0]['disconnected_pairs']==10000**2


import pytest
from server import event_log_topology_review as topology
from server import event_log_relationship_comparison as comparison
from server import event_log_workspace as workspace
from test_event_log_object_paths import imported, local_store


def settings(log, **changes):
    p=comparison.pin(log)
    return {"snapshot":{k:p[k] for k in ("revision","relationships_sha256","object_index_sha256")},
            "confirm":True,**changes}


def test_complete_retained_graph_and_pinned_pages():
    log=imported()
    spec=settings(log,view="bridges",limit=1)
    first=topology.review(log,spec)
    assert first["counts"]=={"objects":6,"literal_assertions":7,"self_assertions":1,
        "projected_pairs":5,"weak_components":2,"singleton_components":1,"largest_component_objects":5,
        "articulation_objects":2,"bridge_pairs":2,"single_assertion_bridges":2}
    assert first["next_offset"]==1
    last=topology.review(log,{**spec,"offset":1,"expected_review_sha256":first["review_sha256"]})
    assert last["next_offset"] is None and last["review_sha256"]==first["review_sha256"]
    assert {(b["left_id"],b["right_id"]) for b in first["rows"]+last["rows"]}=={
        ("item2","order1"),("invoice1","payment1")}
    assert all(b["disconnected_pairs"]==4 for b in first["rows"]+last["rows"])
    objects=topology.review(log,settings(log,view="objects"))
    assert {q["object_id"]:q["disconnected_surviving_pairs"] for q in objects["rows"]}=={"order1":3,"invoice1":3}
    assert not objects["operational_impact_verified"] and not objects["execution_authorized"]
    filtered=topology.review(log,settings(log,qualifier="contains"))
    assert filtered["counts"]["objects"]==6 and filtered["counts"]["weak_components"]==4
    empty=topology.review(log,settings(log,qualifier=""))
    assert empty["counts"]["weak_components"]==6 and empty["counts"]["literal_assertions"]==0


def test_stale_settings_and_complete_budget(monkeypatch):
    log=imported()
    spec=settings(log,view="bridges")
    pin=topology.review(log,spec)["review_sha256"]
    for change in ({"confirm":1},{"confirm":False},{"offset":1},{"limit":101},
                   {"view":"objects","expected_review_sha256":pin},
                   {"snapshot":{**spec["snapshot"],"relationships_sha256":"b"*64}}):
        with pytest.raises(workspace.EventLogWorkspaceError):
            topology.review(log,{**spec,**change})
    monkeypatch.setattr(topology,"MAX_ASSERTIONS",6)
    with pytest.raises(workspace.EventLogWorkspaceError,match="budget"):
        topology.review(log,settings(log,qualifier="contains"))


def test_rejects_duplicate_and_missing_endpoints():
    with pytest.raises(ValueError):
        analyze([("a","x"),("a","y")],[])
    with pytest.raises(ValueError):
        analyze([("a","x")],[("a","b","q")])
    with pytest.raises(ValueError):
        analyze([("a","x")],[("a","a","q"),("a","a","q")])
