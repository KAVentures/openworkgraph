"""Read a complete bounded organization repository portfolio in one snapshot."""
from __future__ import annotations

from shared import process_hierarchy_portfolio as core
from .process_hierarchies import ProcessHierarchies,namespace
from .process_collaboration import ProcessCollaborationError as Error,_now


class RepositoryPortfolio:
    def __init__(self,store):
        self.maps=ProcessHierarchies(store)
        self.store=store
        self.db=store.db

    def snapshot(self,c,p):
        q=self.maps.q
        catalog_rows=q(c,"""SELECT workflow_id,archived FROM gateway_shared_processes
                       WHERE organization_id=? ORDER BY workflow_id LIMIT ?""",
                       (p.organization_id,core.MAX_CATALOG+1)).fetchall()
        if len(catalog_rows)>core.MAX_CATALOG:
            raise Error("Repository portfolio exceeds the bounded catalog scan; no partial totals returned",422)
        catalog={wid:{"archived":bool(archived)} for wid,archived in catalog_rows}
        rows=q(c,"""SELECT k.workflow_id,k.revision,k.payload FROM workflow_knowledge k
                    WHERE k.owner=? AND k.revision=(SELECT MAX(v.revision)
                    FROM workflow_knowledge v WHERE v.owner=k.owner AND v.workflow_id=k.workflow_id)
                    ORDER BY k.workflow_id LIMIT ?""",
                    (namespace(p.organization_id),core.MAX_MAPS+1)).fetchall()
        if len(rows)>core.MAX_MAPS:
            raise Error("Repository portfolio exceeds the bounded map scan; no partial totals returned",422)
        artifacts=[]
        refs={}
        occurrence_count=0
        for wid,revision,payload in rows:
            if wid not in catalog:
                raise Error("Repository portfolio contains an unavailable map root; reconcile the saved repository")
            artifact=self.maps.decode((revision,payload),p.organization_id,wid)
            artifacts.append({"root_workflow_id":wid,**artifact})
            for node in artifact["hierarchy"]["nodes"]:
                ref=node["process"]
                refs[core.ref_key(ref)]=ref
                occurrence_count+=1
        if occurrence_count>core.MAX_OCCURRENCES or len(refs)>core.MAX_REFERENCES:
            raise Error("Repository portfolio exceeds the bounded reference scan; no partial totals returned",422)
        resolutions={}
        for key,ref in sorted(refs.items()):
            try:
                info=self.maps.resolve(c,p,ref)
                # Malformed records must not leak arbitrary text or be treated as current.
                if info.get("state") not in {"current","revision_changed","archived","unavailable","integrity_failed"}:
                    raise ValueError
            except (ValueError,TypeError,KeyError,AttributeError):
                info={**ref,"available":False,"state":"integrity_failed","current":False}
            resolutions[key]=info
        return core.build(p.organization_id,catalog,artifacts,resolutions)

    def report(self,p,query,*,exporting=False):
        if exporting and query.expected_fingerprint is None:
            raise Error("Review the exact portfolio before exporting",422)
        with self.maps.reading(p) as c:
            snapshot=self.snapshot(c,p)
            try:
                result=core.page(snapshot,query)
            except ValueError as exc:
                raise Error(str(exc),409) from None
        result["checked_at"]=_now()
        # Existing tenant process-read authorization applies, audit failures
        # withhold the response. No map titles, actor IDs, paths or model
        # payloads are added to audit details.
        self.db.audit(organization_id=p.organization_id,principal_id=p.audit_id,
                      action="process.hierarchy.portfolio.exported" if exporting else "process.hierarchy.portfolio.read",
                      details={"fingerprint":result["fingerprint"],"view":query.view,
                               "offset":query.offset,"returned":len(result["page"]["rows"]),
                               "maps":result["summary"]["saved_maps"],"occurrences":result["summary"]["declared_occurrences"]})
        return result
