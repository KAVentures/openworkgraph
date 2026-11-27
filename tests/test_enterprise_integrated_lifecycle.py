"""Cross-feature enterprise lifecycle on a real local SQLite source.

The source is never mocked: the operator review, retention, recovery,
monitoring and publication stages all share the canonical workspace.
"""
from datetime import timedelta
import json
import sqlite3

import pytest

from server import enterprise_sql_feed as sql
from server import enterprise_rest_feed as rest
from server import enterprise_case_exclusions as exclusions
from server import enterprise_reviewed_catchup as catchup
from server import enterprise_connector_history as historical
from server import enterprise_ingestion_coverage as coverage
from server import enterprise_ingestion_service_levels as sla
from server import enterprise_retained_integrity as integrity
from server import enterprise_connector_snapshot as snapshot
from server import event_log_workspace as ws
from test_enterprise_sql_feed import workspace_dir,local_source,config,row,insert


def current(p):
    return next(x for x in rest.inventory()["profiles"] if x["profile_id"]==p["profile_id"])


def selection(p):
    return {"targets":[{"profile_id":p["profile_id"],"revision":p["revision"]}]}


def advance(p):
    plan=catchup.preview(selection(p))["targets"][0]
    assert plan["eligible"],plan
    result=catchup.execute({**{k:plan[k] for k in ("profile_id","revision","reviewed_at","since","until","plan_sha256")},
        "confirm_exact_window":True,"confirm_provider_read":True,"confirm_retention":True,
        "confirm_competing_workers_reconciled":True})
    assert result["checkpoint"]==plan["until"]
    return result,current(p)


def recover(p,start,end,request_id):
    body={"revision":p["revision"],"since":start,"until":end}
    plan=historical.preview(p["profile_id"],body)
    spec={**body,"request_id":request_id,"expected_plan_sha256":plan["plan_sha256"],
        "confirm_historical_read":True,"confirm_retention":True,"confirm_workers_reconciled":True}
    return historical.execute(p["profile_id"],spec),spec


def change_count(p,delta):
    with ws._LOCK:
        c=rest._connect()
        try:
            c.execute("UPDATE logs SET events=events+? WHERE log_id=?",[delta,p["log_id"]])
        finally:
            c.close()


def containment(p,action):
    payload=selection(p)
    plan=integrity.inspect(payload,action=action)
    assert plan["all_eligible"],plan
    return integrity.apply({**payload,"plan_sha256":plan["plan_sha256"],
        "confirm_exact_findings":True,"confirm_local_containment":True,
        "confirm_remote_coordination":True},action=action)


def test_integrated_sql_exclusion_recovery_coverage_monitoring_publication(local_source):
    path,_=local_source
    p=sql.create(config(initial_since=rest._iso((rest._now()-timedelta(days=4)).replace(microsecond=0))))
    start=p["config"]["initial_since"]
    first=row(p,"first-private")
    excluded=list(row(p,"excluded-private"))
    excluded[1]="excluded-case"
    insert(path,first,tuple(excluded))
    # Exclude a case before retaining any source rows.
    body={"profile_id":p["profile_id"],"revision":p["revision"],
          "cases":[{"native_case_id":"excluded-case"}]}
    review=exclusions.preview(body)
    assert review["eligible"]
    exclusions.apply({**body,"plan_sha256":review["plan_sha256"],
        "confirm_exact_exclusions":True,"confirm_prospective_only":True,
        "confirm_dispatch_reauthorization":True})
    p=current(p)
    assert not p["enabled"]
    p=rest.change(p["profile_id"],p["revision"],True)
    result,p=advance(p)
    assert result["events_added"]==1
    assert result["last_result"]["excluded_case_events_in_window"]==1
    checkpoint=p["checkpoint"]
    # Simulate pre-upgrade unknown coverage; the real recovery operation
    # must restore the exact request interval, not fabricate an event count.
    with ws._LOCK:
        c=rest._connect()
        try:
            coverage.erase(c,p["profile_id"])
        finally:
            c.close()
    policy={"profile_id":p["profile_id"],"profile_revision":p["revision"],
        "expected_rule_revision":0,"enabled":True,"interval_minutes":15,
        "success_age_hours":None,"checkpoint_age_hours":None,"failure_streak":None,
        "watch_expired_intent":False,"watch_integrity_hold":True,
        "unknown_coverage_hours":1,"confirm_local_monitoring":True}
    sla.save(policy)
    assert not sla.evaluate_due(force=True)["errors"]
    assert sla.inventory()["active_alerts"]==1
    # A late event in the first admitted source window.
    insert(path,row(p,"late-private"))
    outcome,spec=recover(p,start,checkpoint,"a"*32)
    assert outcome["receipt"]["events_added"]==1
    assert outcome["receipt"]["excluded_case_events"]==1
    assert outcome["receipt"]["checkpoint_after"]==checkpoint
    p=current(p)
    assert p["checkpoint"]==checkpoint
    assert historical.execute(p["profile_id"],spec)["replayed_receipt"]
    assert not sla.evaluate_due(force=True)["errors"]
    assert sla.inventory()["active_alerts"]==0
    report=coverage.review({**selection(p),"since":start,"until":checkpoint})
    assert report["summary"]["committed_request_window_fraction"]==1
    assert report["profiles"][0]["historical_commits"]==1
    clean=integrity.inspect(selection(p))["targets"][0]
    assert clean["status"]=="structurally_consistent",clean
    snap=snapshot.prepare(p["profile_id"],start,checkpoint)
    assert snap.manifest.event_count==2 and snap.manifest.case_count==1
    for secret in ("excluded-case","first-private","late-private","actor-private","ignored-secret"):
        assert secret not in snap.model_dump_json()
    change_count(p,1)
    containment(p,"hold")
    p=current(p)
    assert not p["enabled"] and p["integrity_hold"]
    assert not sla.evaluate_due(force=True)["errors"]
    assert sla.inventory()["active_alerts"]==1
    with pytest.raises(ValueError):
        snapshot.prepare(p["profile_id"],start,checkpoint)
    change_count(p,-1)
    containment(p,"release")
    p=current(p)
    assert not p["enabled"] and not p["integrity_hold"]
    p=rest.change(p["profile_id"],p["revision"],True)
    assert not sla.evaluate_due(force=True)["errors"]
    assert sla.inventory()["active_alerts"]==0
    assert snapshot.prepare(p["profile_id"],start,checkpoint).manifest.event_count==2
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT count(*) FROM events").fetchone()[0]==3
    ws.delete_log(p["log_id"])
    deleted=current(p)
    assert deleted["status"]=="dataset_deleted"
    assert not deleted["enabled"]
    assert sla.inventory()["episodes"]==[]


def test_snapshot_refuses_per_event_cardinality_even_if_join_total_matches(local_source):
    path,_=local_source
    p=sql.create(config())
    a=row(p,"a")
    b=list(row(p,"b"));b[1]="second-case"
    insert(path,a,tuple(b))
    _,p=advance(p)
    start=p["config"]["initial_since"]
    end=p["checkpoint"]
    assert snapshot.prepare(p["profile_id"],start,end).manifest.event_count==2
    with ws._LOCK:
        c=rest._connect()
        try:
            pairs=c.execute("SELECT event_id,object_id FROM event_objects WHERE log_id=? ORDER BY event_id",[p["log_id"]]).fetchall()
            assert len(pairs)==2
            c.execute("DELETE FROM event_source_keys WHERE log_id=? AND event_id=?",[p["log_id"],pairs[0][0]])
            c.execute("INSERT INTO event_objects (log_id,event_id,object_id,qualifier) VALUES (?,?,?,?)",
                      [p["log_id"],pairs[1][0],pairs[0][1],"source_case"])
        finally:
            c.close()
    with pytest.raises(ValueError,match="cardinality"):
        snapshot.prepare(p["profile_id"],start,end)
