"""Cross-deployment release evidence inventory (#370 R18).

A bounded, read-only view of one local monitoring database snapshot. Distinguish
operator declarations, reported specifications, attached designs and per-run
specification claims; none of these establishes actual deployed artifact state.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from . import automation_monitoring as monitoring
from . import automation_release_inventory as releases

FORMAT = "openworkgraph.deployment-portfolio-evidence.v1"


def _row(row, cutoff):
    (ident, name, runtime, spec, design, revision, stage, label, artifact,
     reviewed_spec, reviewed_design, declared_at, total, matching, other,
     unknown, latest, recent, recent_matching, recent_other, recent_unknown) = row
    current = {"revision": revision, "stage": stage, "release_label": label,
               "artifact_sha256": artifact, "declared_at": monitoring._iso(declared_at),
               "reported_spec_sha256": reviewed_spec, "design_sha256": reviewed_design} if revision else None
    pins_match = bool(current and spec and design and reviewed_spec == spec and reviewed_design == design)
    flags = []
    if current is None:
        flags.append("no_release_declaration")
    elif not pins_match:
        flags.append("release_pins_missing_or_changed")
    if not spec:
        flags.append("reported_spec_missing")
    if not design:
        flags.append("reviewed_design_missing")
    if spec and design and spec != design:
        flags.append("reported_spec_design_mismatch")
    if current and stage in ("pilot_reported", "production_reported") and not artifact:
        flags.append("artifact_digest_missing")
    if not matching:
        flags.append("no_runs_claiming_current_spec")
    if other:
        flags.append("runs_claiming_other_spec")
    if unknown:
        flags.append("runs_without_comparable_spec")
    if not recent:
        flags.append("no_recent_retained_runs")
    elif not recent_matching:
        flags.append("no_recent_runs_claiming_current_spec")
    if recent_other:
        flags.append("recent_runs_claiming_other_spec")
    if recent_unknown:
        flags.append("recent_runs_without_comparable_spec")
    return {
        "deployment_id": ident, "name": name, "runtime": runtime,
        "reported_spec_sha256": spec, "design_sha256": design,
        "current_declaration": current, "declaration_pins_match_current_metadata": pins_match,
        "latest_retained_run_start": monitoring._iso(latest),
        "runs": {"retained": total, "claiming_current_spec": matching,
                 "claiming_other_spec": other, "without_comparable_spec": unknown},
        "recent_runs": {"retained": recent, "claiming_current_spec": recent_matching,
                        "claiming_other_spec": recent_other, "without_comparable_spec": recent_unknown},
        "review_flags": flags,
        "interpretation": "review_required" if flags else "no_listed_evidence_gaps",
    }


def portfolio(*, recent_days: int = 30) -> dict:
    if type(recent_days) is not int or not 1 <= recent_days <= 365:
        raise monitoring.MonitoringError("recent_days must be from 1 to 365")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now - timedelta(days=recent_days)
    # One grouped query, one snapshot, no N+1 run scans or per-run materialization.
    # Future timestamps are excluded from the recent window, not silently counted.
    with monitoring._LOCK:
        conn = monitoring._connect()
        try:
            releases.initialize(conn)
            rows = conn.execute("""
                WITH latest_review AS (
                    SELECT * FROM deployment_release_reviews
                    QUALIFY row_number() OVER (PARTITION BY deployment_id ORDER BY revision DESC)=1
                ), counts AS (
                    SELECT d.deployment_id,
                        count(r.run_id) AS total,
                        count(r.run_id) FILTER (WHERE d.reported_spec_sha256 IS NOT NULL
                            AND r.reported_spec_sha256=d.reported_spec_sha256) AS matching,
                        count(r.run_id) FILTER (WHERE d.reported_spec_sha256 IS NOT NULL
                            AND r.reported_spec_sha256<>d.reported_spec_sha256) AS other,
                        count(r.run_id) FILTER (WHERE d.reported_spec_sha256 IS NULL
                            OR r.reported_spec_sha256 IS NULL) AS unknown,
                        max(r.started_at) AS latest,
                        count(r.run_id) FILTER (WHERE r.started_at>=? AND r.started_at<=?) AS recent,
                        count(r.run_id) FILTER (WHERE r.started_at>=? AND r.started_at<=?
                            AND d.reported_spec_sha256 IS NOT NULL
                            AND r.reported_spec_sha256=d.reported_spec_sha256) AS recent_matching,
                        count(r.run_id) FILTER (WHERE r.started_at>=? AND r.started_at<=?
                            AND d.reported_spec_sha256 IS NOT NULL
                            AND r.reported_spec_sha256<>d.reported_spec_sha256) AS recent_other,
                        count(r.run_id) FILTER (WHERE r.started_at>=? AND r.started_at<=?
                            AND (d.reported_spec_sha256 IS NULL OR r.reported_spec_sha256 IS NULL)) AS recent_unknown
                    FROM deployments d LEFT JOIN runs r ON d.deployment_id=r.deployment_id
                    GROUP BY d.deployment_id
                )
                SELECT d.deployment_id,d.name,d.runtime,d.reported_spec_sha256,d.design_sha256,
                       l.revision,l.stage,l.release_label,l.artifact_sha256,l.reported_spec_sha256,
                       l.design_sha256,l.declared_at,c.total,c.matching,c.other,c.unknown,c.latest,
                       c.recent,c.recent_matching,c.recent_other,c.recent_unknown
                FROM deployments d JOIN counts c ON d.deployment_id=c.deployment_id
                LEFT JOIN latest_review l ON l.deployment_id=d.deployment_id
                ORDER BY d.deployment_id LIMIT ?
            """, [cutoff, now] * 4 + [monitoring.MAX_DEPLOYMENTS + 1]).fetchall()
        finally:
            conn.close()
    if len(rows) > monitoring.MAX_DEPLOYMENTS:
        raise monitoring.MonitoringError("deployment inventory exceeds supported limit; no partial inventory returned")
    entries = [_row(row, cutoff) for row in rows]
    flags = Counter(flag for entry in entries for flag in entry["review_flags"])
    stages = Counter(entry["current_declaration"]["stage"] if entry["current_declaration"] else "undeclared"
                     for entry in entries)
    return {
        "format": FORMAT, "as_of": monitoring._iso(now),
        "recent_window": {"days": recent_days, "start": monitoring._iso(cutoff),
                          "end": monitoring._iso(now), "basis": "reported_run_start_inclusive"},
        "deployment_count": len(entries),
        "deployments_with_review_flags": sum(bool(e["review_flags"]) for e in entries),
        "review_flag_counts": dict(sorted(flags.items())),
        "declaration_stage_counts": dict(sorted(stages.items())),
        "deployments": entries,
        "scope": "all_retained_local_deployments",
        "remote_deployment_state_verified": False,
        "executable_artifact_verified": False,
        "business_outcomes_verified": False,
        "execution_authorized": False,
        "interpretation": (
            "Counts describe retained run reports and operator declarations only. "
            "Missing or old reports do not prove downtime; matching metadata does "
            "not prove what executed, release readiness or business outcomes."
        ),
    }
