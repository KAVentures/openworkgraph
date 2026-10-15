from __future__ import annotations

"""Explicit per-organization, per-tenant record mappings.

No inference of equivalence, no personal identifiers, no URL/lookup storage.
Mapping requires admin approval and two currently retained exact references.
Every read rechecks retained canonical evidence, so a deleted/expired source
cannot be revived by a cached relationship. SQLite/Postgres compatible.
"""
from datetime import datetime, timezone
import hashlib
import re
from typing import Any

from .workflow_evidence import load_gateway_evidence

_REF = re.compile(r"^owg:r:[0-9a-f]{32}$")
_LINK = re.compile(r"^[0-9a-f]{40}$")
_MAX_ROWS = 25_000

SQL = """
CREATE TABLE IF NOT EXISTS approved_record_links (
 organization_id TEXT NOT NULL,
 link_id TEXT NOT NULL,
 first_ref TEXT NOT NULL,
 second_ref TEXT NOT NULL,
 approved_at TEXT NOT NULL,
 PRIMARY KEY (organization_id, link_id)
);
CREATE INDEX IF NOT EXISTS idx_record_links_org_first ON approved_record_links(organization_id, first_ref);
CREATE INDEX IF NOT EXISTS idx_record_links_org_second ON approved_record_links(organization_id, second_ref);
"""


def init_record_links(db: Any) -> None:
    with db.connect() as conn:
        for statement in (part.strip() for part in SQL.split(";")):
            if statement:
                db._execute(conn, statement)


def _refs_present(db: Any, org: str, *, actor_id: str | None) -> tuple[set[str], bool]:
    rows = load_gateway_evidence(
        db, organization_id=org, actor_id=actor_id,
        since=None, until=None, limit=_MAX_ROWS,
    )
    present: set[str] = set()
    for row in rows:
        metadata = row.get("metadata")
        if not isinstance(metadata, dict):
            continue
        ref = metadata.get("resource_reference")
        if not isinstance(ref, dict):
            continue
        token = str(ref.get("resource_ref") or "")
        if _REF.fullmatch(token):
            present.add(token)
    return present, len(rows) < _MAX_ROWS


def approve_record_link(db: Any, *, organization_id: str,
                        first_ref: str, second_ref: str) -> dict[str, Any]:
    org = str(organization_id or "").strip()
    if not org or len(org) > 512:
        raise ValueError("organization_id is required")
    if not _REF.fullmatch(first_ref) or not _REF.fullmatch(second_ref) or first_ref == second_ref:
        raise ValueError("two distinct observed opaque record references are required")
    # Approval is explicit: never infer from labels, timestamps or actors.
    observed, _complete = _refs_present(db, org, actor_id=None)
    if first_ref not in observed or second_ref not in observed:
        raise ValueError("both references must exist in retained organization evidence")
    a, b = sorted((first_ref, second_ref))
    link_id = hashlib.sha256((org+"\x00"+a+"\x00"+b).encode("utf-8")).hexdigest()[:40]
    at = datetime.now(timezone.utc).isoformat()
    with db.connect() as conn:
        db._execute(conn,
                    "INSERT INTO approved_record_links(organization_id, link_id, first_ref, second_ref, approved_at) "
                    "VALUES (?, ?, ?, ?, ?) ON CONFLICT (organization_id, link_id) DO NOTHING",
                    (org, link_id, a, b, at))
    return {"link_id": link_id, "organization_id": org, "approved_by": "authenticated_gateway_admin",
            "explicit_approval": True, "automatic_identity_matching": False}


def revoke_record_link(db: Any, *, organization_id: str, link_id: str) -> bool:
    if not _LINK.fullmatch(str(link_id or "")):
        return False
    with db.connect() as conn:
        r = db._execute(conn,
                        "DELETE FROM approved_record_links WHERE organization_id = ? AND link_id = ?",
                        (organization_id, link_id))
        return bool(r.rowcount)


def list_record_links(db: Any, *, organization_id: str,
                      resource_ref: str, actor_id: str | None = None) -> dict[str, Any]:
    if not _REF.fullmatch(str(resource_ref or "")):
        raise ValueError("normalized resource reference required")
    # A scoped token is allowed to see a relationship only if it can see
    # BOTH endpoint references in its own retained canonical evidence.
    visible, complete = _refs_present(db, organization_id, actor_id=actor_id)
    if resource_ref not in visible:
        return {"links": [], "returned": 0, "inaccessible_or_absent": complete,
                "coverage_incomplete": not complete,
                "identity_join_method": "explicit_admin_approval_only"}
    with db.connect() as conn:
        rows = db._execute(conn,
                           "SELECT link_id, first_ref, second_ref FROM approved_record_links "
                           "WHERE organization_id = ? AND (first_ref = ? OR second_ref = ?) LIMIT 100",
                           (organization_id, resource_ref, resource_ref)).fetchall()
    active: list[dict[str, Any]] = []
    stale: list[str] = []
    for row in rows:
        record = dict(row) if hasattr(row, "keys") else dict(zip(("link_id", "first_ref", "second_ref"), row))
        other = record["second_ref"] if record["first_ref"] == resource_ref else record["first_ref"]
        if other not in visible:
            # A different actor's observed evidence is not visible to a
            # restricted actor. That is not evidence of a deleted link.
            if actor_id is None and complete:
                stale.append(record["link_id"])
            continue
        active.append({"link_id": record["link_id"], "related_resource_ref": other,
                       "approved": True, "business_handoff_verified": False,
                       "verified_business_outcome": False})
    for link_id in stale:
        revoke_record_link(db, organization_id=organization_id, link_id=link_id)
    return {"links": active, "returned": len(active),
            "identity_join_method": "explicit_admin_approval_only",
            "actor_restricted": bool(actor_id), "source_log_verified": False,
            "coverage_incomplete": not complete}


def prune_record_links(db: Any, *, organization_id: str) -> dict[str, Any]:
    """Remove links whose endpoints no longer exist in retained evidence.

    Physical cleanup must not wrongly delete a mapping if the evidence scan
    hit its bounded limit; a partial scan is explicitly reported as pending.
    """
    init_record_links(db)
    visible, complete = _refs_present(db, organization_id, actor_id=None)
    if not complete:
        return {"removed": 0, "complete": False}
    removed = 0
    with db.connect() as conn:
        rows = db._execute(conn,
            "SELECT link_id, first_ref, second_ref FROM approved_record_links "
            "WHERE organization_id = ?", (organization_id,)).fetchall()
        for row in rows:
            record = dict(row) if hasattr(row, "keys") else dict(
                zip(("link_id", "first_ref", "second_ref"), row))
            if record["first_ref"] not in visible or record["second_ref"] not in visible:
                deleted = db._execute(conn,
                    "DELETE FROM approved_record_links "
                    "WHERE organization_id = ? AND link_id = ?",
                    (organization_id, record["link_id"]))
                removed += max(0, int(deleted.rowcount or 0))
    return {"removed": removed, "complete": True}


__all__ = ["init_record_links", "approve_record_link", "revoke_record_link", "list_record_links", "prune_record_links"]
