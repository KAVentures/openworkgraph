"""Monitoring for customer-run automations built from reviewed designs.

Automations running elsewhere (n8n, the AI SDK or Agents SDK starters, or any
other runtime) export run reports; the user imports them here. OWG stores
the reported runs and steps locally and reports success, human handoff and
failure rates with intervals, step hotspots, drift between a recent window and
an earlier baseline, and how executed steps compare with the reviewed design.
Everything is reported by the automation itself: OWG verifies no outcome,
executes nothing and grants no permission.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import secrets
import threading
from typing import Any

from shared.event_log_formats import parse_timestamp

from .db import DATA_DIR
from .event_log_analytics import _align, _wilson, risk_difference
from .event_log_cohorts import median_difference

FORMAT = "openworkgraph.automation-run-report.v1"
SPEC_FORMAT = "openworkgraph.agent-starter.v1"
RUNTIME_SPEC_FORMAT = "openworkgraph.agent-runtime-project.v1"
MAX_GRAPH_PATHS = 128
RUN_STATUSES = ("succeeded", "failed", "handed_to_human", "cancelled", "running")
STEP_STATUSES = ("completed", "failed", "skipped", "handed_to_human")
RUNTIMES = ("n8n", "ai-sdk", "openai-agents", "claude-agent-sdk", "other")
MAX_REPORT_BYTES = 64 * 1024 * 1024
MAX_RUNS_PER_REPORT = 50000
MAX_STEPS_PER_RUN = 100
MAX_DEPLOYMENTS = 50
_ID = re.compile(r"^[\x20-\x7e]{1,128}$")
_DEPLOYMENT = re.compile(r"^dep_[0-9a-f]{16}$")
_LOCK = threading.RLock()
_SCHEMA = """
CREATE TABLE IF NOT EXISTS deployments (
  deployment_id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL, runtime VARCHAR NOT NULL,
  created_at TIMESTAMP NOT NULL, design_sha256 VARCHAR, design_steps VARCHAR,
  reported_spec_sha256 VARCHAR, design_graph VARCHAR
);
CREATE TABLE IF NOT EXISTS runs (
  deployment_id VARCHAR NOT NULL, run_id VARCHAR NOT NULL, started_at TIMESTAMP NOT NULL,
  finished_at TIMESTAMP, status VARCHAR NOT NULL, outcome_reported_verified BOOLEAN,
  outcome_source VARCHAR NOT NULL, PRIMARY KEY (deployment_id, run_id)
);
CREATE TABLE IF NOT EXISTS steps (
  deployment_id VARCHAR NOT NULL, run_id VARCHAR NOT NULL, seq INTEGER NOT NULL, step_id VARCHAR NOT NULL,
  activity VARCHAR NOT NULL, status VARCHAR NOT NULL, observed_at TIMESTAMP NOT NULL
);
"""
REPORTED_ONLY = {
    "reported_by_the_automation_not_verified_by_owg": True,
    "execution_authorized": False,
    "causal_claims": False,
}


class MonitoringError(ValueError):
    """Safe user-facing error that never echoes report content."""


def database_path() -> Path:
    return DATA_DIR / "automation_runs.duckdb"


def _connect():
    import duckdb

    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(path))
    conn.execute("SET enable_progress_bar = false")
    conn.execute(_SCHEMA)
    # Older reports did not retain their declared spec hash; leave those unknown.
    conn.execute('ALTER TABLE deployments ADD COLUMN IF NOT EXISTS reported_spec_sha256 VARCHAR')
    conn.execute('ALTER TABLE deployments ADD COLUMN IF NOT EXISTS design_graph VARCHAR')
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return conn


def _naive(stamp: datetime) -> datetime:
    return stamp.astimezone(timezone.utc).replace(tzinfo=None)


def _iso(stamp: datetime | None) -> str | None:
    return stamp.isoformat() + "Z" if stamp else None


def _time(value: Any, what: str) -> datetime:
    stamp = parse_timestamp(value)
    if stamp is None:
        raise MonitoringError(f"{what} must be a timezone-qualified ISO time")
    return _naive(stamp)


def _identifier(value: Any, what: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise MonitoringError(f"{what} must be 1 to 128 printable ASCII characters")
    return value


def _label(value: Any, what: str, limit: int = 120) -> str:
    if not isinstance(value, str) or not 0 < len(value.strip()) <= limit or any(ord(c) < 32 for c in value):
        raise MonitoringError(f"{what} must be a short label")
    return value.strip()


# ---------------------------------------------------------------- parsing

def parse_report(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict) or document.get("format") != FORMAT:
        raise MonitoringError(f"run report must use format {FORMAT}")
    if set(document) - {"format", "deployment", "runs"}:
        raise MonitoringError("unknown run report fields")
    deployment = document.get("deployment") or {}
    if not isinstance(deployment, dict) or set(deployment) - {"name", "runtime", "workflow_spec_sha256"}:
        raise MonitoringError("deployment has name, runtime and workflow_spec_sha256")
    runtime = deployment.get("runtime", "other")
    if runtime not in RUNTIMES:
        raise MonitoringError("runtime must be one of " + ", ".join(RUNTIMES))
    spec_hash = deployment.get("workflow_spec_sha256")
    if spec_hash is not None and not (isinstance(spec_hash, str) and re.fullmatch(r"[0-9a-f]{64}", spec_hash)):
        raise MonitoringError("workflow_spec_sha256 must be a lowercase SHA-256 hex digest")
    runs = document.get("runs")
    if not isinstance(runs, list) or not 0 < len(runs) <= MAX_RUNS_PER_REPORT:
        raise MonitoringError(f"a report holds 1 to {MAX_RUNS_PER_REPORT} runs")
    parsed, seen = [], set()
    for run in runs:
        if not isinstance(run, dict) or set(run) - {"run_id", "started_at", "finished_at", "status", "steps", "outcome"}:
            raise MonitoringError("each run has run_id, started_at, finished_at, status, steps and outcome")
        run_id = _identifier(run.get("run_id"), "run_id")
        if run_id in seen:
            raise MonitoringError("run_id values must be unique within a report")
        seen.add(run_id)
        status = run.get("status")
        if status not in RUN_STATUSES:
            raise MonitoringError("run status must be one of " + ", ".join(RUN_STATUSES))
        started = _time(run.get("started_at"), "started_at")
        finished = None if run.get("finished_at") in (None, "") else _time(run.get("finished_at"), "finished_at")
        if finished is not None and finished < started:
            raise MonitoringError("finished_at must not be before started_at")
        if status == "running" and finished is not None:
            raise MonitoringError("a running run has no finished_at")
        outcome = run.get("outcome") or {}
        if not isinstance(outcome, dict) or set(outcome) - {"verified", "source"}:
            raise MonitoringError("outcome has verified and source")
        verified = outcome.get("verified")
        if verified is not None and not isinstance(verified, bool):
            raise MonitoringError("outcome.verified must be true or false")
        source = "" if outcome.get("source") in (None, "") else _label(outcome.get("source"), "outcome.source", 80)
        steps = run.get("steps") or []
        if not isinstance(steps, list) or len(steps) > MAX_STEPS_PER_RUN:
            raise MonitoringError(f"a run holds at most {MAX_STEPS_PER_RUN} steps")
        parsed_steps = []
        for step in steps:
            if not isinstance(step, dict) or set(step) - {"step_id", "activity", "status", "at"}:
                raise MonitoringError("each step has step_id, activity, status and at")
            if step.get("status") not in STEP_STATUSES:
                raise MonitoringError("step status must be one of " + ", ".join(STEP_STATUSES))
            parsed_steps.append((_identifier(step.get("step_id"), "step_id"), _label(step.get("activity"), "activity", 200),
                                 step["status"], _time(step.get("at"), "step at")))
        parsed.append({"run_id": run_id, "started_at": started, "finished_at": finished, "status": status,
                       "verified": verified, "source": source, "steps": parsed_steps})
    name = _label(deployment.get("name", "Automation"), "deployment name")
    return {"name": name, "runtime": runtime, "spec_hash": spec_hash, "runs": parsed}


def spec_digest(spec: Any) -> str:
    """The starter's own digest: UTF-8 json.dumps(sort_keys=True, allow_nan=False)."""
    try:
        return sha256(json.dumps(spec, sort_keys=True, allow_nan=False).encode("utf-8")).hexdigest()
    except (TypeError, ValueError):
        raise MonitoringError("workflow specification must be finite JSON") from None


def parse_runtime_design(spec: Any) -> dict[str, Any]:
    """Validate a portable snapshot without asserting current catalog approval."""
    from shared.process_agent_project import build_runtime_spec
    try:
        packet = {key: spec[key] for key in ('workflow_id', 'revision', 'content_sha256', 'process_model',
                                            'execution_authorization')}
        packet['format'] = 'openworkgraph.versioned-process-export.v1'
        if 'organization_catalog_reference' in spec:
            packet['organization_catalog_reference'] = spec['organization_catalog_reference']
        normalized = build_runtime_spec(packet)
        if spec.get('live_business_tools') != [] or any(spec.get(key) != normalized.get(key) for key in
                ('process_model_sha256', 'control_flow_contract', 'execution_schedule')):
            raise ValueError()
        return {'process_model': normalized['process_model'],
                'process_reference': {key: normalized[key] for key in ('workflow_id', 'revision', 'content_sha256')},
                'organization_catalog_reference': normalized.get('organization_catalog_reference')}
    except (ValueError, KeyError, TypeError, AttributeError):
        raise MonitoringError('attach an intact, non-authorizing exported SDK workflow specification') from None


def parse_design(spec: Any) -> tuple[str, list[dict[str, str]]]:
    if not isinstance(spec, dict) or spec.get("format") != SPEC_FORMAT:
        raise MonitoringError(f"attach the workflow.spec.json from an agent starter ({SPEC_FORMAT})")
    proposed = spec.get('to_be')
    steps = proposed.get('steps') if isinstance(proposed, dict) else None
    if not isinstance(steps, list) or not 0 < len(steps) <= 40 or not all(
            isinstance(s, dict) and isinstance(s.get("id"), str) and isinstance(s.get("activity"), str) for s in steps):
        raise MonitoringError("the workflow spec has no reviewed steps")
    return spec_digest(spec), [{"id": s["id"], "activity": s["activity"]} for s in steps]


# ---------------------------------------------------------------- storage

def _check(conn, deployment_id: str) -> dict[str, Any]:
    if not isinstance(deployment_id, str) or not _DEPLOYMENT.fullmatch(deployment_id):
        raise MonitoringError("unknown deployment")
    row = conn.execute("""SELECT deployment_id, name, runtime, created_at, design_sha256, design_steps,
          (SELECT count(*) FROM runs r WHERE r.deployment_id = d.deployment_id), reported_spec_sha256, design_graph
        FROM deployments d WHERE deployment_id = ?""", [deployment_id]).fetchone()
    if row is None:
        raise MonitoringError("unknown deployment")
    return {"deployment_id": row[0], "name": row[1], "runtime": row[2], "created_at": _iso(row[3]),
            "design_attached": row[4] is not None, "design_sha256": row[4],
            "design_steps": json.loads(row[5]) if row[5] else None, "runs": row[6],
            "reported_spec_sha256": row[7], "design_graph": json.loads(row[8]) if row[8] else None}


def _bulk_load(conn, deployment_id: str, runs: list[dict[str, Any]]) -> int:
    """Stage rows in private CSV files and load them in one statement each (row inserts are slow)."""
    import csv
    import tempfile

    with tempfile.TemporaryDirectory(prefix="owg-runs-", dir=str(DATA_DIR)) as tmp:
        os.chmod(tmp, 0o700)
        run_path, step_path = Path(tmp) / "runs.csv", Path(tmp) / "steps.csv"
        steps = 0
        with run_path.open("w", newline="", encoding="utf-8") as rf, step_path.open("w", newline="", encoding="utf-8") as sf:
            runs_out, steps_out = csv.writer(rf), csv.writer(sf)
            for r in runs:
                runs_out.writerow([r["run_id"], r["started_at"].isoformat(sep=" "),
                                   r["finished_at"].isoformat(sep=" ") if r["finished_at"] else "", r["status"],
                                   "" if r["verified"] is None else str(r["verified"]).lower(), r["source"]])
                for seq, (step_id, activity, status, at) in enumerate(r["steps"]):
                    steps_out.writerow([r["run_id"], seq, step_id, activity, status, at.isoformat(sep=" ")])
                    steps += 1
        options = "header=false, quote='\"', escape='\"', delim=',', all_varchar=true"
        conn.execute(f"""INSERT INTO runs SELECT ?, column0, column1::TIMESTAMP, nullif(column2, '')::TIMESTAMP, column3,
              nullif(column4, '')::BOOLEAN, coalesce(column5, '') FROM read_csv(?, {options},
              names=['column0','column1','column2','column3','column4','column5'])""", [deployment_id, str(run_path)])
        if steps:
            conn.execute(f"""INSERT INTO steps SELECT ?, column0, column1::INTEGER, column2, column3, column4,
                  column5::TIMESTAMP FROM read_csv(?, {options},
                  names=['column0','column1','column2','column3','column4','column5'])""", [deployment_id, str(step_path)])
    return steps


def import_report(document: Any, deployment_id: str | None = None) -> dict[str, Any]:
    """Store a report; runs with an existing run_id in the same deployment are replaced."""
    report = parse_report(document)
    with _LOCK:
        conn = _connect()
        try:
            conn.execute("BEGIN")
            try:
                if deployment_id is None:
                    if conn.execute("SELECT count(*) FROM deployments").fetchone()[0] >= MAX_DEPLOYMENTS:
                        raise MonitoringError("delete a deployment before adding another")
                    deployment_id = "dep_" + secrets.token_hex(8)
                    conn.execute("""INSERT INTO deployments
                        (deployment_id,name,runtime,created_at,design_sha256,design_steps,reported_spec_sha256)
                        VALUES (?, ?, ?, ?, NULL, NULL, ?)""",
                                 [deployment_id, report["name"], report["runtime"],
                                  datetime.now(timezone.utc).replace(tzinfo=None), report['spec_hash']])
                info = _check(conn, deployment_id)
                if report["spec_hash"] and info["design_sha256"] and report["spec_hash"] != info["design_sha256"]:
                    raise MonitoringError("this report names a different workflow spec than the attached design")
                if report['spec_hash'] and info['reported_spec_sha256'] and report['spec_hash'] != info['reported_spec_sha256']:
                    raise MonitoringError('this report names a different workflow spec than the retained reports')
                if report['spec_hash'] and not info['reported_spec_sha256']:
                    conn.execute('UPDATE deployments SET reported_spec_sha256=? WHERE deployment_id=?',
                                 [report['spec_hash'],deployment_id])
                ids = [r["run_id"] for r in report["runs"]]
                conn.execute("DELETE FROM steps WHERE deployment_id = ? AND list_contains(?, run_id)", [deployment_id, ids])
                conn.execute("DELETE FROM runs WHERE deployment_id = ? AND list_contains(?, run_id)", [deployment_id, ids])
                step_count = _bulk_load(conn, deployment_id, report["runs"])
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            return {"deployment": _check(conn, deployment_id), "runs_imported": len(report["runs"]),
                    "steps_imported": step_count}
        finally:
            conn.close()


def attach_design(deployment_id: str, spec: Any) -> dict[str, Any]:
    graph = parse_runtime_design(spec) if isinstance(spec, dict) and spec.get('format') == RUNTIME_SPEC_FORMAT else None
    digest, steps = (spec_digest(spec), None) if graph else parse_design(spec)
    with _LOCK:
        conn = _connect()
        try:
            info = _check(conn, deployment_id)
            if info['reported_spec_sha256'] and digest != info['reported_spec_sha256']:
                raise MonitoringError('this design does not match the workflow spec named by retained reports')
            conn.execute("UPDATE deployments SET design_sha256 = ?, design_steps = ?, design_graph = ? WHERE deployment_id = ?",
                         [digest, json.dumps(steps) if steps else None, json.dumps(graph) if graph else None, deployment_id])
            return _check(conn, deployment_id)
        finally:
            conn.close()


def list_deployments() -> dict[str, Any]:
    with _LOCK:
        conn = _connect()
        try:
            ids = [r[0] for r in conn.execute("SELECT deployment_id FROM deployments ORDER BY created_at DESC").fetchall()]
            return {"deployments": [_check(conn, i) for i in ids], "limit": MAX_DEPLOYMENTS,
                    "stored_on_this_computer_only": True}
        finally:
            conn.close()


def delete_deployment(deployment_id: str) -> dict[str, Any]:
    with _LOCK:
        conn = _connect()
        try:
            _check(conn, deployment_id)
            for table in ("steps", "runs", "deployments"):
                conn.execute(f"DELETE FROM {table} WHERE deployment_id = ?", [deployment_id])
            conn.execute("CHECKPOINT")
            return {"deleted": deployment_id}
        finally:
            conn.close()


# ---------------------------------------------------------------- monitoring

def _graph_comparison(graph: dict, per_run: dict, run_count: int) -> dict[str, Any]:
    """Bounded alignment of reported completed IDs, retaining the saved graph."""
    from collections import Counter
    from shared.process_conformance import compare_paths
    from shared.process_model import ProcessModel

    model = ProcessModel.model_validate(graph['process_model'])
    visible = {node.id for node in model.activities if node.kind == 'activity'}
    # Stable IDs, not potentially repeated display labels, identify observations.
    matching_model = model.model_copy(update={'activities': [node.model_copy(update={'label': node.id})
                                                            for node in model.activities]})
    paths = Counter(tuple(step_id for _, step_id, _, status, _ in items if status == 'completed')
                    for items in per_run.values())
    without_completed = run_count - sum(n for path, n in paths.items() if path)
    paths.pop((), None)
    selected = sorted(paths, key=lambda path: (-paths[path], path))[:MAX_GRAPH_PATHS]
    results = compare_paths(selected, matching_model)
    compared = matched = edits = 0
    unknown = {'no_completed_steps': without_completed,
               'distinct_path_limit': sum(n for path, n in paths.items() if path not in results),
               'alignment_state_limit': 0}
    for path, result in results.items():
        count = paths[path]
        if result.get('comparison_complete', True) is not True:
            unknown['alignment_state_limit'] += count
            continue
        compared += count
        matched += count * (result['exact_structural_match'] is True)
        edits += count * result['minimum_activity_edit_count']
    reached = {step_id for path in paths for step_id in path}
    unexpected = Counter()
    for items in per_run.values():
        unexpected.update({step_id for _, step_id, _, _, _ in items if step_id not in visible})
    return {'kind': 'runtime_graph', 'process_reference': graph['process_reference'],
            'runs_with_steps': len(per_run), 'total_runs': run_count, 'runs_compared': compared,
            'runs_not_compared': run_count - compared, 'runs_exact_structural_match': matched,
            'covered_run_share': round(compared / run_count, 6) if run_count else None,
            'mean_minimum_activity_edits_among_compared_runs': round(edits / compared, 6) if compared else None,
            'not_compared_by_reason': unknown, 'distinct_path_limit': MAX_GRAPH_PATHS,
            'activity_ids_never_reported_completed': sorted(visible - reached),
            'human_activity_ids_never_reported_completed': sorted(node.id for node in model.activities
                if node.kind == 'activity' and node.implementation == 'human' and node.id not in reached),
            'unexpected_step_ids': [{'step_id': key, 'runs': value} for key, value in
                                    sorted(unexpected.items(), key=lambda item: (-item[1], item[0]))[:25]],
            'method': 'minimum insert/delete alignment of reported completed activity IDs against the saved graph',
            'missing_activity_is_missing_reported_evidence_not_proven_process_deviation': True,
            'business_rules_evaluated': False, 'policy_compliance_verified': False,
            'business_outcome_verified': False, 'current_catalog_review_verified': False}

def _rate(successes: int, total: int) -> dict[str, Any] | None:
    if not total:
        return None
    low, high = _wilson(successes, total, 1.959964)
    return {"count": successes, "of": total, "rate": round(successes / total, 6),
            "ci95": [round(max(0.0, low), 6), round(min(1.0, high), 6)]}


def _diff(y_recent: int, n_recent: int, y_base: int, n_base: int) -> dict[str, Any] | None:
    if not n_recent or not n_base:
        return None
    d, low, high = risk_difference(y_recent, n_recent, y_base, n_base)
    return {"difference": round(d, 6), "ci95": [round(low, 6), round(high, 6)], "ci_excludes_zero": low > 0 or high < 0}


def monitor(deployment_id: str, *, recent_days: int = 7, baseline_days: int = 28,
            now: datetime | None = None) -> dict[str, Any]:
    if not 1 <= int(recent_days) <= 90 or not 1 <= int(baseline_days) <= 365:
        raise MonitoringError("recent_days must be 1-90 and baseline_days 1-365")
    with _LOCK:
        conn = _connect()
        try:
            info = _check(conn, deployment_id)
            runs = conn.execute("""SELECT run_id, started_at, finished_at, status, outcome_reported_verified
                FROM runs WHERE deployment_id = ? ORDER BY started_at, run_id""", [deployment_id]).fetchall()
            steps = conn.execute("""SELECT run_id, seq, step_id, activity, status, observed_at FROM steps
                WHERE deployment_id = ? ORDER BY run_id, seq""", [deployment_id]).fetchall()
        finally:
            conn.close()
    end = _naive(now) if now else (max(r[1] for r in runs) + timedelta(seconds=1) if runs else datetime.now(timezone.utc).replace(tzinfo=None))
    recent_from = end - timedelta(days=int(recent_days))
    base_from = recent_from - timedelta(days=int(baseline_days))

    def summary(rows):
        finished = [r for r in rows if r[3] != "running"]
        n = len(finished)
        counts = {s: sum(1 for r in rows if r[3] == s) for s in RUN_STATUSES}
        succeeded = [r for r in finished if r[3] == "succeeded"]
        durations = [(r[2] - r[1]).total_seconds() for r in finished if r[2]]
        reported = [r for r in succeeded if r[4] is not None]
        return {"runs": len(rows), "by_status": counts,
                "success": _rate(counts["succeeded"], n), "handed_to_human": _rate(counts["handed_to_human"], n),
                "failure": _rate(counts["failed"], n),
                "outcome_reported_verified": _rate(sum(1 for r in reported if r[4]), len(reported)),
                "median_duration_seconds": sorted(durations)[len(durations) // 2] if durations else None,
                "_durations": durations, "_n": n, "_counts": counts}

    everything = summary(runs)
    recent = summary([r for r in runs if r[1] >= recent_from])
    baseline = summary([r for r in runs if base_from <= r[1] < recent_from])
    drift: dict[str, Any] = {
        "recent_window": {"from": _iso(recent_from), "to": _iso(end), "runs": recent["runs"]},
        "baseline_window": {"from": _iso(base_from), "to": _iso(recent_from), "runs": baseline["runs"]},
        "success_rate": _diff(recent["_counts"]["succeeded"], recent["_n"], baseline["_counts"]["succeeded"], baseline["_n"]),
        "handoff_rate": _diff(recent["_counts"]["handed_to_human"], recent["_n"],
                              baseline["_counts"]["handed_to_human"], baseline["_n"]),
        "failure_rate": _diff(recent["_counts"]["failed"], recent["_n"], baseline["_counts"]["failed"], baseline["_n"]),
    }
    if len(recent["_durations"]) >= 5 and len(baseline["_durations"]) >= 5:
        d, low, high = median_difference(baseline["_durations"][:5000], recent["_durations"][:5000])
        drift["median_duration_seconds"] = {"difference": d, "ci95": [low, high], "ci_excludes_zero": low > 0 or high < 0}
    alerts = []
    for key, worse_when, label in (("success_rate", "down", "Success rate dropped"),
                                   ("handoff_rate", "up", "More runs were handed to a person"),
                                   ("failure_rate", "up", "More runs failed")):
        change = drift[key]
        if change and change["ci_excludes_zero"] and ((change["difference"] < 0) == (worse_when == "down")):
            alerts.append({"code": key + "_" + worse_when, "message": label + " in the recent window",
                           "difference": change["difference"], "ci95": change["ci95"]})
    if drift.get("median_duration_seconds", {}).get("ci_excludes_zero") and drift["median_duration_seconds"]["difference"] > 0:
        alerts.append({"code": "duration_up", "message": "Runs take longer in the recent window",
                       "difference": drift["median_duration_seconds"]["difference"],
                       "ci95": drift["median_duration_seconds"]["ci95"]})

    by_step: dict[tuple[str, str], dict[str, Any]] = {}
    per_run: dict[str, list[tuple]] = {}
    for run_id, seq, step_id, activity, status, at in steps:
        per_run.setdefault(run_id, []).append((seq, step_id, activity, status, at))
    for items in per_run.values():
        previous = None
        for seq, step_id, activity, status, at in items:
            entry = by_step.setdefault((step_id, activity), {"step_id": step_id, "activity": activity, "attempts": 0,
                                                              "failed": 0, "handed_to_human": 0, "skipped": 0, "_gaps": []})
            entry["attempts"] += 1
            if status in ("failed", "handed_to_human", "skipped"):
                entry[status] += 1
            if previous is not None:
                entry["_gaps"].append((at - previous).total_seconds())
            previous = at
    hotspots = []
    for entry in by_step.values():
        gaps = sorted(entry.pop("_gaps"))
        entry["failure_rate"] = round(entry["failed"] / entry["attempts"], 6)
        entry["handoff_rate"] = round(entry["handed_to_human"] / entry["attempts"], 6)
        entry["median_seconds_since_previous_step"] = gaps[len(gaps) // 2] if gaps else None
        hotspots.append(entry)
    hotspots.sort(key=lambda e: (-(e["failed"] + e["handed_to_human"]), e["step_id"]))

    conformance = None
    if info.get('design_graph'):
        conformance = _graph_comparison(info['design_graph'], per_run, len(runs))
    elif info["design_steps"]:
        design = [s["id"] for s in info["design_steps"]]
        known = set(design)
        fitting, fitness_sum, unexpected, reached = 0, 0.0, {}, set()
        for items in per_run.values():
            executed = [step_id for _, step_id, _, status, _ in items if status == "completed"]
            cost, _ = _align(executed, design)
            fitness_sum += 1 - cost / (len(executed) + len(design))
            fitting += cost == 0
            reached.update(executed)
            for step_id in {s for _, s, _, _, _ in items if s not in known}:
                unexpected[step_id] = unexpected.get(step_id, 0) + 1
        total = len(per_run)
        conformance = {
            "runs_with_steps": total,
            "runs_completing_every_design_step_in_order": fitting,
            "mean_fitness": round(fitness_sum / total, 6) if total else None,
            "design_steps_never_completed": [s for s in info["design_steps"] if s["id"] not in reached],
            "unexpected_step_ids": [{"step_id": k, "runs": v} for k, v in sorted(unexpected.items(), key=lambda kv: -kv[1])][:25],
            "method": "alignment of completed step IDs against the reviewed design order",
        }

    weekly: dict[str, dict[str, int]] = {}
    for run_id, started, finished, status, _ in runs:
        year, week, _ = started.isocalendar()
        bucket = weekly.setdefault(f"{year}-W{week:02d}", {s: 0 for s in RUN_STATUSES})
        bucket[status] += 1
    clean = lambda s: {k: v for k, v in s.items() if not k.startswith("_")}
    return {"deployment": info, "overall": clean(everything), "recent": clean(recent), "baseline": clean(baseline),
            "drift": drift, "alerts": alerts, "steps": hotspots[:100], "design_conformance": conformance,
            "weekly": [{"week": w, **c} for w, c in sorted(weekly.items())][-52:], **REPORTED_ONLY}


def runs_csv(deployment_id: str) -> bytes:
    """Step-level CSV (run, step, time, status) for the event-log workspace."""
    import csv
    import io

    with _LOCK:
        conn = _connect()
        try:
            _check(conn, deployment_id)
            rows = conn.execute("""SELECT run_id, step_id || ' ' || activity, observed_at, status FROM steps
                WHERE deployment_id = ? ORDER BY run_id, seq""", [deployment_id]).fetchall()
        finally:
            conn.close()
    if not rows:
        raise MonitoringError("this deployment has no reported steps")
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["run", "step", "time", "status"])
    for run_id, step, at, status in rows:
        writer.writerow([run_id, step, at.isoformat() + "Z", status])
    return out.getvalue().encode("utf-8")
