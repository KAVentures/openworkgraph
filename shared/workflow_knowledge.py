from __future__ import annotations

"""Portable, explicitly supplied knowledge; never inferred from captured activity."""

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, StrictBool


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

    def portable(self) -> dict[str, Any]:
        if not self.user_confirmed:
            raise ValueError("Ask the person to review this exact procedure and rules before saving")
        data = self.model_dump(exclude={"user_confirmed", "expected_revision"})
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
            current = self.db._execute(conn, "SELECT MAX(revision) FROM workflow_knowledge WHERE owner = ? AND workflow_id = ?", (owner, request.workflow_id)).fetchone()[0] or 0
            if int(current) != request.expected_revision:
                raise ValueError("Revision changed; reload the workflow and review the proposed update")
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
            cursor = self.db._execute(conn, "DELETE FROM workflow_knowledge WHERE owner = ? AND workflow_id = ?", (owner, workflow_id))
        return {"status": "deleted", "versions_deleted": cursor.rowcount}
