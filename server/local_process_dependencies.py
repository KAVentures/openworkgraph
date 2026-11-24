"""Revision-pinned prerequisites in the local reviewed process catalog."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Annotated

from pydantic import Field, StrictBool, StrictInt

from shared.process_dependencies import (
    Declaration, Link, MAX_LINKS, MAX_REVISIONS, MAX_ROOTS, Saved,
    content_digest, impact, topology,
)
from shared.process_hierarchy import Reference, Strict
from .local_process_catalog import knowledge

OWNER = "local-process-dependencies-v1"
PROCESS_OWNER = "local"


class DependencyConflict(ValueError):
    pass


class SaveDependencies(Strict):
    expected_revision: Annotated[StrictInt, Field(ge=0, le=MAX_REVISIONS)] = 0
    root_process: Reference
    links: list[Link] = Field(default_factory=list, max_length=MAX_LINKS)
    user_confirmed: StrictBool = False


def _record(conn, workflow_id, revision=None):
    args = [PROCESS_OWNER, workflow_id]
    exact = " AND revision = ?" if revision is not None else ""
    if revision is not None:
        args.append(revision)
    row = conn.execute(
        "SELECT revision, payload FROM workflow_knowledge WHERE owner = ? AND workflow_id = ?"
        + exact + " ORDER BY revision DESC LIMIT 1", args,
    ).fetchone()
    if row is None:
        return None
    try:
        item = json.loads(row[1])
        digest = item.pop("content_sha256")
        calculated = hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()
        if calculated != digest or item["workflow_id"] != workflow_id:
            raise ValueError
        if not item.get("process_model"):
            return None
        return {"workflow_id": workflow_id, "revision": row[0], "content_sha256": digest,
                "title": item["title"]}
    except (KeyError, ValueError, TypeError):
        raise DependencyConflict("A referenced process failed its integrity check.") from None


def _pin(record):
    return {key: record[key] for key in ("workflow_id", "revision", "content_sha256")}


def _decode(workflow_id, revision, payload):
    try:
        raw = json.loads(payload)
        digest = raw.pop("content_sha256")
        saved = Saved.model_validate(raw).model_dump(mode="json")
        declaration = saved["dependencies"]
        if (content_digest(saved) != digest or declaration["organization_id"] != PROCESS_OWNER
                or declaration["root_process"]["workflow_id"] != workflow_id
                or not 1 <= revision <= MAX_REVISIONS):
            raise ValueError
    except (KeyError, ValueError, TypeError):
        raise DependencyConflict("A saved prerequisite declaration failed its integrity check.") from None
    return {**saved, "revision": revision, "content_sha256": digest}


def _latest_declarations(conn):
    rows = conn.execute("""SELECT k.workflow_id, k.revision, k.payload FROM workflow_knowledge k
        WHERE k.owner = ? AND k.revision = (SELECT MAX(v.revision) FROM workflow_knowledge v
            WHERE v.owner = k.owner AND v.workflow_id = k.workflow_id)
        ORDER BY k.workflow_id LIMIT ?""", (OWNER, MAX_ROOTS + 1)).fetchall()
    if len(rows) > MAX_ROOTS:
        raise DependencyConflict("The local prerequisite graph exceeds 100 designs.")
    return {workflow_id: _decode(workflow_id, revision, payload) for workflow_id, revision, payload in rows}


def _resolution(conn, reference):
    workflow_id = reference["workflow_id"]
    pinned = _record(conn, workflow_id, reference["revision"])
    current = _record(conn, workflow_id)
    if pinned is None or _pin(pinned) != reference:
        state = "unavailable"
    elif current is None or _pin(current) != reference:
        state = "superseded"
    else:
        state = "current"
    return {"state": state, "pinned": pinned if state != "unavailable" else None, "latest": current}


def _describe(conn, root, items):
    workflow_id = root["workflow_id"]
    item = items.get(workflow_id)
    declaration = item["dependencies"] if item is not None else None
    links = ([
        {**link, "resolution": _resolution(conn, link["prerequisite"])} for link in declaration["links"]
    ] if declaration else [])
    graph = topology([entry["dependencies"] for entry in items.values()])
    reach = impact(graph, workflow_id) if workflow_id in graph["process_ids"] else None
    consumers = []
    if reach:
        for row in reach["potential_consumers"]:
            consumer = items[row["workflow_id"]]
            pinned = consumer["dependencies"]["root_process"]
            resolved = _resolution(conn, pinned)
            consumers.append({**row, "process": pinned, **resolved})
    return {
        "format": "openworkgraph.local-process-prerequisites.v1",
        "root": root, "declaration": item, "links": links, "potential_consumers": consumers,
        "root_resolution": _resolution(conn, declaration["root_process"]) if declaration else None,
        "execution_authorized": False,
    }


def detail(workflow_id):
    knowledge.initialize()
    with knowledge.db.connect() as conn:
        conn.execute("BEGIN")
        root = _record(conn, workflow_id)
        if root is None:
            raise LookupError("Saved process not found.")
        return _describe(conn, root, _latest_declarations(conn))


def save(workflow_id: str, request: SaveDependencies):
    if not request.user_confirmed:
        raise DependencyConflict("Review and confirm the exact prerequisite declarations first.")
    declaration = Declaration(organization_id=PROCESS_OWNER, root_process=request.root_process, links=request.links)
    data = declaration.model_dump(mode="json")
    if request.root_process.workflow_id != workflow_id:
        raise DependencyConflict("The root process differs from the selected design.")
    knowledge.initialize()
    with knowledge.db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        root = _record(conn, workflow_id)
        if root is None:
            raise LookupError("Saved process not found.")
        items = _latest_declarations(conn)
        current = items.get(workflow_id)
        previous = current["revision"] if current else 0
        if previous != request.expected_revision:
            raise DependencyConflict("Prerequisite declarations changed. Reload them before saving.")
        if previous >= MAX_REVISIONS:
            raise DependencyConflict("This design already has 20 prerequisite revisions.")
        if len(items) >= MAX_ROOTS and not current:
            raise DependencyConflict("The local prerequisite graph already covers 100 root designs.")
        for ref in [data["root_process"], *(link["prerequisite"] for link in data["links"])]:
            record = _record(conn, ref["workflow_id"])
            if record is None or _pin(record) != ref:
                raise DependencyConflict("A process revision changed or is unavailable. Review its latest revision.")
        topology([entry["dependencies"] for key, entry in items.items() if key != workflow_id] + [data])
        saved = Saved(dependencies=declaration, author_actor_id="local-dashboard",
                      saved_at=datetime.now(timezone.utc).isoformat()).model_dump(mode="json")
        saved["content_sha256"] = content_digest(saved)
        revision = previous + 1
        conn.execute("INSERT INTO workflow_knowledge(owner, workflow_id, revision, payload) VALUES (?, ?, ?, ?)",
                     (OWNER, workflow_id, revision, json.dumps(saved)))
        items[workflow_id] = {**saved, "revision": revision}
        return _describe(conn, root, items)


def forget_process(workflow_id):
    """Delete a catalog design and its own declarations in the same transaction."""
    knowledge.initialize()
    with knowledge.db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if _record(conn, workflow_id) is None:
            raise LookupError("Saved process not found.")
        conn.execute("DELETE FROM workflow_knowledge WHERE owner = ? AND workflow_id = ?", (OWNER, workflow_id))
        result = conn.execute("DELETE FROM workflow_knowledge WHERE owner = ? AND workflow_id = ?",
                              (PROCESS_OWNER, workflow_id))
        return {"status": "deleted", "versions_deleted": result.rowcount}
