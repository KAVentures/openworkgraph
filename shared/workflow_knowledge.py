from __future__ import annotations

"""Portable, explicitly supplied knowledge; never inferred from captured activity."""

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, StrictBool

from .process_model import ProcessModel
from .execution_grouping import ExecutionGrouping


class KnowledgeWrite(BaseModel):
    workflow_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    title: str = Field(min_length=1, max_length=240)
    procedure: str = Field(min_length=1, max_length=24000)
    user_explanations: list[str] = Field(default_factory=list, max_length=40)
    decision_rules: list[str] = Field(default_factory=list, max_length=40)
    unresolved_questions: list[str] = Field(default_factory=list, max_length=40)
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)
    source_client: str = Field(min_length=1, max_length=120)
    expected_revision: int = Field(default=0, ge=0)
    user_confirmed: StrictBool = False
    process_model: ProcessModel | None = None
    execution_grouping: ExecutionGrouping | None = None

    def portable(self) -> dict[str, Any]:
        if not self.user_confirmed:
            raise ValueError("Ask the person to review this exact procedure and rules before saving")
        data = self.model_dump(exclude={"user_confirmed", "expected_revision"})
        if data["process_model"] is None:
            data.pop("process_model")
        if data['execution_grouping'] is None:
            data.pop('execution_grouping')
        if any(len(value) > 2000 for key in ("user_explanations", "decision_rules", "unresolved_questions", "evidence_refs") for value in data[key]):
            raise ValueError("Knowledge list entries must not exceed 2000 characters")
        data.update({"format": "openworkgraph.workflow-knowledge.v1",
                     "data_layer": "user_reviewed_knowledge",
                     "review_basis": "client_declared_user_confirmation",
                     "observed_evidence": False, "organization_policy": False,
                     "execution_authorization": False,
                     "saved_at": datetime.now(timezone.utc).isoformat()})
        data["content_sha256"] = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        return data


class KnowledgeStore:
    def __init__(self, db: Any):
        self.db = db

    def initialize(self) -> None:
        with self.db.connect() as conn:
            self.db._execute(conn, """CREATE TABLE IF NOT EXISTS workflow_knowledge (
              owner TEXT NOT NULL, workflow_id TEXT NOT NULL, revision INTEGER NOT NULL,
              payload TEXT NOT NULL, PRIMARY KEY(owner, workflow_id, revision))""")

    def list(self, owner: str, workflow_id: str = "", limit: int = 50) -> dict[str, Any]:
        self.initialize()
        cap = max(1, min(int(limit), 100))
        with self.db.connect() as conn:
            sql = """SELECT k.workflow_id, k.revision, k.payload FROM workflow_knowledge k
                     WHERE k.owner = ? AND k.revision =
                     (SELECT MAX(v.revision) FROM workflow_knowledge v
                      WHERE v.owner = k.owner AND v.workflow_id = k.workflow_id)"""
            params = [owner]
            if workflow_id:
                sql += " AND k.workflow_id = ?"
                params.append(workflow_id)
            sql += " ORDER BY k.workflow_id LIMIT ?"
            rows = self.db._execute(conn, sql, tuple(params + [cap + 1])).fetchall()
        items = [json.loads(row[2]) | {"revision": int(row[1])} for row in rows[:cap]]
        return {"format": "openworkgraph.workflow-knowledge.v1", "workflows": items,
                "returned": len(items), "has_more": len(rows) > cap,
                "knowledge_is_observed_evidence": False, "knowledge_is_execution_authorization": False}

    def save(self, owner: str, request: KnowledgeWrite) -> dict[str, Any]:
        data = request.portable()
        self.initialize()
        with self.db.connect() as conn:
            return self.save_in_transaction(conn, owner, request, portable=data)

    def save_in_transaction(self, conn: Any, owner: str, request: KnowledgeWrite,
                            *, portable: dict[str, Any] | None = None,
                            allow_execution_grouping: bool = False) -> dict[str, Any]:
        """Append to an initialized store inside the caller's metadata transaction."""
        if request.execution_grouping is not None and not allow_execution_grouping:
            raise ValueError('Use reviewed workflow grouping controls to classify executions')
        data = request.portable() if portable is None else portable
        current = self.db._execute(conn, "SELECT MAX(revision) FROM workflow_knowledge WHERE owner = ? AND workflow_id = ?", (owner, request.workflow_id)).fetchone()[0] or 0
        if int(current) != request.expected_revision:
            raise ValueError("Revision changed; reload the workflow and review the proposed update")
        if current and request.process_model is None:
            prior = self.db._execute(conn, "SELECT payload FROM workflow_knowledge WHERE owner = ? AND workflow_id = ? AND revision = ?", (owner, request.workflow_id, current)).fetchone()
            if prior and json.loads(prior[0]).get("process_model"):
                raise ValueError("Process graph must be preserved; use the process catalog to edit this workflow")
        if current and request.execution_grouping is None:
            prior = self.db._execute(conn, "SELECT payload FROM workflow_knowledge WHERE owner = ? AND workflow_id = ? AND revision = ?", (owner, request.workflow_id, current)).fetchone()
            if prior and json.loads(prior[0]).get('execution_grouping'):
                raise ValueError('Reviewed execution grouping must be preserved when updating this knowledge record')
        revision = int(current) + 1
        try:
            self.db._execute(conn, "INSERT INTO workflow_knowledge(owner, workflow_id, revision, payload) VALUES (?, ?, ?, ?)", (owner, request.workflow_id, revision, json.dumps(data)))
        except Exception as exc:
            if isinstance(exc, sqlite3.IntegrityError) or getattr(exc, "sqlstate", None) == "23505":
                raise ValueError("Revision changed; reload the workflow and review the proposed update") from exc
            raise
        return data | {"revision": revision}

    def forget(self, owner: str, workflow_id: str) -> dict[str, Any]:
        self.initialize()
        with self.db.connect() as conn:
            row = self.db._execute(conn, 'SELECT payload FROM workflow_knowledge WHERE owner = ? AND workflow_id = ? ORDER BY revision DESC LIMIT 1',
                                   (owner, workflow_id)).fetchone()
            if row and json.loads(row[0]).get('execution_grouping'):
                raise ValueError('Use reviewed workflow grouping controls to delete classifications')
            cursor = self.db._execute(conn, "DELETE FROM workflow_knowledge WHERE owner = ? AND workflow_id = ?", (owner, workflow_id))
        return {"status": "deleted", "versions_deleted": cursor.rowcount}

    def history(self, owner: str, workflow_id: str, *, revision: int | None = None,
                before_revision: int | None = None, limit: int = 20) -> dict[str, Any]:
        self.initialize()
        cap = max(1, min(int(limit), 100))
        with self.db.connect() as conn:
            sql = "SELECT revision, payload FROM workflow_knowledge WHERE owner = ? AND workflow_id = ?"
            params: list[Any] = [owner, workflow_id]
            if revision is not None:
                sql += " AND revision = ?"
                params.append(revision)
            if before_revision is not None:
                sql += " AND revision < ?"
                params.append(before_revision)
            sql += " ORDER BY revision DESC LIMIT ?"
            rows = self.db._execute(conn, sql, tuple(params + [cap + 1])).fetchall()
        records = [json.loads(row[1]) | {"revision": int(row[0])} for row in rows[:cap]]
        return {"workflows": records, "has_more": len(rows) > cap,
                "next_before_revision": records[-1]["revision"] if len(rows) > cap else None}

    def page(self, owner: str, *, after: str = '', limit: int = 20) -> dict[str, Any]:
        """Latest revisions with a stable ID cursor; never mix owners."""
        self.initialize()
        cap = max(1, min(int(limit), 100))
        with self.db.connect() as conn:
            rows = self.db._execute(conn, '''SELECT k.workflow_id, k.revision, k.payload
                FROM workflow_knowledge k WHERE k.owner = ? AND k.workflow_id > ?
                AND k.revision = (SELECT MAX(v.revision) FROM workflow_knowledge v
                  WHERE v.owner = k.owner AND v.workflow_id = k.workflow_id)
                ORDER BY k.workflow_id LIMIT ?''', (owner, after, cap + 1)).fetchall()
        items = [json.loads(row[2]) | {'revision': int(row[1])} for row in rows[:cap]]
        return {'workflows': items, 'has_more': len(rows) > cap,
                'next_after': items[-1]['workflow_id'] if len(rows) > cap else None}
