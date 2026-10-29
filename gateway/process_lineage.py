"""Scoped read-only lineage of exact process, opportunity, and plan snapshots.

This joins existing organization-scoped, digest-checked artifacts. It does not
resolve raw evidence, infer business outcomes, or grant source/action authority.
"""
from .process_collaboration import ProcessCollaborationError as Error
from .process_hierarchies import ProcessHierarchies
from .process_opportunity_review import SharedOpportunityStore
from .process_implementation_plans import ImplementationPlans


def _reference(item, *, workflow_id=None):
    if not isinstance(item, dict):
        return None
    ref = {key: item.get(key) for key in ("workflow_id", "revision", "content_sha256")}
    if workflow_id is not None:
        ref["workflow_id"] = workflow_id
    if (not isinstance(ref["workflow_id"], str) or
            type(ref["revision"]) is not int or ref["revision"] < 1 or
            not isinstance(ref["content_sha256"], str) or
            len(ref["content_sha256"]) != 64):
        return None
    return ref


class ProcessLineage:
    def __init__(self, store):
        self.store = store
        self.catalog = ProcessHierarchies(store)
        self.opportunities = SharedOpportunityStore(store)
        self.plans = ImplementationPlans(store)

    def detail(self, principal, workflow_id):
        with self.catalog.reading(principal) as conn:
            # The root is a real current-tenant process; guessed references
            # cannot enumerate another organization's process IDs.
            self.store._meta(conn, principal, workflow_id)
            try:
                record = self.store._record(conn, principal, workflow_id)
                root_ref = _reference(record, workflow_id=workflow_id)
                root = self.catalog.resolve(conn, principal, root_ref) if root_ref else None
            except (ValueError, TypeError, KeyError):
                root_ref, root = None, None
            if root is None:
                root = {"available": False, "current": False, "state": "integrity_failed"}

            opportunity = self.opportunities.view(conn, principal, workflow_id)
            implementation = self.plans.view(conn, principal, workflow_id)
            opp_artifact = opportunity.get("artifact") if opportunity.get("integrity_ok") else None
            plan_artifact = implementation.get("artifact") if implementation.get("integrity_ok") else None
            opportunity_ref = _reference(opp_artifact, workflow_id=workflow_id)
            implementation_ref = _reference(plan_artifact, workflow_id=workflow_id)

            declared_root = (opp_artifact or {}).get("data", {}).get("process")
            declared_opp = (plan_artifact or {}).get("baseline", {}).get("opportunity")
            root_edge = bool(root.get("available") and root.get("current") and opportunity_ref and
                             declared_root == root_ref)
            opp_edge = bool(opportunity_ref and implementation_ref and
                            declared_opp == opportunity_ref and opportunity.get("integrity_ok"))

            # The root and opportunity are only verified as authentic local
            # revision references, NOT as independent customer/source truth.
            return {
                "format": "openworkgraph.process-lineage-summary.v1",
                "organization_id": principal.organization_id,
                "workflow_id": workflow_id,
                "process": {
                    "reference": root_ref if root.get("available") else None,
                    "state": root["state"],
                    "current": bool(root.get("current")),
                    "independent_design_review": root.get("independent_model_review", "unavailable"),
                },
                "opportunity": {
                    "reference": opportunity_ref,
                    "state": opportunity.get("effective_state"),
                    "integrity_ok": bool(opportunity.get("integrity_ok")),
                    "current_approved_assessment": bool(opportunity.get("current_approved_assessment")),
                    "pinned_process_matches_current": root_edge,
                },
                "implementation_plan": {
                    "reference": implementation_ref,
                    "state": implementation.get("effective_state"),
                    "integrity_ok": bool(implementation.get("integrity_ok")),
                    "current_reviewed_baseline": bool(implementation.get("current_reviewed_baseline")),
                    "pinned_opportunity_matches_current": opp_edge,
                },
                "complete_current_declared_chain": bool(root_edge and opp_edge and
                    implementation.get("current_reviewed_baseline")),
                "evidence_references_independently_verified": False,
                "business_outcomes_independently_verified": False,
                "execution_authorized": False,
                "new_sharing_authorized": False,
                "interpretation": "Exact same-tenant snapshot lineage only. A current declared chain does not prove source evidence, business outcomes, customer approval or execution authority.",
            }
