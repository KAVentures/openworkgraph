"""Versioned, grant-bound case review plans. No raw event rows or match tags.

Each version is an immutable approved assertion plan. It remains readable
only while every exact underlying publication is live. Saved review does
not authorize subsequent exports without separate explicit confirmation.
"""
import json
import secrets
from pydantic import Field,StrictBool,StrictInt,model_validator
from shared.connector_event_snapshot import Strict,DIGEST
from .connector_case_review import Plan
from .connector_jobs import JobError

ID=r"^crp_[a-f0-9]{32}$"
MAX_PLANS=20
MAX_VERSIONS=5
SCHEMA="""
CREATE TABLE IF NOT EXISTS connector_review_plan_heads (
 organization_id TEXT NOT NULL, plan_id TEXT NOT NULL, revision INTEGER NOT NULL,
 PRIMARY KEY(organization_id,plan_id)
);
CREATE TABLE IF NOT EXISTS connector_review_plan_versions (
 organization_id TEXT NOT NULL, plan_id TEXT NOT NULL, revision INTEGER NOT NULL,
 expires_at DOUBLE PRECISION NOT NULL, definition TEXT NOT NULL,
 PRIMARY KEY(organization_id,plan_id,revision)
);
CREATE TABLE IF NOT EXISTS connector_review_plan_pins (
 organization_id TEXT NOT NULL, plan_id TEXT NOT NULL, revision INTEGER NOT NULL,
 job_id TEXT NOT NULL, grant_id TEXT NOT NULL, publication_revision INTEGER NOT NULL,
 manifest_sha256 TEXT NOT NULL, PRIMARY KEY(organization_id,plan_id,revision,job_id)
);
"""


class Save(Strict):
    plan_id:str|None=Field(default=None,pattern=ID)
    expected_revision:StrictInt=Field(ge=0,le=1_000_000)
    plan:Plan
    expected_review_sha256:str=Field(pattern=DIGEST)
    confirm_assertions:StrictBool
    confirm_plan_retention:StrictBool

    @model_validator(mode="after")
    def revision(self):
        if (self.plan_id is None)!=(self.expected_revision==0):
            raise ValueError("new plan requires revision zero")
        return self


class Delete(Strict):
    expected_revision:StrictInt=Field(ge=1)
    confirm_delete:StrictBool


class SavedPlans:
    def __init__(self,reviewed):
        self.reviewed=reviewed;self.jobs=reviewed.jobs;self.db=self.jobs.db
        self.retention_ok=True

    def initialize(self):
        with self.db.connect() as c:
            for stmt in SCHEMA.split(";"):
                if stmt.strip():self.db._execute(c,stmt)

    def q(self,c,sql,*params):
        return self.jobs._sql(c,sql,*params)

    def _remove_version(self,c,org,plan,revision):
        self.q(c,"DELETE FROM connector_review_plan_pins WHERE organization_id=? AND plan_id=? AND revision=?",org,plan,revision)
        self.q(c,"DELETE FROM connector_review_plan_versions WHERE organization_id=? AND plan_id=? AND revision=?",org,plan,revision)

    def _remove(self,c,org,plan):
        for table in ("connector_review_plan_pins","connector_review_plan_versions","connector_review_plan_heads"):
            self.q(c,"DELETE FROM "+table+" WHERE organization_id=? AND plan_id=?",org,plan)

    def _valid(self,c,org,plan,revision):
        row=self.q(c,"SELECT definition,expires_at FROM connector_review_plan_versions WHERE organization_id=? AND plan_id=? AND revision=?",org,plan,revision).fetchone()
        if not row or row[1]<=self.jobs.clock():return None
        missing=self.q(c,"""SELECT 1 FROM connector_review_plan_pins p
             LEFT JOIN connector_event_publications s ON s.organization_id=p.organization_id
             AND s.job_id=p.job_id AND s.grant_id=p.grant_id AND s.revision=p.publication_revision
             AND s.manifest_sha256=p.manifest_sha256 AND s.expires_at>? AND s.payload IS NOT NULL
             WHERE p.organization_id=? AND p.plan_id=? AND p.revision=? AND s.job_id IS NULL LIMIT 1""",
             self.jobs.clock(),org,plan,revision).fetchone()
        if missing:return None
        return json.loads(row[0])

    def _clean_org(self,c,org):
        for plan,rev in self.q(c,"SELECT plan_id,revision FROM connector_review_plan_heads WHERE organization_id=?",org).fetchall():
            if self._valid(c,org,plan,rev) is None:
                self._remove(c,org,plan)
                continue
            for (old,) in self.q(c,"SELECT revision FROM connector_review_plan_versions WHERE organization_id=? AND plan_id=?",org,plan).fetchall():
                if self._valid(c,org,plan,old) is None:self._remove_version(c,org,plan,old)

    def purge_expired(self):
        # Bound each sweep to organizations containing at least one invalid
        # version, so healthy organizations cannot starve expiry cleanup.
        with self.db.connect() as c:
            rows=self.q(c,"""SELECT DISTINCT v.organization_id FROM connector_review_plan_versions v
                WHERE v.expires_at<=? OR EXISTS(
                  SELECT 1 FROM connector_review_plan_pins p
                  LEFT JOIN connector_event_publications s ON s.organization_id=p.organization_id
                  AND s.job_id=p.job_id AND s.grant_id=p.grant_id AND s.revision=p.publication_revision
                  AND s.manifest_sha256=p.manifest_sha256 AND s.expires_at>? AND s.payload IS NOT NULL
                  WHERE p.organization_id=v.organization_id AND p.plan_id=v.plan_id
                  AND p.revision=v.revision AND s.job_id IS NULL)
                ORDER BY v.organization_id LIMIT 100""",self.jobs.clock(),self.jobs.clock()).fetchall()
        for (org,) in rows:
            with self.jobs._tx(org) as c:self._clean_org(c,org)

    def _public(self,d):
        return {k:d[k] for k in ("plan_id","revision","review_sha256","scope_sha256","expires_at","saved_at","summary")}

    def inventory(self,org):
        with self.jobs._tx(org) as c:
            self._clean_org(c,org)
            rows=self.q(c,"SELECT plan_id,revision FROM connector_review_plan_heads WHERE organization_id=? ORDER BY plan_id",org).fetchall()
            out=[]
            for plan,rev in rows:
                d=self._valid(c,org,plan,rev)
                if d is not None:out.append(self._public(d))
            return {"plans":out,"retention_cleanup_ok":self.retention_ok,"raw_events_stored":False,"match_tags_stored":False}

    def save(self,org,spec,principal):
        if not spec.confirm_assertions or not spec.confirm_plan_retention:
            raise JobError("explicit_review_assertion_and_plan_retention_required",403)
        with self.jobs._tx(org) as c:
            self._clean_org(c,org)
            loaded=self.reviewed._read(c,org,spec.plan)
            _,review=self.reviewed._compute(org,spec.plan,loaded)
            if review["review_sha256"]!=spec.expected_review_sha256:
                raise JobError("cross_source_review_changed")
            if spec.plan_id is None:
                if self.q(c,"SELECT count(*) FROM connector_review_plan_heads WHERE organization_id=?",org).fetchone()[0]>=MAX_PLANS:
                    raise JobError("saved_review_plan_limit")
                plan="crp_"+secrets.token_hex(16);revision=1
                self.q(c,"INSERT INTO connector_review_plan_heads VALUES (?,?,?)",org,plan,revision)
            else:
                plan=spec.plan_id
                head=self.q(c,"SELECT revision FROM connector_review_plan_heads WHERE organization_id=? AND plan_id=?",org,plan).fetchone()
                if not head or head[0]!=spec.expected_revision:
                    raise JobError("saved_review_revision_changed")
                revision=head[0]+1
                if revision>1_000_000:raise JobError("saved_review_revision_limit")
                self.q(c,"UPDATE connector_review_plan_heads SET revision=? WHERE organization_id=? AND plan_id=?",revision,org,plan)
            self.reviewed._fresh(review["expires_at"])
            d={"plan_id":plan,"revision":revision,"review_sha256":review["review_sha256"],"scope_sha256":review["scope_sha256"],
               "expires_at":review["expires_at"],"saved_at":self.jobs.clock(),"summary":review["summary"],
               "plan":spec.plan.model_dump()}
            self.q(c,"INSERT INTO connector_review_plan_versions VALUES (?,?,?,?,?)",org,plan,revision,d["expires_at"],json.dumps(d,separators=(",",":")))
            for p in spec.plan.snapshots:
                self.q(c,"INSERT INTO connector_review_plan_pins VALUES (?,?,?,?,?,?,?)",org,plan,revision,p.job_id,p.grant_id,p.publication_revision,p.manifest_sha256)
            for (old,) in self.q(c,"SELECT revision FROM connector_review_plan_versions WHERE organization_id=? AND plan_id=? ORDER BY revision DESC",org,plan).fetchall()[MAX_VERSIONS:]:
                self._remove_version(c,org,plan,old)
            self.reviewed._audit(c,org,spec.plan,principal,"plan_saved",{"plan_id":plan,"revision":revision,"review_sha256":d["review_sha256"]})
            return self._public(d)

    def read(self,org,plan,revision,principal):
        with self.jobs._tx(org) as c:
            self._clean_org(c,org)
            d=self._valid(c,org,plan,revision)
            if d is None:raise JobError("saved_review_missing_or_invalidated",404)
            spec=Plan.model_validate(d["plan"])
            loaded=self.reviewed._read(c,org,spec)
            _,review=self.reviewed._compute(org,spec,loaded)
            if review["review_sha256"]!=d["review_sha256"]:
                raise JobError("saved_review_integrity_failure")
            self.reviewed._fresh(review["expires_at"])
            history=[]
            for (r,) in self.q(c,"SELECT revision FROM connector_review_plan_versions WHERE organization_id=? AND plan_id=? ORDER BY revision DESC",org,plan).fetchall():
                version=self._valid(c,org,plan,r)
                if version:history.append(self._public(version))
            self.reviewed._audit(c,org,spec,principal,"plan_opened",{"plan_id":plan,"revision":revision,"review_sha256":d["review_sha256"]})
            return {**self._public(d),"plan":d["plan"],"review":review,"history":history,"export_requires_new_confirmation":True}

    def delete(self,org,plan,spec,principal):
        if not spec.confirm_delete:raise JobError("explicit_review_plan_deletion_required",403)
        with self.jobs._tx(org) as c:
            head=self.q(c,"SELECT revision FROM connector_review_plan_heads WHERE organization_id=? AND plan_id=?",org,plan).fetchone()
            if not head or head[0]!=spec.expected_revision:raise JobError("saved_review_revision_changed")
            d=self._valid(c,org,plan,head[0])
            self._remove(c,org,plan)
            if d:
                self.reviewed._audit(c,org,Plan.model_validate(d["plan"]),principal,"plan_deleted",{"plan_id":plan,"revision":head[0]})
            return {"deleted":True,"publications_deleted":False,"local_source_deleted":False}
