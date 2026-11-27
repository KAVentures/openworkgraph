"""Grant-bound exception review queues. No raw evidence, labels or free text.

Disposition is a human assessment of an evidence exception, never a
certification of real-world remediation or compliance.
"""
import json
import secrets
from typing import Literal
from pydantic import Field,StrictBool,StrictInt,model_validator
from shared.connector_event_snapshot import Strict,DIGEST,digest
from .connector_linked_conformance import Review,_review
from .connector_jobs import JobError

ID=r"^cet_[a-f0-9]{32}$"
MAX_QUEUES=10
MAX_ITEMS=20000
MAX_ORG_ITEMS=40000
MAX_HISTORY=10
MAX_BATCH=50
STATES=("open","investigating","reviewed","deferred")
REASONS=("source_gap_suspected","expected_sample_variation","process_followup_needed",
         "timestamp_uncertainty","scope_or_rule_needs_review","other_reviewed_reason")
SCHEMA="""
CREATE TABLE IF NOT EXISTS connector_exception_queues (
 organization_id TEXT NOT NULL, queue_id TEXT NOT NULL,
 conformance_sha256 TEXT NOT NULL, expires_at DOUBLE PRECISION NOT NULL,
 definition TEXT NOT NULL, PRIMARY KEY(organization_id,queue_id),
 UNIQUE(organization_id,conformance_sha256)
);
CREATE TABLE IF NOT EXISTS connector_exception_pins (
 organization_id TEXT NOT NULL, queue_id TEXT NOT NULL, job_id TEXT NOT NULL,
 grant_id TEXT NOT NULL, publication_revision INTEGER NOT NULL,
 manifest_sha256 TEXT NOT NULL, PRIMARY KEY(organization_id,queue_id,job_id)
);
CREATE TABLE IF NOT EXISTS connector_exception_items (
 organization_id TEXT NOT NULL, queue_id TEXT NOT NULL, item_id TEXT NOT NULL,
 state TEXT NOT NULL, revision INTEGER NOT NULL, definition TEXT NOT NULL,
 PRIMARY KEY(organization_id,queue_id,item_id)
);
CREATE INDEX IF NOT EXISTS connector_exception_state_idx
 ON connector_exception_items(organization_id,queue_id,state,item_id);
"""


class Save(Review):
    expected_conformance_sha256:str=Field(pattern=DIGEST)
    confirm_exception_retention:StrictBool

    @model_validator(mode="after")
    def whole_scope(self):
        if self.offset!=0 or self.filter_status!="all":
            raise ValueError("save requires complete unfiltered review")
        return self


class Page(Strict):
    state:Literal["all","open","investigating","reviewed","deferred"]="all"
    offset:StrictInt=Field(ge=0,le=MAX_ITEMS,default=0)
    limit:StrictInt=Field(ge=1,le=100,default=50)
    expected_revision:StrictInt|None=Field(default=None,ge=1)

    @model_validator(mode="after")
    def pinned(self):
        if self.offset and self.expected_revision is None:
            raise ValueError("queue revision required for subsequent pages")
        return self


class Edit(Strict):
    item_id:str=Field(pattern=DIGEST)
    expected_revision:StrictInt=Field(ge=1)
    state:Literal["open","investigating","reviewed","deferred"]
    reason:Literal["source_gap_suspected","expected_sample_variation","process_followup_needed",
                   "timestamp_uncertainty","scope_or_rule_needs_review","other_reviewed_reason"]|None=None

    @model_validator(mode="after")
    def disposition(self):
        if (self.state in ("reviewed","deferred"))!=(self.reason is not None):
            raise ValueError("reviewed and deferred require a disposition; open and investigating do not")
        return self


class Change(Strict):
    expected_queue_revision:StrictInt=Field(ge=1)
    edits:list[Edit]=Field(min_length=1,max_length=MAX_BATCH)
    confirm_disposition:StrictBool

    @model_validator(mode="after")
    def distinct(self):
        if len({x.item_id for x in self.edits})!=len(self.edits):
            raise ValueError("duplicate item edit")
        return self


class Delete(Strict):
    expected_revision:StrictInt=Field(ge=1)
    confirm_delete:StrictBool


class Queues:
    def __init__(self,reviewed):
        self.reviewed=reviewed;self.jobs=reviewed.jobs;self.db=self.jobs.db
        self.retention_ok=True

    def initialize(self):
        with self.db.connect() as c:
            for stmt in SCHEMA.split(";"):
                if stmt.strip():self.db._execute(c,stmt)

    def q(self,c,sql,*params):
        return self.jobs._sql(c,sql,*params)

    def _remove(self,c,org,queue):
        for table in ("connector_exception_items","connector_exception_pins","connector_exception_queues"):
            self.q(c,"DELETE FROM "+table+" WHERE organization_id=? AND queue_id=?",org,queue)

    def _valid(self,c,org,queue):
        r=self.q(c,"SELECT definition,expires_at FROM connector_exception_queues WHERE organization_id=? AND queue_id=?",org,queue).fetchone()
        if not r or r[1]<=self.jobs.clock():return None
        d=json.loads(r[0])
        pins=self.q(c,"SELECT job_id,grant_id,publication_revision,manifest_sha256 FROM connector_exception_pins WHERE organization_id=? AND queue_id=? ORDER BY job_id",org,queue).fetchall()
        expected=sorted((p["job_id"],p["grant_id"],p["publication_revision"],p["manifest_sha256"]) for p in d["pins"])
        if [tuple(p) for p in pins]!=expected or not pins:return None
        for job,grant,rev,manifest in pins:
            if not self.q(c,"""SELECT 1 FROM connector_event_publications WHERE organization_id=? AND job_id=?
                AND grant_id=? AND revision=? AND manifest_sha256=? AND expires_at>? AND payload IS NOT NULL""",
                org,job,grant,rev,manifest,self.jobs.clock()).fetchone():return None
        return d

    def _require(self,c,org,queue):
        d=self._valid(c,org,queue)
        if d is None:raise JobError("exception_queue_missing_or_invalidated",404)
        return d

    def _clean(self,c,org):
        for (queue,) in self.q(c,"SELECT queue_id FROM connector_exception_queues WHERE organization_id=?",org).fetchall():
            if self._valid(c,org,queue) is None:self._remove(c,org,queue)

    def purge_expired(self):
        with self.db.connect() as c:
            rows=self.q(c,"""SELECT DISTINCT q.organization_id FROM connector_exception_queues q
                WHERE q.expires_at<=? OR EXISTS(
                  SELECT 1 FROM connector_exception_pins p
                  LEFT JOIN connector_event_publications s ON s.organization_id=p.organization_id
                  AND s.job_id=p.job_id AND s.grant_id=p.grant_id AND s.revision=p.publication_revision
                  AND s.manifest_sha256=p.manifest_sha256 AND s.expires_at>? AND s.payload IS NOT NULL
                  WHERE p.organization_id=q.organization_id AND p.queue_id=q.queue_id AND s.job_id IS NULL)
                ORDER BY q.organization_id LIMIT 100""",self.jobs.clock(),self.jobs.clock()).fetchall()
        for (org,) in rows:
            with self.jobs._tx(org) as c:self._clean(c,org)

    def _audit(self,c,org,d,principal,action,details):
        for pin in d["pins"]:
            j=self.jobs._get(c,org,pin["job_id"])
            self.jobs._audit(c,j,principal,"conformance_triage."+action,
                {"queue_id":d["queue_id"],"conformance_sha256":d["conformance_sha256"],**details})

    def _public(self,c,org,d):
        summary=dict.fromkeys(STATES,0)
        for state,n in self.q(c,"SELECT state,count(*) FROM connector_exception_items WHERE organization_id=? AND queue_id=? GROUP BY state",
                              org,d["queue_id"]).fetchall():summary[state]=n
        return {k:d[k] for k in ("queue_id","revision","review_sha256","conformance_sha256",
                                "scope_sha256","expires_at","created_at","updated_at","exception_count")} | {
            "states":summary,"actual_process_compliance_verified":False}

    def save(self,org,spec,principal):
        if not spec.confirm_exception_retention:
            raise JobError("explicit_exception_retention_required",403)
        def retain(c,report):
            self._clean(c,org)
            old=self.q(c,"SELECT queue_id FROM connector_exception_queues WHERE organization_id=? AND conformance_sha256=?",
                       org,report["conformance_sha256"]).fetchone()
            if old:
                d=self._require(c,org,old[0])
                result=self._public(c,org,d)
                self.reviewed._fresh(d["expires_at"])
                return {"queue":result,"already_exists":True}
            if not report["exceptions"]:
                raise JobError("no_exceptions_to_review",422)
            if len(report["exceptions"])>MAX_ITEMS:
                raise JobError("exception_item_budget_exceeded")
            if self.q(c,"SELECT count(*) FROM connector_exception_queues WHERE organization_id=?",org).fetchone()[0]>=MAX_QUEUES:
                raise JobError("exception_queue_limit")
            total=self.q(c,"SELECT count(*) FROM connector_exception_items WHERE organization_id=?",org).fetchone()[0]
            if total+len(report["exceptions"])>MAX_ORG_ITEMS:
                raise JobError("organization_exception_item_budget_exceeded")
            queue="cet_"+secrets.token_hex(16);now=self.jobs.clock()
            d={k:report[k] for k in ("review_sha256","conformance_sha256","scope_sha256","expires_at","exception_count")}
            d.update(queue_id=queue,revision=1,created_at=now,updated_at=now,
                     pins=sorted((p.model_dump() for p in spec.snapshots),key=lambda p:p["job_id"]))
            self.q(c,"INSERT INTO connector_exception_queues VALUES (?,?,?,?,?)",org,queue,d["conformance_sha256"],d["expires_at"],json.dumps(d))
            for p in spec.snapshots:
                self.q(c,"INSERT INTO connector_exception_pins VALUES (?,?,?,?,?,?)",org,queue,p.job_id,p.grant_id,p.publication_revision,p.manifest_sha256)
            batch=[]
            for row in report["exceptions"]:
                identity=digest({"conformance_sha256":d["conformance_sha256"],
                                  "linked_case_id":row["linked_case_id"],"rule_id":row["rule_id"]})
                item={**row,"item_id":identity,"state":"open","revision":1,"disposition":None,"history":[]}
                batch.append((org,queue,identity,"open",1,json.dumps(item)))
                if len(batch)==100:
                    self._insert_items(c,batch);batch=[]
            if batch:self._insert_items(c,batch)
            self._audit(c,org,d,principal,"created",{"exception_count":d["exception_count"]})
            result=self._public(c,org,d)
            self.reviewed._fresh(d["expires_at"])
            return {"queue":result,"already_exists":False}
        return _review(self.reviewed,org,spec,principal,worklist=True,retain=retain)

    def _insert_items(self,c,batch):
        # 600 bind variables at most, below even legacy SQLite limits.
        sql="INSERT INTO connector_exception_items VALUES "+",".join(["(?,?,?,?,?,?)"]*len(batch))
        self.q(c,sql,*(v for row in batch for v in row))

    def inventory(self,org):
        with self.jobs._tx(org) as c:
            self._clean(c,org)
            rows=[]
            for (queue,) in self.q(c,"SELECT queue_id FROM connector_exception_queues WHERE organization_id=? ORDER BY queue_id",org).fetchall():
                d=self._valid(c,org,queue)
                if d:
                    result=self._public(c,org,d)
                    if d["expires_at"]>self.jobs.clock():rows.append(result)
            return {"queues":rows,"retention_cleanup_ok":self.retention_ok,
                    "raw_events_stored":False,"activity_labels_stored":False}

    def page(self,org,queue,spec):
        with self.jobs._tx(org) as c:
            d=self._require(c,org,queue)
            if spec.expected_revision is not None and d["revision"]!=spec.expected_revision:
                raise JobError("exception_queue_revision_changed")
            args=[org,queue];where="organization_id=? AND queue_id=?"
            if spec.state!="all":
                where+=" AND state=?";args.append(spec.state)
            total=self.q(c,"SELECT count(*) FROM connector_exception_items WHERE "+where,*args).fetchone()[0]
            rows=self.q(c,"SELECT definition FROM connector_exception_items WHERE "+where+" ORDER BY item_id LIMIT ? OFFSET ?",*args,spec.limit,spec.offset).fetchall()
            result={"queue":self._public(c,org,d),"items":[json.loads(r[0]) for r in rows],
                    "total":total,"offset":spec.offset,
                    "next_offset":spec.offset+len(rows) if spec.offset+len(rows)<total else None}
            self.reviewed._fresh(d["expires_at"])
            return result

    def change(self,org,queue,spec,principal):
        if not spec.confirm_disposition:raise JobError("explicit_exception_disposition_required",403)
        with self.jobs._tx(org) as c:
            d=self._require(c,org,queue)
            if d["revision"]!=spec.expected_queue_revision:raise JobError("exception_queue_revision_changed")
            now=self.jobs.clock();pending=[]
            for edit in spec.edits:
                row=self.q(c,"SELECT definition FROM connector_exception_items WHERE organization_id=? AND queue_id=? AND item_id=?",org,queue,edit.item_id).fetchone()
                if not row:raise JobError("unknown_exception_item",404)
                item=json.loads(row[0])
                if item["revision"]!=edit.expected_revision:raise JobError("exception_item_revision_changed")
                if item["state"]==edit.state and item["disposition"]==edit.reason:
                    raise JobError("exception_disposition_unchanged",422)
                previous={"state":item["state"],"disposition":item["disposition"],"revision":item["revision"]}
                item.update(state=edit.state,disposition=edit.reason,revision=item["revision"]+1)
                item["history"]=[{"at":now,"previous":previous,"state":edit.state,
                                  "disposition":edit.reason,"revision":item["revision"]},*item["history"]][:MAX_HISTORY]
                pending.append(item)
            for item in pending:
                self.q(c,"UPDATE connector_exception_items SET state=?,revision=?,definition=? WHERE organization_id=? AND queue_id=? AND item_id=?",
                       item["state"],item["revision"],json.dumps(item),org,queue,item["item_id"])
            d.update(revision=d["revision"]+1,updated_at=now)
            self.q(c,"UPDATE connector_exception_queues SET definition=? WHERE organization_id=? AND queue_id=?",json.dumps(d),org,queue)
            self._audit(c,org,d,principal,"dispositions_changed",{"queue_revision":d["revision"],"items_changed":len(pending)})
            result=self._public(c,org,d);self.reviewed._fresh(d["expires_at"])
            return {"queue":result,"items":pending}

    def delete(self,org,queue,spec,principal):
        if not spec.confirm_delete:raise JobError("explicit_exception_queue_deletion_required",403)
        with self.jobs._tx(org) as c:
            d=self._require(c,org,queue)
            if d["revision"]!=spec.expected_revision:raise JobError("exception_queue_revision_changed")
            self._audit(c,org,d,principal,"deleted",{"queue_revision":d["revision"]})
            self._remove(c,org,queue)
            return {"deleted":True,"publications_deleted":False,"source_data_deleted":False}
