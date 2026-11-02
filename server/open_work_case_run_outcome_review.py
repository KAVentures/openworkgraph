from __future__ import annotations

"""Compare an imported automation's *reported* run with independently retained case evidence.

The run is manually selected by the local operator. Neither matching timestamps
nor a reported success establishes run-to-case identity, customer completion or
agent causality. No provider access, imports or durable case/run association.
"""
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import re
from typing import Any

from .open_work_case_declarations import CaseDeclarationError
from .open_work_case_status_checkpoint import _parse_utc

_FORMAT = "openworkgraph.local-reviewed-run-outcome-comparison.v1"
_TRIAGE = "openworkgraph.local-reviewed-outcome-evidence-triage.v1"
_PLAN = "openworkgraph.local-reviewed-outcome-action-plan.v1"
_DEPLOYMENT = re.compile(r"^dep_[0-9a-f]{16}$")
_RUN_ID = re.compile(r"^[\x20-\x7e]{1,128}$")
_STATES = frozenset({"succeeded", "failed", "handed_to_human", "cancelled", "running"})


def _opaque(secret: str, scope: str, purpose: str, value: Any) -> str:
    if not isinstance(secret, str) or not secret or not isinstance(scope, str) or not scope.startswith("owg:scope:"):
        raise CaseDeclarationError("invalid local run review scope")
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return hmac.new(secret.encode("utf-8"), (purpose + ":" + scope + ":" + payload).encode("utf-8"), hashlib.sha256).hexdigest()


def read_imported_run(*, deployment_id: str, run_id: str, scope: str, secret: str) -> dict[str, Any]:
    """Exact, bounded local DuckDB lookup; do not create an empty run database."""
    if (not isinstance(deployment_id, str) or not _DEPLOYMENT.fullmatch(deployment_id)
            or not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id)):
        raise CaseDeclarationError("invalid imported run selection")
    from . import automation_monitoring as monitoring

    path = monitoring.database_path()
    if not isinstance(path, Path) or path.is_symlink() or not path.is_file():
        raise CaseDeclarationError("no imported automation run available")
    # Use a read-only DuckDB connection: the normal monitoring connection
    # initializes/migrates schema and would mutate the database on a GET-like
    # comparison. A concurrent writer is a refusal, never a partial result.
    import duckdb
    with monitoring._LOCK:
        if path.is_symlink() or not path.is_file():
            raise CaseDeclarationError("no imported automation run available")
        try:
            conn = duckdb.connect(str(path), read_only=True)
            try:
                conn.execute("BEGIN TRANSACTION")
                row = conn.execute(
                    """SELECT r.status, r.started_at, r.finished_at,
                              r.outcome_reported_verified, d.runtime, r.outcome_source
                       FROM runs r JOIN deployments d ON d.deployment_id = r.deployment_id
                       WHERE r.deployment_id = ? AND r.run_id = ? LIMIT 1""",
                    [deployment_id, run_id],
                ).fetchone()
                steps = conn.execute(
                    """SELECT seq, step_id, activity, status, observed_at
                       FROM steps WHERE deployment_id = ? AND run_id = ?
                       ORDER BY seq LIMIT 101""", [deployment_id, run_id],
                ).fetchall()
                conn.execute("COMMIT")
            finally:
                conn.close()
        except duckdb.Error:
            raise CaseDeclarationError("imported automation run store unavailable") from None
    if row is None or row[0] not in _STATES or not isinstance(row[1], datetime):
        raise CaseDeclarationError("no imported automation run available")
    status, started, finished, reported, runtime, reported_source = row
    if (finished is not None and (not isinstance(finished, datetime) or finished < started)
            or status == "running" and finished is not None
            or reported is not None and type(reported) is not bool
            or runtime not in monitoring.RUNTIMES
            or not isinstance(reported_source, str) or len(reported_source) > 80
            or len(steps) > 100):
        raise CaseDeclarationError("invalid retained automation run")
    minimized_steps = []
    for index, step_id, activity, step_status, observed_at in steps:
        if (type(index) is not int or index != len(minimized_steps)
                or not isinstance(step_id, str) or len(step_id) > 128
                or not isinstance(activity, str) or len(activity) > 200
                or step_status not in monitoring.STEP_STATUSES
                or not isinstance(observed_at, datetime)):
            raise CaseDeclarationError("invalid retained automation run steps")
        minimized_steps.append([index, step_id, activity, step_status, observed_at.isoformat()])
    started_at = started.replace(tzinfo=timezone.utc).isoformat()
    finished_at = finished.replace(tzinfo=timezone.utc).isoformat() if finished else None
    identity = [deployment_id, run_id]
    # Sensitive report labels/steps are used only inside the keyed digest,
    # never included in the response. Replacing a run's steps or outcome-source
    # label must invalidate the double-read consistency check too.
    snapshot = [deployment_id, run_id, status, started_at, finished_at,
                reported, runtime, reported_source, minimized_steps]
    return {
        "run_ref": "owg:run:" + _opaque(secret, scope, "selected-imported-run", identity)[:32],
        "run_snapshot_fingerprint": _opaque(secret, scope, "imported-run-snapshot", snapshot),
        "reported_run_status": status,
        "reported_outcome_verified": reported,
        "reported_started_at": started_at,
        "reported_finished_at": finished_at,
        "reported_runtime": runtime,
        "reported_by_automation_not_verified_by_owg": True,
    }


def compose_run_outcome_review(
    run: dict[str, Any], triage: dict[str, Any], action_plan: dict[str, Any],
) -> dict[str, Any]:
    """Produce a conservative case comparison, never a success/causality decision."""
    if (not isinstance(triage, dict) or triage.get("format") != _TRIAGE
            or not isinstance(action_plan, dict) or action_plan.get("format") != _PLAN
            or action_plan.get("work_item_ref") != triage.get("work_item_ref")
            or triage.get("customer_outcome_verified") is not False
            or triage.get("source_read_performed") is not False):
        raise CaseDeclarationError("invalid admitted run outcome review")
    rows, plans = triage.get("case_reviews"), action_plan.get("cases")
    if (not isinstance(rows, list) or not isinstance(plans, list)
            or not 1 <= len(rows) <= 4 or len(rows) != len(plans)
            or triage.get("included_case_count") != len(rows)
            or rows[-1].get("work_item_ref") != triage.get("work_item_ref")):
        raise CaseDeclarationError("incomplete reviewed case evidence")
    for row, plan in zip(rows, plans):
        if (not isinstance(row, dict) or not isinstance(plan, dict)
                or row.get("work_item_ref") != plan.get("work_item_ref")):
            raise CaseDeclarationError("conflicting reviewed case evidence")
    root = rows[-1]
    root_plan = plans[-1]
    provider = root.get("historical_last_provider_state")
    human = root.get("latest_human_assessed_status")
    checked = root.get("last_historical_provider_check_at")
    finished = run.get("reported_finished_at")
    if (provider not in ("paid", "unknown") or human not in ("met", "not_met", "unknown")
            or type(root.get("provider_check_count")) is not int
            or type(root.get("disputed_check_count")) is not int
            or root["provider_check_count"] < root["disputed_check_count"]
            or root["disputed_check_count"] < 0
            or root.get("owner_declared_expected_outcome_code") not in (
                "human_review_recorded", "source_state_rechecked",
                "operation_receipt_reviewed", "stripe_invoice_paid_reported"
            ) or run.get("reported_run_status") not in _STATES):
        raise CaseDeclarationError("invalid historical run comparison state")
    if checked is None:
        timing = "no_historical_provider_check"
    elif finished is None:
        timing = "run_has_no_reported_finish"
    else:
        timing = ("on_or_after_reported_finish" if _parse_utc(checked) >= _parse_utc(finished)
                  else "before_reported_finish")
    payment_goal = root["owner_declared_expected_outcome_code"] == "stripe_invoice_paid_reported"
    paid_after = payment_goal and provider == "paid" and timing == "on_or_after_reported_finish"
    pending = root_plan.get("prerequisites_with_unresolved_review")
    if not isinstance(pending, list) or any(not isinstance(x, str) for x in pending):
        raise CaseDeclarationError("invalid prerequisite review status")
    return {
        "format": _FORMAT,
        "work_item_ref": triage["work_item_ref"],
        "selected_run": run,
        "owner_declared_expected_outcome_code": root["owner_declared_expected_outcome_code"],
        "latest_human_assessed_status": human,
        "historical_last_provider_state": provider,
        "provider_check_count": root["provider_check_count"],
        "disputed_check_count": root["disputed_check_count"],
        "last_historical_provider_check_at": checked,
        "provider_check_vs_reported_run_finish": timing,
        "narrow_historical_paid_observed_after_reported_finish": bool(paid_after),
        "unresolved_prerequisite_refs": pending,
        "unscanned_prerequisite_count": triage["unscanned_prerequisite_count"],
        "all_prerequisites_reviewed": triage["all_declared_prerequisites_included"],
        "review_action_plan": action_plan,
        "operator_selected_run_case_association_only": True,
        "run_case_identity_independently_verified": False,
        "imported_run_status_is_self_reported": True,
        "reported_run_times_are_not_independently_verified": True,
        "historical_provider_state_is_not_current": True,
        "separate_local_stores_not_atomic_snapshot": True,
        "current_provider_operation_verified": False,
        "customer_outcome_verified": False,
        "business_obligation_verified": False,
        "agent_caused_outcome_verified": False,
        "agent_execution_authorized": False,
        "source_read_performed": False,
        "source_write_performed": False,
        "not_synced": True,
    }


__all__ = ["read_imported_run", "compose_run_outcome_review"]
