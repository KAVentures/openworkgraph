"""Atomic, explicitly reviewed aggregate + coded-case sharing (R4 / M2).

A paired publication is a new immutable parent revision and a separately
permissioned case attachment in ONE database transaction. The two existing
stores remain the sole authorities for quotas, ACL, audit and content checks.
No raw local log or case IDs are imported, and no case viewers are granted.
"""
from __future__ import annotations

from typing import Annotated

from pydantic import Field, StrictBool, StrictInt

from shared.analysis_snapshot import Snapshot, Strict
from shared.analysis_case_dataset import CaseDataset
from .analysis_publications import AnalysisPublications, PublicationError, Publish
from .analysis_case_data import Attach, CaseData, READ, WRITE


class PairedPublish(Strict):
    title: Annotated[str, Field(min_length=1, max_length=240)]
    snapshot: Snapshot
    case_dataset: CaseDataset
    expected_revision: Annotated[StrictInt, Field(ge=0, le=9)] = 0
    expected_parent_access_revision: Annotated[StrictInt, Field(ge=1)] = 1
    expires_in_days: Annotated[StrictInt, Field(ge=1, le=90)] = 7
    user_confirmed: StrictBool = False
    organization_sharing_confirmed: StrictBool = False
    case_sharing_confirmed: StrictBool = False


def publish(store: AnalysisPublications, principal, body: PairedPublish,
            view_id: str | None = None) -> dict:
    """Create a new or successor aggregate revision and case data atomically."""
    for scope in ("org:analysis:read", "org:analysis:write", READ, WRITE):
        store.scope(principal, scope)
    if not all((body.user_confirmed, body.organization_sharing_confirmed,
                body.case_sharing_confirmed)):
        raise PublicationError(
            "Separate aggregate and case sharing confirmations are required", 403
        )
    # Reject a conflicting source/selection before taking any database write.
    snapshot = body.snapshot.model_dump(mode="json")
    packet = body.case_dataset.model_dump(mode="json")
    if not CaseData.matches(packet, {"snapshot": snapshot}):
        raise PublicationError(
            "Complete coded case population or source/analysis fingerprint "
            "does not match the aggregate snapshot", 422
        )

    # The new-publication route always starts from revision zero. On a
    # successor revision, only the actual owner can attach new case evidence,
    # even if aggregate editors and administrators could normally publish.
    if view_id is None and (body.expected_revision != 0 or body.expected_parent_access_revision != 1):
        raise PublicationError("New paired publication requires initial revision and access counters", 422)
    if view_id is not None and body.expected_revision < 1:
        raise PublicationError("Successor paired publication needs an exact existing revision", 422)

    # An explicit separate grant is still needed for each future case reader.
    # This operation cannot create members or grant viewers implicitly.
    with store.transaction(principal) as conn:
        if view_id is not None:
            current = CaseData(store).owner(conn, principal, view_id)
            if (current["revision"], current["access_revision"]) != (
                body.expected_revision, body.expected_parent_access_revision
            ):
                raise PublicationError("Analysis revision or access changed; reload", 409)
        parent = store.publish_in(
            conn, principal,
            Publish(
                title=body.title, snapshot=body.snapshot,
                expected_revision=body.expected_revision,
                expires_in_days=body.expires_in_days,
                user_confirmed=body.user_confirmed,
                organization_sharing_confirmed=body.organization_sharing_confirmed,
            ), view_id
        )
        detail = CaseData(store).attach_in(
            conn, principal, parent["view_id"],
            Attach(
                revision=parent["revision"],
                parent_content_sha256=parent["content_sha256"],
                expected_parent_access_revision=body.expected_parent_access_revision,
                dataset=body.case_dataset,
                user_confirmed=body.user_confirmed,
                case_sharing_confirmed=body.case_sharing_confirmed,
            )
        )
        store.audit(
            conn, principal, "analysis.paired_published",
            view_id=parent["view_id"], revision=parent["revision"],
            aggregate_content_sha256=parent["content_sha256"],
            case_content_sha256=detail["content_sha256"],
            case_viewer_grants=0,
        )
        return {
            "publication": parent,
            "case_data": detail,
            "atomic_publication": True,
            "case_viewer_grants_created": False,
            "retained_source_log_uploaded": False,
            "source_independently_verified": False,
            "execution_authorized": False,
        }


__all__ = ["PairedPublish", "publish"]
