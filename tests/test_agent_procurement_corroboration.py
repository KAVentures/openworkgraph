from copy import deepcopy
from itertools import product

import pytest

from shared.agent_procurement_corroboration import summarize
from shared.agent_procurement import digest
from server import agent_procurement_corroboration as review
from server import agent_procurement_monitor_journal as journal
from test_agent_procurement_monitor_journal import local_db,doc,monitor,pilot,version_change,rehearsal,trial,study,decision


def base(view,window="w1"):
    return {"monitor_id":view["monitor_id"],"expected_head_sha256":view["head_sha256"],
            "expected_report_sha256":view["latest_report"]["report_sha256"],"window_id":window,"confirmed":True}


def declaration(slot):
    return {"slot_id":slot["slot_id"],"case_token_sha256":slot["case_token_sha256"],
            "business_outcome":"success","critical_incident":False,"forbidden_action":False,
            "source_reference_sha256":digest(["reviewer evidence",slot["slot_id"]])}


def request(view,window,rows):
    return {**base(view,window),"reviewer_alias":"separate-reviewer",
            "confirm_separate_source_review":True,"rows":rows}


def test_complete_required_corroboration_and_immutable_review(local_db,monitor):
    packet,initial,plan,snapshot=monitor
    view=journal.append(packet,"process_1",1,doc(initial,plan,snapshot))
    assert view["eligible_for_continued_monitoring_review"]
    view=review.require("process_1",1,{"monitor_id":"monitor-1","expected_head_sha256":view["head_sha256"],"confirmed":True})
    assert not view["eligible_for_continued_monitoring_review"]
    for w in plan["windows"]:
        manifest=review.manifest("process_1",1,base(view,w["window_id"]))
        assert all(set(s)=={"slot_id","case_token_sha256"} for s in manifest["slots"])
        assert not manifest["contains_arm_assignments"] and not manifest["contains_producer_outcomes"]
        rows=[declaration(s) for s in manifest["slots"]]
        view=review.record("process_1",1,request(view,w["window_id"],rows))
        with pytest.raises(ValueError,match="rewritten"):
            review.record("process_1",1,request(view,w["window_id"],rows))
    assert view["outcome_corroboration"]["complete"]
    assert view["eligible_for_continued_monitoring_review"]
    assert journal.decide("process_1",1,decision(view))["history"][-1]["kind"]=="decision"


def test_late_conflict_blocks_and_preserves_review(local_db,monitor):
    packet,initial,plan,snapshot=monitor
    partial=deepcopy(snapshot);partial["windows"][0]["observations"]=[]
    view=journal.append(packet,"process_1",1,doc(initial,plan,partial))
    row=declaration(plan["windows"][0]["plan"]["slots"][0]);row["business_outcome"]="failure"
    a=review.record("process_1",1,request(view,"w1",[row]))
    assert a["outcome_corroboration"]["conflicts"]==0
    later=deepcopy(snapshot);later["as_of_ms"]+=1
    b=journal.append(packet,"process_1",1,doc(initial,plan,later,a["head_sha256"]))
    assert b["outcome_corroborations"]==a["outcome_corroborations"]
    assert b["outcome_corroboration"]["conflicts"]==1
    assert b["outcome_corroboration"]["blocks_continuation"]
    with pytest.raises(journal.JournalError,match="blocks continuation"):
        journal.decide("process_1",1,decision(b))


def test_review_pin_source_and_alias_validation(local_db,monitor):
    packet,initial,plan,snapshot=monitor
    view=journal.append(packet,"process_1",1,doc(initial,plan,snapshot))
    row=declaration(plan["windows"][0]["plan"]["slots"][0]);req=request(view,"w1",[row])
    bad=deepcopy(req);bad["reviewer_alias"]="MONITOR-OWNER"
    with pytest.raises(ValueError,match="distinct"):review.record("process_1",1,bad)
    bad=deepcopy(req);bad["rows"][0]["source_reference_sha256"]=snapshot["windows"][0]["observations"][0]["source_evidence_sha256"]
    with pytest.raises(ValueError,match="reused"):review.record("process_1",1,bad)
    bad=deepcopy(req);bad["expected_report_sha256"]="f"*64
    with pytest.raises(journal.JournalError,match="report changed"):review.record("process_1",1,bad)
    assert journal.detail("process_1",1,{"monitor_id":"monitor-1"})==view


def test_pure_comparison_exhaustive():
    for declared,reviewed,critical,review_critical,forbidden,review_forbidden,required,overlap in product(
            ("success","failure",None),("success","failure"),(True,False,None),(True,False),
            (True,False,None),(True,False),(True,False),(True,False)):
        row={"slot_id":"slot","source_reference_sha256":"same" if overlap else "review",
             "business_outcome":reviewed,"critical_incident":review_critical,"forbidden_action":review_forbidden}
        obs={"slot_id":"slot","source_evidence_sha256":"same","status":"completed",
             "business_outcome":declared,"critical_incident":critical,"forbidden_action":forbidden}
        state={"monitor_plan":{"windows":[{"window_id":"w","plan":{"slots":[{"slot_id":"slot"}]}}]},
               "snapshot":{"windows":[{"observations":[obs]}]},
               "outcome_corroborations":[{"reviewer_alias":"reviewer","rows":[row]}],
               "outcome_corroboration_required":required}
        result=summarize(state)
        fields=[(declared,reviewed),(critical,review_critical),(forbidden,review_forbidden)]
        conflict=any(a is not None and a!=b for a,b in fields)
        matched=not overlap and all(a==b for a,b in fields)
        assert result["conflicts"]==int(conflict)
        assert result["matched"]==int(matched)
        assert result["source_overlap"]==int(overlap)
        assert result["blocks_continuation"]==(conflict or overlap or review_critical or review_forbidden or required and not matched)


def test_reviewer_alias_cannot_later_become_producer(local_db,monitor):
    from server import agent_procurement_monitor_telemetry as telemetry
    packet,initial,plan,snapshot=monitor
    view=journal.append(packet,"process_1",1,doc(initial,plan,snapshot))
    row=declaration(plan["windows"][0]["plan"]["slots"][0])
    view=review.record("process_1",1,request(view,"w1",[row]))
    with pytest.raises(journal.JournalError,match="already used"):
        telemetry.grant("process_1",1,{"monitor_id":"monitor-1","expected_head_sha256":view["head_sha256"],
            "producer_alias":"SEPARATE-REVIEWER","window_id":"w1","arm":"challenger","ttl_seconds":60,"confirmed":True})
    assert journal.detail("process_1",1,{"monitor_id":"monitor-1"})==view
