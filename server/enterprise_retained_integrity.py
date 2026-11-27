"""Local exact structural integrity review and explicit feed containment.

This verifies the retained relational projection, not source authenticity,
upstream completeness, or a cryptographic proof of original event content.
No external reads, destructive edits, or remote publication withdrawal.
"""
import hmac
import re
import time
from pydantic import Field, StrictBool, StrictInt, ValidationError, model_validator

from . import enterprise_rest_feed as rest
from . import enterprise_ingestion_run_history as history
from . import event_log_workspace as ws

FORMAT = "owg_enterprise_retained_integrity_v1"
MAX_ROWS_PER_TABLE = 1000000
MAX_TARGETS = 5
HEX = "[0-9a-f]{64}"
LOG = re.compile(r"log_[a-f0-9]{16}\Z")


class Target(rest.Strict):
    profile_id: str = Field(pattern=r"^rest_[a-f0-9]{24}$")
    revision: StrictInt = Field(ge=1)


class Selection(rest.Strict):
    targets: list[Target] = Field(min_length=1, max_length=MAX_TARGETS)

    @model_validator(mode="after")
    def unique(self):
        if len({x.profile_id for x in self.targets}) != len(self.targets):
            raise ValueError("duplicate target")
        return self


class Apply(Selection):
    plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirm_exact_findings: StrictBool
    confirm_local_containment: StrictBool
    confirm_remote_coordination: StrictBool


def require(ok, code, status=409):
    if not ok:
        raise rest.FeedError(code, status)


def parse(model, value):
    try:
        return model.model_validate(value)
    except ValidationError:
        raise rest.FeedError("invalid_retained_integrity_request", 422) from None


def scalar(c, sql, *args):
    return int(c.execute(sql, list(args)).fetchone()[0])


def audit(c, p):
    log = p["log_id"]
    result = {"profile_id": p["profile_id"], "provider": p.get("provider", "rest"),
              "revision": p["revision"], "enabled": p["enabled"], "log_id": log,
              "checkpoint": p["checkpoint"], "hold": p.get("integrity_hold"),
              "active_or_unreconciled_intent": bool(p.get("run_id")),
              "counts": {}, "violations": {}, "status": "not_applicable",
              "source_authenticity_verified": False, "provider_completeness_verified": False}
    if not log:
        return result
    require(isinstance(log, str) and LOG.fullmatch(log), "invalid_retained_log_reference")
    row = c.execute("SELECT events,objects,rejected_events FROM logs WHERE log_id=?", [log]).fetchone()
    if row is None:
        result.update(status="violations", violations={"missing_log_metadata": 1})
        return result
    counts = dict(zip(("metadata_events", "metadata_objects", "metadata_rejected_events"), row))
    # Each table is probed with a bounded subquery before relational checks.
    # This bounds returned and inspected matching rows, not disk scan time.
    for table in ("events", "objects", "event_objects", "event_source_keys",
                  "event_source_namespaces"):
        counts[table] = scalar(c, f"SELECT count(*) FROM (SELECT 1 FROM {table} WHERE log_id=? LIMIT ?)",
                               log, MAX_ROWS_PER_TABLE+1)
    result["counts"] = counts
    if max(counts.values()) > MAX_ROWS_PER_TABLE:
        result.update(status="limit_exceeded", counts_are_lower_bounds=True)
        return result
    prefix = "src:" + p["profile_id"] + ":"
    case_pattern = prefix + HEX
    ns = p["profile_id"]
    v = {}
    def check(code, query, *params):
        v[code] = scalar(c, query, *params)
    v["event_metadata_count_difference"] = abs(counts["metadata_events"] - counts["events"])
    v["object_metadata_count_difference"] = abs(counts["metadata_objects"] - counts["objects"])
    v["rejected_events"] = counts["metadata_rejected_events"]
    v["empty_retained_dataset"] = int(counts["events"] == 0)
    v["namespace_population_difference"] = abs(counts["event_source_namespaces"] - 1)
    check("foreign_namespaces", """SELECT count(*) FROM event_source_namespaces WHERE log_id=?
        AND (source_namespace<>? OR case_namespace<>?)""", log, ns, ns)
    check("foreign_or_malformed_event_keys", """SELECT count(*) FROM event_source_keys WHERE log_id=?
        AND (source_namespace<>? OR NOT regexp_full_match(source_event_id,?))""", log, ns, HEX)
    check("duplicate_event_id_groups", """SELECT count(*) FROM (
        SELECT event_id FROM events WHERE log_id=? GROUP BY event_id HAVING count(*)<>1)""", log)
    check("duplicate_object_id_groups", """SELECT count(*) FROM (
        SELECT object_id FROM objects WHERE log_id=? GROUP BY object_id HAVING count(*)<>1)""", log)
    check("events_without_exact_source_key", """SELECT count(*) FROM events e WHERE e.log_id=?
        AND NOT EXISTS(SELECT 1 FROM event_source_keys k WHERE k.log_id=e.log_id AND k.event_id=e.event_id)""", log)
    check("keys_without_event", """SELECT count(*) FROM event_source_keys k WHERE k.log_id=?
        AND NOT EXISTS(SELECT 1 FROM events e WHERE e.log_id=k.log_id AND e.event_id=k.event_id)""", log)
    check("keys_without_namespace", """SELECT count(*) FROM event_source_keys k WHERE k.log_id=?
        AND NOT EXISTS(SELECT 1 FROM event_source_namespaces n WHERE n.log_id=k.log_id
        AND n.source_namespace=k.source_namespace)""", log)
    check("malformed_or_foreign_case_objects", """SELECT count(*) FROM objects WHERE log_id=?
        AND (object_type<>'source_case' OR NOT regexp_full_match(object_id,?))""", log, case_pattern)
    check("events_without_exact_case_link", """SELECT count(*) FROM (
        SELECT e.event_id FROM events e LEFT JOIN event_objects l ON l.log_id=e.log_id AND l.event_id=e.event_id
        LEFT JOIN objects o ON o.log_id=l.log_id AND o.object_id=l.object_id
        WHERE e.log_id=? GROUP BY e.event_id
        HAVING count(l.event_id)<>1 OR count(o.object_id)<>1 OR
        NOT bool_and(coalesce(o.object_type='source_case' AND regexp_full_match(o.object_id,?)
            AND l.qualifier='source_case',false)))""", log, case_pattern)
    check("dangling_event_links", """SELECT count(*) FROM event_objects l WHERE l.log_id=?
        AND NOT EXISTS(SELECT 1 FROM events e WHERE e.log_id=l.log_id AND e.event_id=l.event_id)""", log)
    check("dangling_object_links", """SELECT count(*) FROM event_objects l WHERE l.log_id=?
        AND NOT EXISTS(SELECT 1 FROM objects o WHERE o.log_id=l.log_id AND o.object_id=l.object_id)""", log)
    check("unlinked_objects", """SELECT count(*) FROM objects o WHERE o.log_id=?
        AND NOT EXISTS(SELECT 1 FROM event_objects l WHERE l.log_id=o.log_id AND l.object_id=o.object_id)""", log)
    check("malformed_resource_pseudonyms", """SELECT count(*) FROM events WHERE log_id=?
        AND resource<>'' AND NOT regexp_full_match(resource,?)""", log, HEX)
    # Event payloads are intentionally metadata-only. Case-level annotations
    # are supported elsewhere and do not count as projection violations.
    check("unexpected_event_attributes", """SELECT count(*) FROM events WHERE log_id=?
        AND trim(attributes)<>'{}'""", log)
    result["violations"] = {k: n for k, n in v.items() if n}
    result["status"] = "violations" if result["violations"] else "structurally_consistent"
    return result


def _plan(c, targets, action):
    items = []
    for t in sorted(targets, key=lambda x: x.profile_id):
        p = rest._get(c, t.profile_id)
        require(p["revision"] == t.revision, "profile_changed")
        report = audit(c, p)
        if action == "hold":
            eligible = report["status"] == "violations" and not p.get("integrity_hold")
        elif action == "release":
            eligible = (report["status"] == "structurally_consistent" and bool(p.get("integrity_hold"))
                        and not p.get("run_id") and not p["enabled"])
        else:
            eligible = False
        # The exact structural findings and full profile state are pinned;
        # this is not a content digest of arbitrary raw source payloads.
        item = {**report, "eligible": eligible,
                "profile_state_sha256": rest._digest(p),
                "next_revision": p["revision"]+1 if eligible else None}
        items.append(item)
    return {"format": FORMAT, "action": action, "targets": items,
            "all_eligible": all(x["eligible"] for x in items),
            "plan_sha256": rest._digest({"format": FORMAT, "action": action, "targets": items}),
            "provider_reads_performed": False, "source_data_modified": False,
            "remote_publications_withdrawn": False}


def inspect(value, *, action="hold"):
    require(action in ("hold", "release"), "invalid_integrity_action", 422)
    spec = parse(Selection, value)
    with ws._LOCK:
        c = rest._connect()
        try:
            return _plan(c, spec.targets, action)
        finally:
            c.close()


def apply(value, *, action="hold", _transaction_hook=None):
    require(action in ("hold", "release"), "invalid_integrity_action", 422)
    spec = parse(Apply, value)
    require(spec.confirm_exact_findings and spec.confirm_local_containment and
            spec.confirm_remote_coordination, "explicit_integrity_review_required", 403)
    with ws._LOCK:
        c = rest._connect()
        try:
            c.execute("BEGIN")
            try:
                plan = _plan(c, spec.targets, action)
                require(hmac.compare_digest(plan["plan_sha256"], spec.plan_sha256), "integrity_review_stale")
                require(plan["all_eligible"], "integrity_review_targets_ineligible")
                changed = []
                for item in plan["targets"]:
                    p = rest._get(c, item["profile_id"])
                    if action == "hold":
                        history.invalidate(c, p, "retained_integrity_hold")
                        p["integrity_hold"] = {"at": rest._iso(rest._now()), "plan_sha256": spec.plan_sha256,
                                               "violation_codes": sorted(item["violations"])}
                    else:
                        p.pop("integrity_hold", None)
                    p.update(revision=p["revision"]+1, enabled=False, status="disabled",
                             run_id=None, lease_until=0)
                    rest._put(c, p)
                    changed.append({"profile_id": p["profile_id"], "revision": p["revision"],
                                    "enabled": False, "integrity_hold": p.get("integrity_hold"),
                                    "retained_log_id": p["log_id"], "checkpoint": p["checkpoint"]})
                if _transaction_hook is not None:
                    _transaction_hook(c)
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise
            return {"format": FORMAT, "action": action, "plan_sha256": spec.plan_sha256,
                    "changed": changed, "provider_reads_performed": False,
                    "source_data_modified": False, "remote_publications_withdrawn": False,
                    "remote_cancellation_verified": False, "separate_enable_and_reauthorization_required": True}
        finally:
            c.close()


def inventory():
    with ws._LOCK:
        c = rest._connect()
        try:
            targets = []
            for (pid,) in c.execute("SELECT profile_id FROM enterprise_rest_profiles ORDER BY profile_id").fetchall():
                p = rest._get(c, pid)
                targets.append({"profile_id": pid, "provider": p.get("provider", "rest"),
                                "revision": p["revision"], "enabled": p["enabled"],
                                "log_id": p["log_id"], "integrity_hold": p.get("integrity_hold")})
            return {"format": FORMAT, "profiles": targets, "max_targets": MAX_TARGETS,
                    "max_rows_per_table": MAX_ROWS_PER_TABLE, "provider_reads_performed": False}
        finally:
            c.close()
