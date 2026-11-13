"""Revisioned, actor-scoped process outcome comparisons (roadmap #370 R16).

Uses the existing shared process and knowledge store, never a parallel case
database. No source calls, automatic permissions or independently verified
realized-savings claims are made.
"""
from __future__ import annotations

import json

from shared.analysis_snapshot import content_digest
from shared.process_value_measurement import Saved, compare, MAX_REVISIONS
from .process_collaboration import ProcessCollaborationError as Error, _now
from .process_hierarchies import ProcessHierarchies


def namespace(org):
    return json.dumps(["organization-process-value-measurements", org, "v1"], separators=(",", ":"))


def erase(store, conn, org, workflow_id):
    store.db._execute(conn, "DELETE FROM workflow_knowledge WHERE owner=? AND workflow_id=?",
                      (namespace(org), workflow_id))


class ProcessValueMeasurements:
    def __init__(self, store):
        self.store = store
        self.db = store.db
        self.catalog = ProcessHierarchies(store)

    def q(self, conn, sql, args=()):
        return self.db._execute(conn, sql, args)

    def latest(self, conn, p, wid):
        return int(self.q(conn, "SELECT MAX(revision) FROM workflow_knowledge WHERE owner=? AND workflow_id=?",
                          (namespace(p.organization_id), wid)).fetchone()[0] or 0)

    def get(self, conn, p, wid, revision=None):
        sql = "SELECT revision,payload FROM workflow_knowledge WHERE owner=? AND workflow_id=?"
        args = [namespace(p.organization_id), wid]
        if revision is not None:
            sql += " AND revision=?"
            args.append(revision)
        row = self.q(conn, sql + " ORDER BY revision DESC LIMIT 1", args).fetchone()
        if row is None:
            return None
        try:
            raw = json.loads(row[1])
            digest = raw.pop("content_sha256")
            saved = Saved.model_validate(raw).model_dump(mode="json")
            m = saved["measurement"]
            if (content_digest(saved) != digest or m["organization_id"] != p.organization_id
                    or m["root_process"]["workflow_id"] != wid):
                raise ValueError("mismatched measurement")
        except (KeyError, TypeError, ValueError):
            raise Error("Stored value comparison failed its integrity check") from None
        return {**saved, "revision": int(row[0]), "content_sha256": digest}

    def detail(self, p, wid, revision=None, *, exporting=False):
        with self.catalog.reading(p) as conn:
            meta = self.store._meta(conn, p, wid)
            role = self.store._role(conn, p, wid, meta)
            latest = self.latest(conn, p, wid)
            integrity = True
            try:
                item = self.get(conn, p, wid, revision)
            except Error:
                item = None
                integrity = False
            if revision is not None and item is None and integrity:
                raise Error("Value measurement revision not found", 404)
            if exporting and (item is None or not integrity):
                raise Error("A valid saved value measurement is required for export")
            result = {
                "format": "openworkgraph.process-value-measurement-view.v1",
                "root_workflow_id": wid, "artifact": item,
                "latest_revision": latest, "integrity_ok": integrity,
                "access_revision": meta["access_revision"],
                "can_write": integrity and not meta["archived"] and role in {"owner", "editor", "admin"}
                             and "org:process:write" in p.scopes,
                "comparison": None, "root_resolution": None,
                "status": "not_saved" if item is None and integrity else "integrity_failed",
                "realized_savings_verified": False,
                "causal_effect_established": False,
                "execution_authorized": False,
            }
            if item is not None:
                measurement = item["measurement"]
                basis = self.catalog.resolve(conn, p, measurement["root_process"])
                result["root_resolution"] = basis
                if not basis["available"] or not basis["current"] or basis["independent_model_review"] != "reviewed_design":
                    result["status"] = "withheld_changed_or_unreviewed_process"
                else:
                    from shared.process_value_measurement import Measurement
                    comparison = compare(Measurement.model_validate(measurement))
                    result["comparison"] = comparison
                    result["status"] = comparison["status"]
        self.db.audit(organization_id=p.organization_id, principal_id=p.audit_id,
                      action="process.value_measurement.exported" if exporting else "process.value_measurement.read",
                      details={"workflow_id": wid, "revision": item["revision"] if item else 0})
        return result

    def save(self, p, wid, body):
        s = self.store
        s.require_scope(p, "org:process:read")
        s.require_scope(p, "org:process:write")
        if not body.user_confirmed or not body.organization_sharing_confirmed:
            raise Error("Explicitly review and confirm sharing the aggregate outcome comparison", 422)
        measurement = body.measurement.model_dump(mode="json")
        if measurement["organization_id"] != p.organization_id or measurement["root_process"]["workflow_id"] != wid:
            raise Error("Measurement root or organization differs from the selected process", 422)
        with s.write_transaction() as conn:
            meta = s._meta(conn, p, wid, lock=True)
            if meta["archived"] or s._role(conn, p, wid, meta) not in {"owner", "editor", "admin"}:
                raise Error("Current process editor permission required", 403)
            if meta["access_revision"] != body.expected_access_revision:
                raise Error("Process access changed; reload")
            if self.latest(conn, p, wid) != body.expected_revision:
                raise Error("Value comparison changed; reload")
            if body.expected_revision >= MAX_REVISIONS:
                raise Error("Value comparisons retain at most twenty revisions; nothing was pruned")
            basis = self.catalog.resolve(conn, p, measurement["root_process"])
            if not basis["available"] or not basis["current"] or basis["independent_model_review"] != "reviewed_design":
                raise Error("Current independently reviewed process revision required")
            saved = Saved(measurement=body.measurement, author_actor_id=p.actor_id,
                          saved_at=_now()).model_dump(mode="json")
            digest = content_digest(saved)
            revision = body.expected_revision + 1
            self.q(conn, "INSERT INTO workflow_knowledge(owner,workflow_id,revision,payload) VALUES (?,?,?,?)",
                   (namespace(p.organization_id), wid, revision, json.dumps({**saved, "content_sha256": digest})))
            s._audit(conn, p, "process.value_measurement.saved",
                     workflow_id=wid, revision=revision, content_sha256=digest,
                     baseline_case_count=measurement["baseline"]["case_count"],
                     intervention_case_count=measurement["intervention"]["case_count"])
        return {"revision": revision, "content_sha256": digest,
                "realized_savings_verified": False, "execution_authorized": False}

    def delete(self, p, wid, body):
        s = self.store
        s.require_scope(p, "org:process:read")
        s.require_scope(p, "org:process:write")
        if not body.user_confirmed:
            raise Error("Confirm deletion of all saved value measurement revisions", 422)
        with s.write_transaction() as conn:
            meta = s._meta(conn, p, wid, lock=True)
            if s._role(conn, p, wid, meta) not in {"owner", "admin"}:
                raise Error("Only the process owner or administrator can erase the measurement", 403)
            if meta["access_revision"] != body.expected_access_revision or self.latest(conn, p, wid) != body.expected_revision:
                raise Error("Process or measurement changed; reload")
            erase(s, conn, p.organization_id, wid)
            s._audit(conn, p, "process.value_measurement.deleted", workflow_id=wid)
        return {"deleted": True}
