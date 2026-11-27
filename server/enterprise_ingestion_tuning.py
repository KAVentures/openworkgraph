"""Reviewed, atomic operational tuning for retained enterprise feeds.

No source reads, no credential replacement, no checkpoint rewind, no changed
case/event semantics. A changed revision invalidates existing local/Gateway
dispatch pins and publication artifacts; operators must separately reauthorize.
"""
from datetime import timedelta
import hmac
import json
import re
import time

from pydantic import Field, StrictBool, StrictInt, ValidationError, model_validator

from . import enterprise_rest_feed as rest
from . import event_log_workspace as ws
from shared.enterprise_connector_contract import PROVIDERS

FORMAT = "owg_enterprise_ingestion_tuning_v1"
MAX_TARGETS = 20
MAX_HISTORY = 100
SCHEMA = """CREATE TABLE IF NOT EXISTS enterprise_ingestion_tuning_receipts (
    receipt_id VARCHAR PRIMARY KEY, created_at VARCHAR NOT NULL, definition VARCHAR NOT NULL
)"""
PROFILE = re.compile(r"rest_[a-f0-9]{24}\Z")
PAGE_LIMITS = {"rest": (1, 500), "google": (1, 500), "sql": (1, 500),
               "slack": (1, 500), "salesforce": (200, 2000), "servicenow": (1, 500),
               "sap": (1, 500), "dataverse": (1, 500), "netsuite": (1, 1000),
               "bigquery": (1, 1000)}
LAG_PROVIDERS = frozenset({"google", "slack", "salesforce", "servicenow", "dataverse",
                          "hubspot", "databricks", "snowflake", "netsuite", "workday",
                          "oracle_fusion", "bigquery", "zendesk"})
FIELDS = ("window_hours", "overlap_minutes", "ingestion_lag_minutes", "page_size", "lag_days")


class Selection(rest.Strict):
    profile_id: str = Field(pattern=r"^rest_[a-f0-9]{24}$")
    revision: StrictInt = Field(ge=1)
    window_hours: StrictInt | None = Field(default=None, ge=1, le=168)
    overlap_minutes: StrictInt | None = Field(default=None, ge=0, le=1440)
    ingestion_lag_minutes: StrictInt | None = Field(default=None, ge=0, le=1440)
    page_size: StrictInt | None = Field(default=None, ge=1, le=2000)
    lag_days: StrictInt | None = Field(default=None, ge=0, le=7)

    @model_validator(mode="after")
    def selected(self):
        if not any(getattr(self, name) is not None for name in FIELDS):
            raise ValueError("select at least one operational field")
        return self


class Preview(rest.Strict):
    targets: list[Selection] = Field(min_length=1, max_length=MAX_TARGETS)

    @model_validator(mode="after")
    def unique(self):
        if len({r.profile_id for r in self.targets}) != len(self.targets):
            raise ValueError("duplicate feed selection")
        return self


class Apply(Preview):
    plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirm_exact_changes: StrictBool
    confirm_dispatch_reauthorization: StrictBool
    confirm_workers_reconciled: StrictBool


def require(ok, code, status=409):
    if not ok:
        raise rest.FeedError(code, status)


def parse(model, value):
    try:
        return model.model_validate(value)
    except ValidationError:
        raise rest.FeedError("invalid_ingestion_tuning_request", 422) from None


def supported(provider):
    require(provider in PROVIDERS, "unknown_enterprise_connector_provider", 422)
    fields = {"window_hours": {"min": 1, "max": 168, "step": 1},
              "overlap_minutes": {"min": 0, "max": 1440, "step": 1}}
    if provider == "sap":
        fields.update(window_hours={"min": 24, "max": 168, "step": 24},
                      overlap_minutes={"min": 0, "max": 1440, "step": 1440},
                      lag_days={"min": 0, "max": 7, "step": 1})
    if provider in LAG_PROVIDERS:
        fields["ingestion_lag_minutes"] = {"min": 0, "max": 1440, "step": 1}
    if provider in PAGE_LIMITS:
        low, high = PAGE_LIMITS[provider]
        fields["page_size"] = {"min": low, "max": high, "step": 1}
    return fields


def proposed(p, selection):
    provider = p.get("provider", "rest")
    fields = supported(provider)
    cfg = p["config"]
    before = {}
    for key, rules in fields.items():
        value = cfg.get(key)
        require(type(value) is int and rules["min"] <= value <= rules["max"]
                and (value - rules["min"]) % rules["step"] == 0,
                "unsupported_existing_ingestion_configuration")
        before[key] = value
    changes = {key: getattr(selection, key) for key in FIELDS if getattr(selection, key) is not None}
    require(set(changes) <= set(fields), "unsupported_provider_tuning_field", 422)
    after = {**before, **changes}
    for key, value in after.items():
        rules = fields[key]
        require(rules["min"] <= value <= rules["max"] and (value - rules["min"]) % rules["step"] == 0,
                "invalid_provider_tuning_value", 422)
    require(after["overlap_minutes"] < after["window_hours"] * 60,
            "overlap_must_be_shorter_than_window", 422)
    require(after != before, "ingestion_tuning_no_change", 422)
    updated = {**cfg, **after}
    if provider == "sap":
        # Preserve provider's redundant public day settings and runtime hours.
        updated.update(window_days=after["window_hours"] // 24,
                       overlap_days=after["overlap_minutes"] // 1440)
    return before, after, updated


def next_window(p, cfg, now):
    """Illustrative next *change* window; does not reserve or advance it."""
    anchor = rest._stamp(p["checkpoint"] or cfg["initial_since"])
    start = anchor - timedelta(minutes=cfg["overlap_minutes"]) if p["checkpoint"] else anchor
    candidate = dict(p, config=cfg)
    provider = p.get("provider", "rest")
    adapter = rest._adapter(provider)
    try:
        if provider == "google":
            ceiling = adapter.review_cutoff(candidate, now, anchor)
        elif adapter is not None and provider != "sql":
            ceiling = adapter.cutoff(candidate, now, anchor)
        else:
            ceiling = now
    except rest.FeedError as error:
        return {"since": rest._iso(start), "until": None, "status": error.code,
                "anchor": rest._iso(anchor), "illustrative": True}
    end = min(ceiling, anchor + timedelta(hours=cfg["window_hours"]))
    return {"since": rest._iso(start), "until": rest._iso(end) if end > anchor else None,
            "anchor": rest._iso(anchor), "status": "window_available" if end > anchor else "no_new_window",
            "illustrative": True}


def _target(c, selection, now):
    p = rest._get(c, selection.profile_id)
    require(p["revision"] == selection.revision, "profile_changed")
    require(p["status"] != "dataset_deleted", "retained_dataset_deleted_create_new_profile")
    # Refuse even expired lease intents: an operator must reconcile them
    # explicitly using existing state controls; never silently clear a run.
    require(not p.get("run_id") and not p.get("lease_until"), "ingestion_tuning_run_unreconciled")
    rest._check_binding(p)  # local validation only, no provider access
    log = p["log_id"]
    population = ws._check_log(c, log) if log else None
    before, after, cfg = proposed(p, selection)
    # The full private profile state and retained population pin are hashed,
    # never returned. Includes checkpoint, attempts, receipts, cooldown and
    # binding, so concurrent dispatch/reconfiguration stales the review.
    state = rest._digest({"profile": p, "retained": population})
    item = {"profile_id": p["profile_id"], "provider": p.get("provider", "rest"),
            "name": p["config"]["name"], "revision": p["revision"],
            "next_revision": p["revision"] + 1, "enabled": p["enabled"],
            "checkpoint": p["checkpoint"], "retained_log_id": log,
            "retained_events": population["events"] if population else 0,
            "before": before, "after": after,
            "before_next_window": next_window(p, p["config"], now),
            "after_next_window": next_window(p, cfg, now),
            "state_sha256": state, "source_scope_changed": False,
            "checkpoint_changed": False, "retained_data_changed": False,
            "old_dispatch_pins_invalidated": True,
            "requires_separate_schedule_and_gateway_reauthorization": True}
    return p, cfg, item


def _plan(c, targets, now):
    records = [_target(c, row, now) for row in sorted(targets, key=lambda x: x.profile_id)]
    items = [r[2] for r in records]
    # The preview clock is not included in the hash: the illustrative next
    # window naturally moves, but exact operational state and changes must
    # remain unchanged between preview and apply.
    stable = [{k: v for k, v in item.items()
               if k not in ("before_next_window", "after_next_window")} for item in items]
    digest = rest._digest({"format": FORMAT, "targets": stable})
    return records, {"format": FORMAT, "plan_sha256": digest, "targets": items,
                     "target_count": len(items), "previewed_at": rest._iso(now),
                     "provider_reads_performed": False, "execution_authorized": False,
                     "existing_publications_removed": False}


def preview(value, *, now=None):
    spec = parse(Preview, value)
    with ws._LOCK:
        c = rest._connect()
        try:
            _, result = _plan(c, spec.targets, now or rest._now())
            return result
        finally:
            c.close()


def apply(value, *, now=None, _transaction_hook=None):
    spec = parse(Apply, value)
    require(spec.confirm_exact_changes and spec.confirm_dispatch_reauthorization
            and spec.confirm_workers_reconciled, "explicit_ingestion_tuning_approvals_required", 403)
    now = now or rest._now()
    with ws._LOCK:
        c = rest._connect()
        try:
            c.execute(SCHEMA)
            # Replay is counts-only, never reapplies a prior change or restores
            # old pins. A pruned receipt can still not replay a changed revision.
            row = c.execute("SELECT definition FROM enterprise_ingestion_tuning_receipts WHERE receipt_id=?",
                            [spec.plan_sha256]).fetchone()
            if row:
                previous = json.loads(row[0])
                require(previous["request_sha256"] == rest._digest(
                    [x.model_dump() for x in sorted(spec.targets, key=lambda x: x.profile_id)]),
                    "ingestion_tuning_receipt_request_mismatch")
                return {**previous, "replayed": True, "changes_applied_this_call": 0}
            c.execute("BEGIN")
            try:
                records, plan = _plan(c, spec.targets, now)
                require(hmac.compare_digest(plan["plan_sha256"], spec.plan_sha256),
                        "ingestion_tuning_review_stale")
                summaries = []
                for p, cfg, item in records:
                    # No change to credential key, status, enabled, checkpoint,
                    # retained log, cooldown, principal/schema pins or read receipt.
                    # Revision alone invalidates outstanding dispatch and grant pins.
                    saved = dict(p, config=cfg, revision=p["revision"] + 1)
                    rest._put(c, saved)
                    summaries.append({"profile_id": item["profile_id"], "provider": item["provider"],
                                      "previous_revision": item["revision"],
                                      "revision": item["next_revision"], "before": item["before"],
                                      "after": item["after"], "checkpoint_preserved": True,
                                      "retained_log_preserved": True, "enabled": item["enabled"]})
                receipt = {"format": FORMAT, "receipt_id": spec.plan_sha256,
                           "request_sha256": rest._digest([x.model_dump() for x in
                               sorted(spec.targets, key=lambda x: x.profile_id)]),
                           "applied_at": rest._iso(now), "targets": summaries,
                           "target_count": len(summaries), "replayed": False,
                           "changes_applied_this_call": len(summaries),
                           "provider_reads_performed": False,
                           "existing_publications_removed": False,
                           "requires_separate_schedule_and_gateway_reauthorization": True}
                c.execute("INSERT INTO enterprise_ingestion_tuning_receipts VALUES (?,?,?)",
                          [spec.plan_sha256, receipt["applied_at"], json.dumps(receipt, separators=(",", ":"))])
                c.execute("""DELETE FROM enterprise_ingestion_tuning_receipts WHERE receipt_id IN
                    (SELECT receipt_id FROM enterprise_ingestion_tuning_receipts
                     ORDER BY created_at DESC, receipt_id DESC OFFSET ?)""", [MAX_HISTORY])
                if _transaction_hook is not None:
                    _transaction_hook(c)
                c.execute("COMMIT")
                return receipt
            except Exception:
                c.execute("ROLLBACK")
                raise
        finally:
            c.close()


def inventory():
    """Counts-only local history and allowed operational fields; no source access."""
    with ws._LOCK:
        c = rest._connect()
        try:
            present = c.execute("""SELECT 1 FROM information_schema.tables
                WHERE table_name='enterprise_ingestion_tuning_receipts'""").fetchone()
            receipts = [json.loads(row[0]) for row in c.execute(
                """SELECT definition FROM enterprise_ingestion_tuning_receipts
                   ORDER BY created_at DESC,receipt_id DESC LIMIT ?""", [MAX_HISTORY]).fetchall()] if present else []
            profiles = []
            for (definition,) in c.execute("SELECT definition FROM enterprise_rest_profiles ORDER BY profile_id").fetchall():
                p = json.loads(definition)
                provider = p.get("provider", "rest")
                if provider not in PROVIDERS:
                    continue
                rules = supported(provider)
                profiles.append({"profile_id": p["profile_id"], "provider": provider,
                                 "name": p["config"]["name"], "revision": p["revision"],
                                 "enabled": p["enabled"], "status": p["status"],
                                 "checkpoint": p["checkpoint"],
                                 "unreconciled_run": bool(p.get("run_id") or p.get("lease_until")),
                                 "fields": rules, "values": {k: p["config"].get(k) for k in rules}})
            return {"format": FORMAT, "profiles": profiles, "receipts": receipts,
                    "max_targets": MAX_TARGETS, "max_history": MAX_HISTORY,
                    "provider_reads_performed": False}
        finally:
            c.close()
