"""Saved prediction snapshots, scored later against what actually happened (#370 R19).

Backtests and monitoring replay a log's history. A snapshot is prospective: it
freezes the remaining-time and outcome-risk estimates for every open case at the
moment it is taken. After newer events are appended to the log, evaluating the
snapshot compares each frozen estimate with how that case actually went:

- **finished cases**: absolute error of the median remaining time (against the
  naive median every case would have received), whether the actual remaining time
  fell inside the frozen 10th–90th percentile range (nominally 80%, with a 95%
  Wilson interval) and, for an outcome, the Brier score against the base rate;
- **still-open cases** are censored: their remaining time is at least the time since
  the snapshot. They are counted separately; a case already past its 90th percentile
  is a known miss, the rest are unknown. Scoring only finished cases favours short
  ones, so the open share is reported next to every score.

Snapshots use the completed-cases estimator. They describe this log; they are not
commitments, targets or ratings of anyone.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
import secrets
import sqlite3
import threading
from typing import Any

from . import event_log_predictions as predictions
from . import event_log_workspace as workspace
from .event_log_analytics import _wilson

MAX_SNAPSHOT_CASES = 5000
MAX_SNAPSHOTS_PER_LOG = 50
_LOCK = threading.RLock()


def _connect() -> sqlite3.Connection:
    path = workspace.DATA_DIR / "event_log_prediction_snapshots.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("""CREATE TABLE IF NOT EXISTS snapshots (
        snapshot_id TEXT PRIMARY KEY, log_id TEXT NOT NULL, created_at TEXT NOT NULL, as_of TEXT NOT NULL,
        settings TEXT NOT NULL, summary TEXT NOT NULL, cases TEXT NOT NULL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS snapshots_by_log ON snapshots(log_id)")
    return conn


def _iso(value) -> str | None:
    return value.isoformat() + "Z" if value is not None else None


def take(log_id: str, spec: Any) -> dict[str, Any]:
    """Freeze the current estimates for every supported open case."""
    settings = predictions.parse(spec)
    if settings.estimator != "completed_cases":
        raise workspace.EventLogWorkspaceError("snapshots use the completed_cases estimator")

    def run(conn):
        as_of = predictions._prepare(conn, settings, log_id)
        conn.execute("""CREATE OR REPLACE TEMP TABLE po AS SELECT case_id, last_activity AS activity,
              least(events, ?) AS step, epoch(? - started) AS age, epoch(? - ended) AS idle
            FROM pc WHERE NOT complete""", [predictions.MAX_STEP, as_of, as_of])
        predictions._train(conn, "TRUE", [])
        predictions._choose(conn, "po", settings.min_support)
        open_total = conn.execute("SELECT count(*) FROM po").fetchone()[0]
        rows = conn.execute("""SELECT case_id, activity, step, basis, q50, q10, q90, rate, naive_q50, base_rate
            FROM po_est WHERE basis IS NOT NULL ORDER BY case_id LIMIT ?""", [MAX_SNAPSHOT_CASES + 1]).fetchall()
        return as_of, open_total, rows
    result = workspace._query(log_id, settings.case_type, lambda conn: {"_run": run(conn)})
    as_of, open_total, rows = result["_run"]
    if as_of is None:
        raise workspace.EventLogWorkspaceError("the log has no events to predict from")
    if len(rows) > MAX_SNAPSHOT_CASES:
        raise workspace.EventLogWorkspaceError(f"a snapshot holds at most {MAX_SNAPSHOT_CASES} open cases; narrow it with a cohort")
    cases = [{"case_id": c, "last_activity": a, "events_so_far": s, "basis": b, "median_remaining_seconds": q50,
              "remaining_p10_seconds": q10, "remaining_p90_seconds": q90, "outcome_probability": rate,
              "naive_median_remaining_seconds": naive, "base_rate": base}
             for c, a, s, b, q50, q10, q90, rate, naive, base in rows]
    summary = {"open_cases": open_total, "predicted_cases": len(cases), "unsupported_open_cases": open_total - len(cases),
               "events": result["log"].get("events") if isinstance(result.get("log"), dict) else None}
    with _LOCK, closing(_connect()) as db, db:
        if db.execute("SELECT count(*) FROM snapshots WHERE log_id = ?", [log_id]).fetchone()[0] >= MAX_SNAPSHOTS_PER_LOG:
            raise workspace.EventLogWorkspaceError(f"a log keeps at most {MAX_SNAPSHOTS_PER_LOG} snapshots; delete one first")
        snapshot_id = "psnap_" + secrets.token_hex(8)
        db.execute("INSERT INTO snapshots VALUES (?, ?, ?, ?, ?, ?, ?)",
                   [snapshot_id, log_id, datetime.now(timezone.utc).isoformat(timespec="seconds"), _iso(as_of),
                    json.dumps(settings.model_dump()), json.dumps(summary), json.dumps(cases)])
    return _describe(snapshot_id, log_id)


def _row(log_id: str, snapshot_id: str):
    with _LOCK, closing(_connect()) as db:
        row = db.execute("SELECT snapshot_id, created_at, as_of, settings, summary, cases FROM snapshots "
                         "WHERE log_id = ? AND snapshot_id = ?", [log_id, str(snapshot_id)]).fetchone()
    if row is None:
        raise workspace.EventLogWorkspaceError("unknown prediction snapshot")
    return row


def _describe(snapshot_id: str, log_id: str) -> dict[str, Any]:
    sid, created, as_of, settings, summary, _cases = _row(log_id, snapshot_id)
    return {"snapshot_id": sid, "log_id": log_id, "created_at": created, "as_of": as_of,
            "settings": json.loads(settings), **json.loads(summary)}


def listing(log_id: str) -> dict[str, Any]:
    workspace._query(log_id, None, lambda conn: {})  # the log must exist
    with _LOCK, closing(_connect()) as db:
        ids = [r[0] for r in db.execute("SELECT snapshot_id FROM snapshots WHERE log_id = ? ORDER BY created_at DESC, "
                                        "snapshot_id", [log_id]).fetchall()]
    return {"log_id": log_id, "snapshots": [_describe(i, log_id) for i in ids], "max_snapshots": MAX_SNAPSHOTS_PER_LOG}


def delete(log_id: str, snapshot_id: str) -> dict[str, Any]:
    _row(log_id, snapshot_id)
    with _LOCK, closing(_connect()) as db, db:
        db.execute("DELETE FROM snapshots WHERE log_id = ? AND snapshot_id = ?", [log_id, snapshot_id])
    return {"deleted": True, "snapshot_id": snapshot_id}


def evaluate(log_id: str, snapshot_id: str) -> dict[str, Any]:
    """Score a snapshot against the log as it is now."""
    sid, created, as_of_text, settings_text, summary, cases_text = _row(log_id, snapshot_id)
    settings = predictions.parse(json.loads(settings_text))
    frozen = {c["case_id"]: c for c in json.loads(cases_text)}
    snap_at = datetime.fromisoformat(as_of_text.rstrip("Z"))

    def run(conn):
        predictions._prepare(conn, settings, log_id)
        now = conn.execute("SELECT max(ts) FROM ce").fetchone()[0]
        outcome_sql, params = predictions._outcome_sql(settings.outcome)
        rows = conn.execute(f"""SELECT c.case_id, c.complete, epoch(c.ended - ?) AS remaining, {outcome_sql}
            FROM pc c""", [snap_at, *params]).fetchall()
        return now, rows
    result = workspace._query(log_id, settings.case_type, lambda conn: {"_run": run(conn)})
    now, rows = result["_run"]
    current = {case_id: (complete, remaining, outcome) for case_id, complete, remaining, outcome in rows}
    since = (now - snap_at).total_seconds() if now is not None else 0.0
    finished, open_rows, missing = [], [], 0
    for case_id, item in frozen.items():
        state = current.get(case_id)
        if state is None:
            missing += 1
        elif state[0] and state[1] is not None and state[1] >= 0:
            finished.append((item, float(state[1]), state[2]))
        else:
            open_rows.append(item)
    scored = len(finished)
    errors = [abs(item["median_remaining_seconds"] - actual) for item, actual, _ in finished]
    naive = [abs(item["naive_median_remaining_seconds"] - actual) for item, actual, _ in finished
             if item["naive_median_remaining_seconds"] is not None]
    inside = sum(1 for item, actual, _ in finished
                 if item["remaining_p10_seconds"] <= actual <= item["remaining_p90_seconds"])
    known_open_miss = sum(1 for item in open_rows if since > item["remaining_p90_seconds"])
    out: dict[str, Any] = {
        "snapshot": _describe(sid, log_id), "evaluated_against": _iso(now), "seconds_since_snapshot": since,
        "snapshot_cases": len(frozen), "finished_cases": scored, "still_open_cases": len(open_rows),
        "cases_no_longer_in_log": missing,
        "open_share": round(len(open_rows) / len(frozen), 4) if frozen else None,
        "remaining_time": None, "outcome": None,
        "interpretation": ("Frozen estimates scored against what happened after the snapshot. Only finished cases "
                           "have a known remaining time; long cases are still open, so scores on finished cases "
                           "lean towards short ones. Estimates are not commitments, targets or ratings of anyone."),
        "censoring_note": "still-open cases past their frozen 90th percentile are known misses; the rest are unknown",
    }
    if scored:
        sorted_errors = sorted(errors)
        out["remaining_time"] = {
            "mean_absolute_error_seconds": round(sum(errors) / scored, 3),
            "median_absolute_error_seconds": round(sorted_errors[scored // 2] if scored % 2 else
                                                   (sorted_errors[scored // 2 - 1] + sorted_errors[scored // 2]) / 2, 3),
            "naive_mean_absolute_error_seconds": round(sum(naive) / len(naive), 3) if naive else None,
            "mean_signed_error_seconds": round(sum(item["median_remaining_seconds"] - actual
                                                   for item, actual, _ in finished) / scored, 3),
            "coverage_10_90": round(inside / scored, 4),
            "coverage_10_90_ci95": [round(v, 4) for v in _wilson(inside, scored, 1.959964)],
            "nominal_coverage": 0.8,
            "still_open_known_misses": known_open_miss,
        }
    if settings.outcome is not None and scored:
        pairs = [(item["outcome_probability"], bool(outcome), item["base_rate"]) for item, _, outcome in finished
                 if item["outcome_probability"] is not None and outcome is not None]
        if pairs:
            brier = sum((p - y) ** 2 for p, y, _ in pairs) / len(pairs)
            base = [(b - y) ** 2 for _, y, b in pairs if b is not None]
            out["outcome"] = {"cases": len(pairs), "brier_score": round(brier, 5),
                              "base_rate_brier_score": round(sum(base) / len(base), 5) if base else None,
                              "observed_rate": round(sum(1 for _, y, _ in pairs if y) / len(pairs), 4),
                              "mean_predicted": round(sum(p for p, _, _ in pairs) / len(pairs), 4)}
    return out


def erase_log(log_id: str) -> None:
    with _LOCK, closing(_connect()) as db, db:
        db.execute("DELETE FROM snapshots WHERE log_id = ?", [log_id])


__all__ = ["delete", "erase_log", "evaluate", "listing", "take"]
