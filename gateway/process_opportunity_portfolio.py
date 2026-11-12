"""Read-only selected opportunity portfolio budget comparisons.

No background query, stored portfolio, spending grant, or roll-up across
unselected organization records. One consistent read transaction resolves
all caller-selected exact revisions and current root review permissions.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from shared.analysis_snapshot import content_digest

from shared.process_opportunity_portfolio import Preview, summarize
from .process_collaboration import ProcessCollaborationError as Error
from .process_opportunity_review import SharedOpportunityStore


class SelectedOpportunityBudget:
    def __init__(self, store):
        self.store = store
        self.opps = SharedOpportunityStore(store)

    def preview(self, p, body: Preview):
        if not body.user_confirmed:
            raise Error("Review and confirm this selected opportunity comparison", 422)
        rows = []
        # The existing process catalog checks tenant and read scope. A single
        # transaction snapshots all saved opportunity revisions for this query.
        with self.opps.catalog.reading(p) as conn:
            for selection in body.selections:
                wid = selection.workflow_id
                row = {
                    "workflow_id": wid,
                    "pinned_revision": selection.revision,
                    "pinned_content_sha256": selection.content_sha256,
                    "current_approved_assessment": False,
                    "implementation_budget": None,
                    "stage": None,
                    "blockers": [],
                }
                try:
                    view = self.opps.view(conn, p, wid)
                except Error:
                    # Do not disclose whether a missing/foreign root exists.
                    row["blockers"] = ["unavailable_or_inaccessible"]
                    rows.append(row)
                    continue
                item = view["artifact"]
                if not view["integrity_ok"]:
                    row["blockers"].append("saved_opportunity_integrity_failed")
                if item is None:
                    row["blockers"].append("saved_opportunity_unavailable")
                elif (item["revision"] != selection.revision
                      or item["content_sha256"] != selection.content_sha256
                      or item["revision"] != view["latest_revision"]):
                    row["blockers"].append("opportunity_revision_or_content_changed")
                if item is not None and not row["blockers"]:
                    row["stage"] = view["effective_state"]
                    data = item["data"]
                    assessment = data.get("assessment")
                    if assessment is None:
                        row["blockers"].append("assessment_missing")
                    if view["approval_blockers"]:
                        row["blockers"].extend(view["approval_blockers"])
                    if row["stage"] not in {"assessing", "approved", "implemented"}:
                        row["blockers"].append("opportunity_stage_not_in_active_pipeline")
                    if assessment:
                        if assessment["budget_currency"] != body.currency:
                            row["blockers"].append("currency_conversion_not_supported")
                        if not row["blockers"]:
                            amount = assessment["implementation_budget"]
                            # Validate the stored snapshot via its model, then
                            # decimal parsing stays exact; no float arithmetic.
                            row["implementation_budget"] = str(Decimal(amount))
                            row["current_approved_assessment"] = bool(view["current_approved_assessment"])
                if row["blockers"]:
                    # No stale amount is returned from a disqualified row.
                    row["implementation_budget"] = None
                    row["current_approved_assessment"] = False
                rows.append(row)
        result = summarize(rows, body.ceiling, body.currency)
        result["snapshot_checked_at"] = datetime.now(timezone.utc).isoformat()
        result["selection_reference_digest"] = content_digest({
            "currency": body.currency,
            "ceiling": body.ceiling,
            "selections": [selection.model_dump(mode="json") for selection in body.selections],
        })
        result["reference_digest_is_not_source_authentication"] = True
        self.store.db.audit(
            organization_id=p.organization_id,
            principal_id=p.audit_id,
            action="process.opportunity_portfolio.preview",
            details={
                "selection_count": len(rows),
                "blocked_count": len(result["blockers"]),
                "status": result["status"],
                "content_exported": False,
                "financial_values_in_audit": False,
            },
        )
        return result
