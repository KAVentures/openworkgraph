"""Tenant-scoped procurement governance on the existing process catalog.

Only minimized, server-recomputed selection assessments and authenticated
human decisions are persisted. Full trial bundles are transient; no raw
case inputs, source data, model calls or execution authorization.
"""
from __future__ import annotations

import json
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt

from shared.agent_procurement import Strict, loads, digest, MAX_BYTES
from shared.agent_procurement_selection import operation
from shared.process_shadow_review import Hash
from .process_collaboration import ProcessCollaborationError as Error, _now
from .process_hierarchies import ProcessHierarchies

MAX_REVISIONS=30
Revision=Annotated[StrictInt,Field(ge=0,le=MAX_REVISIONS)]
AccessRevision=Annotated[StrictInt,Field(ge=1)]
Bundle=Annotated[str,Field(min_length=2,max_length=MAX_BYTES)]


def namespace(org):
    return json.dumps(["organization-agent-procurement",org,"v1"],separators=(",",":"))


def erase(store,c,org,wid):
    store.db._execute(c,"DELETE FROM workflow_knowledge WHERE owner=? AND workflow_id=?",(namespace(org),wid))


class Submit(Strict):
    process_revision: Annotated[StrictInt,Field(ge=1)]
    expected_procurement_revision: Revision
    expected_access_revision: AccessRevision
    bundle: Bundle
    user_confirmed: StrictBool
    organization_sharing_confirmed: StrictBool


class Decision(Strict):
    expected_procurement_revision: Revision
    expected_access_revision: AccessRevision
    expected_assessment_sha256: Hash
    decision: Literal["approve_rehearsal_review","retain_baseline","defer"]
    bundle: Bundle
    user_confirmed: StrictBool


class Handoff(Strict):
    expected_procurement_revision: Annotated[StrictInt,Field(ge=1,le=MAX_REVISIONS)]
    expected_access_revision: AccessRevision
    expected_approval_sha256: Hash
    expected_selection_receipt_sha256: Hash
    bundle: Bundle
    user_confirmed: StrictBool


class Remove(Strict):
    expected_procurement_revision: Revision
    expected_access_revision: AccessRevision
    user_confirmed: StrictBool


def _summary(result):
    # Retain aggregate evidence only, never original reports, per-case IDs,
    # nominated aliases, raw plan text or source state values.
    def cohort(c):
        keys=("checks","all_required_checks_pass","distinct_cases","repeats","paired_attempts",
              "baseline_match_bounds","candidate_match_bounds","paired_match_count_change_bounds",
              "paired_regression_bounds","candidate_critical_bounds","candidate_non_delivery_bounds",
              "baseline_total_cost_microunits","candidate_total_cost_microunits","baseline_p95_ms",
              "candidate_p95_ms","plan_sha256","ledger_sha256","review_sha256")
        return {k:c[k] for k in keys}
    return {"assessment_sha256":result["assessment_sha256"],"nomination_sha256":result["nomination_sha256"],
            "process_reference":result["process_reference"],"spec_sha256":result["spec_sha256"],
            "candidate":result["candidate"],"baseline_candidate_id":result["baseline_candidate_id"],
            "currency":result["currency"],"policy":result["policy"],
            "discovery":cohort(result["discovery"]),"holdout":cohort(result["holdout"]),
            "eligible_for_rehearsal_review":result["eligible_for_rehearsal_review"]}


class SharedProcurement:
    def __init__(self,store):
        self.store=store;self.db=store.db;self.catalog=ProcessHierarchies(store)

    def q(self,c,sql,args=()):
        return self.db._execute(c,sql,args)

    def permissions(self,c,p,wid,meta):
        role=self.store._role(c,p,wid,meta)
        return {"submit":not meta["archived"] and role in {"owner","editor","admin"} and "org:process:write" in p.scopes,
                "review":not meta["archived"] and role in {"owner","reviewer","admin"} and "org:process:review" in p.scopes,
                "delete":role in {"owner","admin"} and "org:process:write" in p.scopes}

    def preflight(self,p,wid,permission):
        self.store.require_scope(p,"org:process:read")
        with self.db.connect() as c:
            meta=self.store._meta(c,p,wid)
            if not self.permissions(c,p,wid,meta)[permission]:
                raise Error("Current shared process procurement permission required",403)

    def latest(self,c,p,wid):
        return int(self.q(c,"SELECT MAX(revision) FROM workflow_knowledge WHERE owner=? AND workflow_id=?",
                          (namespace(p.organization_id),wid)).fetchone()[0] or 0)

    def get(self,c,p,wid,revision=None):
        sql="SELECT revision,payload FROM workflow_knowledge WHERE owner=? AND workflow_id=?"
        args=[namespace(p.organization_id),wid]
        if revision is not None:sql+=" AND revision=?";args.append(revision)
        row=self.q(c,sql+" ORDER BY revision DESC LIMIT 1",args).fetchone()
        if not row:return None
        try:
            data=json.loads(row[1]);pin=data.pop("content_sha256")
            if (digest(data)!=pin or data["organization_id"]!=p.organization_id or data["revision"]!=int(row[0])
                or data["summary"]["process_reference"]["workflow_id"]!=wid or
                data["state"] not in ("submitted","approve_rehearsal_review","retain_baseline","defer")):
                raise ValueError("integrity")
        except (ValueError,TypeError,KeyError):raise Error("Stored procurement summary failed integrity check",409) from None
        return {**data,"content_sha256":pin}

    def _packet(self,c,p,wid,revision):
        record=self.store._record(c,p,wid,revision)
        packet={k:record[k] for k in ("workflow_id","revision","content_sha256","process_model")}
        packet.update(format="openworkgraph.versioned-process-export.v1",execution_authorization=False,
                      organization_catalog_reference={"organization_id":p.organization_id})
        info=self.catalog.resolve(c,p,{k:packet[k] for k in ("workflow_id","revision","content_sha256")})
        if not info.get("active_latest_independently_reviewed_model"):
            raise Error("Current active independently reviewed shared design required",409)
        return packet

    def _assessment(self,packet,text):
        try:
            return _summary(operation(packet,"assess",loads(text)))
        except (ValueError,TypeError,KeyError,OverflowError,RecursionError):
            raise Error("Invalid or incompatible exact shared procurement assessment bundle",422) from None

    def _append(self,c,p,wid,previous,data):
        if previous>=MAX_REVISIONS:raise Error("Procurement history full; no revisions pruned",409)
        saved={**data,"organization_id":p.organization_id,"revision":previous+1,"saved_at":_now()}
        pin=digest(saved)
        self.q(c,"INSERT INTO workflow_knowledge(owner,workflow_id,revision,payload) VALUES (?,?,?,?)",
               (namespace(p.organization_id),wid,previous+1,json.dumps({**saved,"content_sha256":pin})))
        self.store._audit(c,p,"process.procurement.recorded",workflow_id=wid,revision=previous+1,
                          assessment_sha256=saved["summary"]["assessment_sha256"],state=saved["state"])
        return {"revision":previous+1,"content_sha256":pin,"state":saved["state"],"execution_authorized":False}

    def submit(self,p,wid,body):
        self.store.require_scope(p,"org:process:read")
        if not body.user_confirmed or not body.organization_sharing_confirmed:
            raise Error("Explicit confirmation of organization summary sharing required",422)
        with self.store.write_transaction() as c:
            meta=self.store._meta(c,p,wid,lock=True)
            if not self.permissions(c,p,wid,meta)["submit"]:raise Error("Current procurement editor required",403)
            previous=self.latest(c,p,wid)
            if previous!=body.expected_procurement_revision or meta["access_revision"]!=body.expected_access_revision:
                raise Error("Procurement or process access changed; reload",409)
            packet=self._packet(c,p,wid,body.process_revision)
            summary=self._assessment(packet,body.bundle)
            return self._append(c,p,wid,previous,{"summary":summary,"state":"submitted","submitted_by_actor_id":p.actor_id,
                        "submitted_access_revision":meta["access_revision"],"decision_actor_id":None})

    def decide(self,p,wid,body):
        self.store.require_scope(p,"org:process:read")
        if not body.user_confirmed:raise Error("Explicit exact-evidence review confirmation required",422)
        with self.store.write_transaction() as c:
            meta=self.store._meta(c,p,wid,lock=True)
            if not self.permissions(c,p,wid,meta)["review"]:raise Error("Current procurement reviewer required",403)
            previous=self.latest(c,p,wid);current=self.get(c,p,wid)
            if (previous!=body.expected_procurement_revision or meta["access_revision"]!=body.expected_access_revision
                or not current or current["submitted_access_revision"]!=meta["access_revision"]):
                raise Error("Procurement or process access changed; resubmit exact evidence",409)
            if current["state"]!="submitted":raise Error("Submit new evidence before a new decision",409)
            if current["submitted_by_actor_id"]==p.actor_id:raise Error("A different authenticated actor must review",403)
            packet=self._packet(c,p,wid,current["summary"]["process_reference"]["revision"])
            summary=self._assessment(packet,body.bundle)
            if (summary["assessment_sha256"]!=body.expected_assessment_sha256 or digest(summary)!=digest(current["summary"])):
                raise Error("Assessment evidence changed; resubmit and review",409)
            if body.decision=="approve_rehearsal_review" and not summary["eligible_for_rehearsal_review"]:
                raise Error("Holdout checks do not allow positive rehearsal review",422)
            return self._append(c,p,wid,previous,{"summary":summary,"state":body.decision,
                        "submitted_by_actor_id":current["submitted_by_actor_id"],"submitted_access_revision":meta["access_revision"],
                        "decision_actor_id":p.actor_id})

    def view(self,c,p,wid,revision=None):
        meta=self.store._meta(c,p,wid);latest=self.latest(c,p,wid)
        item=self.get(c,p,wid,revision)
        if revision is not None and item is None:raise Error("Procurement revision not found",404)
        blockers=[]
        if item:
            info=self.catalog.resolve(c,p,item["summary"]["process_reference"])
            if not info.get("active_latest_independently_reviewed_model"):blockers.append("design_not_current_reviewed")
            if item["submitted_access_revision"]!=meta["access_revision"]:blockers.append("process_access_changed")
            if item["revision"]!=latest:blockers.append("historical_procurement_revision")
            if not item["summary"]["eligible_for_rehearsal_review"]:blockers.append("holdout_checks_not_met")
        permissions=self.permissions(c,p,wid,meta)
        approved=bool(item and not blockers and item["state"]=="approve_rehearsal_review")
        return {"format":"openworkgraph.shared-procurement-view.v1","workflow_id":wid,"organization_id":p.organization_id,
                "latest_revision":latest,"access_revision":meta["access_revision"],"artifact":item,
                "permissions":permissions,"blockers":blockers,
                "may_decide":bool(item and item["state"]=="submitted" and permissions["review"] and
                    item["submitted_by_actor_id"]!=p.actor_id and not any(b!="holdout_checks_not_met" for b in blockers)),
                "current_approved_rehearsal_review":approved,
                "authenticated_gateway_actor_decisions":True,"real_world_identity_independently_verified":False,
                "raw_trial_bundles_retained":False,"business_outcomes_verified":False,
                "provider_cost_verified":False,"execution_authorized":False,"production_rollout_authorized":False}

    def check_handoff(self,p,wid,body):
        """Check a portable local selection against the CURRENT shared decision.

        A successful check is an observation of catalog state at inspection,
        not a transferable credential, authorization or durable approval.
        """
        self.store.require_scope(p,"org:process:read")
        if not body.user_confirmed:
            raise Error("Explicit confirmation of selection bundle inspection required",422)
        with self.catalog.reading(p) as c:
            view=self.view(c,p,wid)
            current=view["artifact"]
            if not current or not view["current_approved_rehearsal_review"]:
                raise Error("Current authenticated shared rehearsal approval required",409)
            if (view["latest_revision"]!=body.expected_procurement_revision or
                view["access_revision"]!=body.expected_access_revision or
                current["content_sha256"]!=body.expected_approval_sha256):
                raise Error("Shared procurement approval or access changed; reload",409)
            packet=self._packet(c,p,wid,current["summary"]["process_reference"]["revision"])
            try:
                selection_bundle=loads(body.bundle)
                receipt=operation(packet,"decide",selection_bundle)
            except (ValueError,TypeError,KeyError,OverflowError,RecursionError):
                raise Error("Invalid or incompatible selection decision bundle",422) from None
            summary=current["summary"]
            if (receipt["receipt_sha256"]!=body.expected_selection_receipt_sha256 or
                receipt["rehearsal_candidate_id"]!=summary["candidate"]["candidate_id"] or
                receipt["assessment_sha256"]!=summary["assessment_sha256"] or
                receipt["nomination_sha256"]!=summary["nomination_sha256"] or
                receipt["process_reference"]!=summary["process_reference"] or
                receipt["spec_sha256"]!=summary["spec_sha256"] or
                receipt["candidate"]!=summary["candidate"] or
                receipt["baseline_candidate_id"]!=summary["baseline_candidate_id"]):
                raise Error("Local selection does not match the exact shared approval",409)
            result={"format":"openworkgraph.shared-procurement-handoff-check.v1",
                    "organization_id":p.organization_id,"process_reference":summary["process_reference"],
                    "spec_sha256":summary["spec_sha256"],"procurement_revision":current["revision"],
                    "access_revision":view["access_revision"],"approval_sha256":current["content_sha256"],
                    "assessment_sha256":summary["assessment_sha256"],"nomination_sha256":summary["nomination_sha256"],
                    "selection_receipt_sha256":receipt["receipt_sha256"],
                    "candidate_id":receipt["rehearsal_candidate_id"],
                    "candidate_configuration_sha256":receipt["candidate"]["configuration_sha256"],
                    "baseline_candidate_id":receipt["baseline_candidate_id"],
                    "checked_at":_now(),"current_shared_rehearsal_approval_at_inspection":True,
                    "authenticated_gateway_reviewer_separation_at_inspection":True,
                    "independent_evidence_authenticity_verified":False,"execution_authorized":False,
                    "production_rollout_authorized":False,"result_is_transferable_authorization":False}
        self.db.audit(organization_id=p.organization_id,principal_id=p.audit_id,
                      action="process.procurement.handoff.checked",details={"workflow_id":wid,
                        "procurement_revision":result["procurement_revision"],"approval_sha256":result["approval_sha256"],
                        "selection_receipt_sha256":result["selection_receipt_sha256"]})
        return {**result,"handoff_check_sha256":digest(result)}

    def detail(self,p,wid,revision=None):
        with self.catalog.reading(p) as c:result=self.view(c,p,wid,revision)
        self.db.audit(organization_id=p.organization_id,principal_id=p.audit_id,action="process.procurement.read",
                      details={"workflow_id":wid,"revision":result["latest_revision"]})
        return result

    def inventory(self,p,after="",limit=50):
        with self.catalog.reading(p) as c:
            rows=self.q(c,"""SELECT DISTINCT workflow_id FROM workflow_knowledge
                 WHERE owner=? AND workflow_id>? ORDER BY workflow_id LIMIT ?""",
                 (namespace(p.organization_id),after,limit+1)).fetchall()
            items=[]
            for (wid,) in rows[:limit]:
                try:
                    view=self.view(c,p,wid);a=view["artifact"]
                    if a is None:continue
                    s=a["summary"]
                    items.append({"workflow_id":wid,"revision":a["revision"],"process_revision":s["process_reference"]["revision"],
                                  "state":a["state"],"candidate_id":s["candidate"]["candidate_id"],"currency":s["currency"],
                                  "holdout_cases":s["holdout"]["distinct_cases"],"blockers":view["blockers"],
                                  "current_approved_rehearsal_review":view["current_approved_rehearsal_review"]})
                except Error as exc:
                    if exc.status!=404:items.append({"workflow_id":wid,"state":"unavailable","current_approved_rehearsal_review":False})
        self.db.audit(organization_id=p.organization_id,principal_id=p.audit_id,action="process.procurement.inventory.read",
                      details={"returned":len(items)})
        return {"organization_id":p.organization_id,"items":items,"has_more":len(rows)>limit,
                "next_after":rows[limit-1][0] if len(rows)>limit else None,"portfolio_savings_total":None,
                "execution_authorized":False}

    def delete(self,p,wid,body):
        self.store.require_scope(p,"org:process:read")
        if not body.user_confirmed:raise Error("Confirm deletion of all procurement history",422)
        with self.store.write_transaction() as c:
            meta=self.store._meta(c,p,wid,lock=True)
            if not self.permissions(c,p,wid,meta)["delete"]:raise Error("Current process owner or admin required",403)
            if meta["access_revision"]!=body.expected_access_revision or self.latest(c,p,wid)!=body.expected_procurement_revision:
                raise Error("Procurement or process access changed; reload",409)
            erase(self.store,c,p.organization_id,wid)
            self.store._audit(c,p,"process.procurement.deleted",workflow_id=wid)
            return {"deleted":True,"execution_authorized":False}
