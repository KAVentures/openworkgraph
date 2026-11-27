"""Grant-bound human follow-up planning, never automatic remediation.

The evidence outcome and human disposition are independent of follow-up.
A reported completion is a human assertion, not verified source repair.
"""
import json
from typing import Literal
from pydantic import Field,StrictBool,StrictInt,model_validator
from shared.connector_event_snapshot import Strict,DIGEST,digest
from .connector_jobs import JobError
from .connector_exception_triage import MAX_BATCH,MAX_ITEMS

FORMAT="openworkgraph.exception-followup.v1"
MAX_HISTORY=10
ACTIONS={
    "source_gap_suspected":"inspect_source_coverage",
    "process_followup_needed":"review_process_with_owner",
    "timestamp_uncertainty":"review_timestamp_assumptions",
    "scope_or_rule_needs_review":"review_scope_and_rule",
    "other_reviewed_reason":"review_other_evidence_issue",
}
STATES=("planned","in_progress","reported_done","cancelled")
TRANSITIONS={"planned":{"in_progress","reported_done","cancelled"},
             "in_progress":{"reported_done","cancelled"},
             "reported_done":set(),"cancelled":set()}


class Edit(Strict):
    item_id:str=Field(pattern=DIGEST)
    expected_revision:StrictInt=Field(ge=1)
    operation:Literal["plan","in_progress","reported_done","cancelled"]
    due_days:StrictInt|None=Field(default=None,ge=1,le=90)

    @model_validator(mode="after")
    def fields(self):
        if (self.operation=="plan")!=(self.due_days is not None):
            raise ValueError("only planning requires a due interval")
        return self


class Change(Strict):
    expected_queue_revision:StrictInt=Field(ge=1)
    edits:list[Edit]=Field(min_length=1,max_length=MAX_BATCH)
    confirm_followup:StrictBool

    @model_validator(mode="after")
    def distinct(self):
        if len({e.item_id for e in self.edits})!=len(self.edits):
            raise ValueError("duplicate follow-up edit")
        return self


class Export(Strict):
    expected_revision:StrictInt=Field(ge=1)
    confirm_followup_export:StrictBool


def suggestion(item):
    if item["state"] not in ("reviewed","deferred"):
        return None
    return ACTIONS.get(item["disposition"])


def transition(item,edit,now,expires):
    if item["revision"]!=edit.expected_revision:
        raise JobError("exception_item_revision_changed")
    old=item.get("followup")
    if edit.operation=="plan":
        action=suggestion(item)
        if not action:
            raise JobError("followup_requires_actionable_disposition",422)
        if old and not old.get("stale") and old["state"] in ("planned","in_progress"):
            raise JobError("followup_already_active")
        due=now+edit.due_days*86400
        if due>=expires:
            raise JobError("followup_due_after_publication_expiry",422)
        new={"action":action,"state":"planned","created_at":now,"due_at":due,
             "based_on_disposition":item["disposition"],"based_on_state":item["state"],
             "stale":False,"updated_at":now,"history":[]}
        if old:
            new["history"]=[{"at":now,"previous_state":old["state"],"state":"planned",
                             "action":action,"previous_stale":bool(old.get("stale"))},*old["history"]][:MAX_HISTORY]
    else:
        if not old or old.get("stale") or old["based_on_disposition"]!=item["disposition"] or old["based_on_state"]!=item["state"]:
            raise JobError("followup_requires_fresh_plan")
        if edit.operation not in TRANSITIONS[old["state"]]:
            raise JobError("invalid_followup_transition",422)
        new={**old,"state":edit.operation,"updated_at":now,
             "history":[{"at":now,"previous_state":old["state"],"state":edit.operation,
                         "action":old["action"],"previous_stale":False},*old["history"]][:MAX_HISTORY]}
    return {**item,"revision":item["revision"]+1,"followup":new}


def change(queues,org,queue,spec,principal):
    if not spec.confirm_followup:
        raise JobError("explicit_followup_approval_required",403)
    with queues.jobs._tx(org) as c:
        d=queues._require(c,org,queue)
        if d["revision"]!=spec.expected_queue_revision:
            raise JobError("exception_queue_revision_changed")
        now=queues.jobs.clock()
        pending=[]
        for edit in spec.edits:
            row=queues.q(c,"SELECT definition FROM connector_exception_items WHERE organization_id=? AND queue_id=? AND item_id=?",
                         org,queue,edit.item_id).fetchone()
            if not row:
                raise JobError("unknown_exception_item",404)
            pending.append(transition(json.loads(row[0]),edit,now,d["expires_at"]))
        for item in pending:
            queues.q(c,"UPDATE connector_exception_items SET revision=?,definition=? WHERE organization_id=? AND queue_id=? AND item_id=?",
                     item["revision"],json.dumps(item),org,queue,item["item_id"])
        d.update(revision=d["revision"]+1,updated_at=now)
        queues.q(c,"UPDATE connector_exception_queues SET definition=? WHERE organization_id=? AND queue_id=?",
                 json.dumps(d),org,queue)
        queues._audit(c,org,d,principal,"followups_changed",{"queue_revision":d["revision"],"items_changed":len(pending)})
        result=queues._public(c,org,d)
        queues.reviewed._fresh(d["expires_at"])
        return {"queue":result,"items":pending,"source_actions_executed":False,
                "remediation_independently_verified":False}


def _report(queues,c,org,d):
    now=queues.jobs.clock()
    rows=queues.q(c,"SELECT definition FROM connector_exception_items WHERE organization_id=? AND queue_id=? ORDER BY item_id",
                  org,d["queue_id"]).fetchall()
    if len(rows)!=d["exception_count"] or len(rows)>MAX_ITEMS:
        raise JobError("exception_queue_integrity_failure")
    counts=dict.fromkeys((*STATES,"stale","overdue","suggested_unplanned"),0)
    items=[]
    for (definition,) in rows:
        item=json.loads(definition)
        f=item.get("followup")
        action=suggestion(item)
        if f:
            stale=bool(f.get("stale"))
            counts["stale" if stale else f["state"]]+=1
            overdue=not stale and f["state"] in ("planned","in_progress") and f["due_at"]<now
            counts["overdue"]+=int(overdue)
            items.append({"item_id":item["item_id"],"rule_id":item["rule_id"],
                          "evidence_outcome":item["status"],"disposition":item["disposition"],
                          "item_revision":item["revision"],"action":f["action"],"state":f["state"],
                          "due_at":f["due_at"],"stale":stale,"overdue":overdue})
        elif action:
            counts["suggested_unplanned"]+=1
            items.append({"item_id":item["item_id"],"rule_id":item["rule_id"],
                          "evidence_outcome":item["status"],"disposition":item["disposition"],
                          "item_revision":item["revision"],"action":action,"state":"unplanned",
                          "due_at":None,"stale":False,"overdue":False})
    items.sort(key=lambda i:(not i["overdue"],i["due_at"] is None,i["due_at"] or 0,i["item_id"]))
    return {"format":FORMAT,"queue":queues._public(c,org,d),"observed_at":now,
            "counts":counts,"items":items,"source_actions_executed":False,
            "remediation_independently_verified":False,"actual_process_compliance_verified":False}


def report(queues,org,queue):
    with queues.jobs._tx(org) as c:
        d=queues._require(c,org,queue)
        out=_report(queues,c,org,d)
        # Passive view is counts-only; export needs separate approval.
        out.pop("items")
        queues.reviewed._fresh(d["expires_at"])
        return out


def export(queues,org,queue,spec,principal):
    if not spec.confirm_followup_export:
        raise JobError("explicit_followup_export_required",403)
    with queues.jobs._tx(org) as c:
        d=queues._require(c,org,queue)
        if d["revision"]!=spec.expected_revision:
            raise JobError("exception_queue_revision_changed")
        out=_report(queues,c,org,d)
        out["export_sha256"]=digest(out)
        queues._audit(c,org,d,principal,"followups_exported",
                      {"queue_revision":d["revision"],"items":len(out["items"]),"export_sha256":out["export_sha256"]})
        queues.reviewed._fresh(d["expires_at"])
        return out
