"""Grant-bound human follow-up packets for reviewed evidence exceptions.

A packet is an internal work request, not execution authority, a source write,
a verified remediation result or a modification of the underlying evidence.
"""
import json
import secrets
from typing import Literal
from pydantic import Field,StrictBool,StrictInt,model_validator
from shared.connector_event_snapshot import Strict,DIGEST,digest
from .connector_jobs import JobError
from .connector_exception_triage import ID

FORMAT="openworkgraph.exception-followup.v1"
PACKET=r"^cfp_[a-f0-9]{32}$"
MAX_PACKETS=50
MAX_ITEMS=50
MAX_HISTORY=10
ACTION={
    "source_gap_suspected":"inspect_source_coverage",
    "process_followup_needed":"investigate_process",
    "timestamp_uncertainty":"review_time_assumptions",
    "scope_or_rule_needs_review":"review_rule_and_scope",
    "other_reviewed_reason":"review_evidence",
}
STATES=("requested","in_progress","completed_unverified","cancelled")
TRANSITIONS={
    "requested":{"in_progress","cancelled"},
    "in_progress":{"completed_unverified","cancelled"},
    "completed_unverified":set(),"cancelled":set(),
}


class Item(Strict):
    item_id:str=Field(pattern=DIGEST)
    expected_revision:StrictInt=Field(ge=1)


class Preview(Strict):
    expected_queue_revision:StrictInt=Field(ge=1)
    items:list[Item]=Field(min_length=1,max_length=MAX_ITEMS)

    @model_validator(mode="after")
    def unique(self):
        if len({x.item_id for x in self.items})!=len(self.items):
            raise ValueError("duplicate follow-up item")
        return self


class Create(Preview):
    expected_plan_sha256:str=Field(pattern=DIGEST)
    confirm_followup_request:StrictBool
    confirm_no_execution_authority:StrictBool


class Change(Strict):
    expected_queue_revision:StrictInt=Field(ge=1)
    expected_packet_revision:StrictInt=Field(ge=1)
    state:Literal["in_progress","completed_unverified","cancelled"]
    confirm_human_status:StrictBool


class Export(Strict):
    expected_queue_revision:StrictInt=Field(ge=1)
    expected_packet_revision:StrictInt=Field(ge=1)
    confirm_export:StrictBool


def require(ok,code,status=409):
    if not ok:raise JobError(code,status)


class Followups:
    def __init__(self,queues):
        self.queues=queues
        self.jobs=queues.jobs

    def _item(self,c,org,queue,item_id):
        row=self.queues.q(c,"""SELECT state,revision,definition FROM connector_exception_items
            WHERE organization_id=? AND queue_id=? AND item_id=?""",org,queue,item_id).fetchone()
        require(row is not None,"unknown_exception_item",404)
        item=json.loads(row[2])
        require(item["item_id"]==item_id and item["state"]==row[0] and item["revision"]==row[1],
                "exception_item_integrity_failure")
        return item

    def _packets(self,d):
        packets=d.get("followup_packets",[])
        require(isinstance(packets,list) and len(packets)<=MAX_PACKETS
                and len({p["packet_id"] for p in packets})==len(packets),
                "followup_packet_integrity_failure")
        return packets

    def _current(self,c,org,d,packet):
        for item in packet["items"]:
            live=self._item(c,org,d["queue_id"],item["item_id"])
            if (live["revision"]!=item["revision"] or live["state"]!=item["state"]
                    or live["disposition"]!=item["disposition"]):
                return False
        return True

    def _public(self,c,org,d,packet):
        return {**packet,"item_pins_current":self._current(c,org,d,packet),
                "execution_authorized":False,"source_remediation_verified":False}

    def _load(self,c,org,queue,revision):
        d=self.queues._require(c,org,queue)
        require(d["revision"]==revision,"exception_queue_revision_changed")
        return d

    def _plan(self,c,org,d,spec):
        selected=[]
        for pin in sorted(spec.items,key=lambda x:x.item_id):
            item=self._item(c,org,d["queue_id"],pin.item_id)
            require(item["revision"]==pin.expected_revision,"exception_item_revision_changed")
            action=ACTION.get(item["disposition"])
            require(item["state"] in ("reviewed","deferred") and action is not None,
                    "exception_not_actionable",422)
            selected.append({k:item[k] for k in ("item_id","revision","linked_case_id","rule_id",
                "status","reason","state","disposition")}|{"requested_action":action})
        ids={x["item_id"] for x in selected}
        for packet in self._packets(d):
            if (packet["state"] in ("requested","in_progress") and
                    ids.intersection(x["item_id"] for x in packet["items"])):
                require(not self._current(c,org,d,packet),"followup_items_already_requested")
        plan={"format":FORMAT,"queue_id":d["queue_id"],"queue_revision":d["revision"],
              "conformance_sha256":d["conformance_sha256"],"review_sha256":d["review_sha256"],
              "expires_at":d["expires_at"],"items":selected,
              "execution_authorized":False,"source_remediation_verified":False}
        return {**plan,"plan_sha256":digest(plan)}

    def inventory(self,org,queue):
        with self.jobs._tx(org) as c:
            d=self.queues._require(c,org,queue)
            result={"queue":self.queues._public(c,org,d),
                    "packets":[self._public(c,org,d,p) for p in self._packets(d)],
                    "max_packets":MAX_PACKETS,"execution_authorized":False}
            self.queues.reviewed._fresh(d["expires_at"])
            return result

    def preview(self,org,queue,spec):
        with self.jobs._tx(org) as c:
            d=self._load(c,org,queue,spec.expected_queue_revision)
            plan=self._plan(c,org,d,spec)
            self.queues.reviewed._fresh(d["expires_at"])
            return plan

    def _store(self,c,org,d,principal,action,details):
        d.update(revision=d["revision"]+1,updated_at=self.jobs.clock())
        self.queues.q(c,"UPDATE connector_exception_queues SET definition=? WHERE organization_id=? AND queue_id=?",
                      json.dumps(d),org,d["queue_id"])
        self.queues._audit(c,org,d,principal,action,{"queue_revision":d["revision"],**details})
        self.queues.reviewed._fresh(d["expires_at"])

    def create(self,org,queue,spec,principal):
        require(spec.confirm_followup_request and spec.confirm_no_execution_authority,
                "explicit_followup_approval_required",403)
        with self.jobs._tx(org) as c:
            d=self._load(c,org,queue,spec.expected_queue_revision)
            plan=self._plan(c,org,d,spec)
            require(plan["plan_sha256"]==spec.expected_plan_sha256,"followup_plan_changed")
            packets=self._packets(d)
            require(len(packets)<MAX_PACKETS,"followup_packet_limit")
            packet={**plan,"packet_id":"cfp_"+secrets.token_hex(16),"revision":1,
                    "state":"requested","created_at":self.jobs.clock(),"history":[]}
            d["followup_packets"]=[packet,*packets]
            self._store(c,org,d,principal,"followup_requested",{
                "packet_id":packet["packet_id"],"plan_sha256":plan["plan_sha256"],"items":len(plan["items"])})
            return {"queue":self.queues._public(c,org,d),"packet":self._public(c,org,d,packet)}

    def _find(self,d,packet_id,revision):
        packet=next((x for x in self._packets(d) if x["packet_id"]==packet_id),None)
        require(packet is not None,"followup_packet_missing",404)
        require(packet["revision"]==revision,"followup_packet_revision_changed")
        return packet

    def change(self,org,queue,packet_id,spec,principal):
        require(spec.confirm_human_status,"explicit_followup_status_required",403)
        with self.jobs._tx(org) as c:
            d=self._load(c,org,queue,spec.expected_queue_revision)
            packet=self._find(d,packet_id,spec.expected_packet_revision)
            require(spec.state in TRANSITIONS[packet["state"]],"invalid_followup_transition",422)
            require(spec.state=="cancelled" or self._current(c,org,d,packet),"followup_item_pins_changed")
            previous=packet["state"]
            packet.update(state=spec.state,revision=packet["revision"]+1)
            packet["history"]=[{"previous":previous,"state":spec.state,"at":self.jobs.clock(),
                "revision":packet["revision"]},*packet["history"]][:MAX_HISTORY]
            self._store(c,org,d,principal,"followup_status_changed",{
                "packet_id":packet_id,"packet_revision":packet["revision"],"state":spec.state})
            return {"queue":self.queues._public(c,org,d),"packet":self._public(c,org,d,packet)}

    def export(self,org,queue,packet_id,spec,principal):
        require(spec.confirm_export,"explicit_followup_export_required",403)
        with self.jobs._tx(org) as c:
            d=self._load(c,org,queue,spec.expected_queue_revision)
            p=self._find(d,packet_id,spec.expected_packet_revision)
            require(self._current(c,org,d,p),"followup_item_pins_changed")
            require(p["state"]!="cancelled","followup_packet_cancelled")
            out={"format":FORMAT,"queue_revision":d["revision"],"packet":self._public(c,org,d,p),
                 "external_execution_authorized":False,"source_remediation_verified":False,
                 "local_copies_not_revoked_by_grant_expiry":True}
            out["export_sha256"]=digest(out)
            self.queues._audit(c,org,d,principal,"followup_exported",{
                "packet_id":packet_id,"packet_revision":p["revision"],"export_sha256":out["export_sha256"]})
            self.queues.reviewed._fresh(d["expires_at"])
            return out
