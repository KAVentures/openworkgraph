"""Anonymized export: an event log that can leave the machine with less that points back to people.

Sharing an event log with a consultant, a vendor or a public benchmark is common in process mining,
and the exported cases often describe identifiable customers, patients or staff. Research tools
(PRIPEL, ProM's privacy plug-ins) and some commercial suites offer a privacy-preserving export; this
one applies a few transparent, explainable steps rather than noise injection:

* **variant suppression** (k-anonymity of the control flow): a case is kept only when at least
  ``min_cases`` cases follow exactly the same sequence of activities, so a rare path cannot single
  out one case;
* **new case IDs**: original case IDs are replaced by ``C000001``, ``C000002``, … in a random order,
  so the export cannot be joined back to the source system by ID or by row order;
* **coarser time**: ``exact`` keeps timestamps; ``day`` keeps only the date of each event (a step
  column keeps the order); ``relative`` moves each case to start on the first day of the month it
  started and keeps the gaps between its events. ``elapsed`` keeps the gaps and
  uses the same synthetic start for every case. ``sequence`` uses synthetic
  timestamps one second apart to retain order only;
* **resources**: dropped (default), or replaced by ``Resource 01``, … in a random order;
* **activity codes**: optional export-local codes replace activity names;
* **no attributes**: case and event attributes are never exported.

The summary says what was removed before anything is downloaded. Suppressing rare paths does not
make a log anonymous on its own: exact durations, dates and activity names can still identify a case
to someone who knows it. The export is a reduction of risk, not a guarantee.
"""
from __future__ import annotations

import csv
import io
import secrets
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from . import event_log_workspace as workspace
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.event_log_anonymized_export.v1"
TIME_MODES = ("exact", "day", "relative", "elapsed", "sequence")
ACTIVITY_MODES = ("keep", "pseudonymize")
SYNTHETIC_START = datetime(2000, 1, 1)
RESOURCE_MODES = ("drop", "pseudonymize")
MIN_K, MAX_K = 2, 1000
MAX_EXPORT_EVENTS = 1_000_000


def _cell(value: Any) -> str:
    """CSV cell safe to open in a spreadsheet: a leading formula character is neutralized."""
    from shared.spreadsheet_review import review_cell

    return review_cell("" if value is None else str(value))


def _check(min_cases: Any, time_mode: str, resource_mode: str, activity_mode: str) -> int:
    if isinstance(min_cases, bool) or not isinstance(min_cases, int) or not MIN_K <= min_cases <= MAX_K:
        raise EventLogWorkspaceError(f"min_cases must be a whole number from {MIN_K} to {MAX_K}")
    if time_mode not in TIME_MODES:
        raise EventLogWorkspaceError("time_mode must be one of " + ", ".join(TIME_MODES))
    if resource_mode not in RESOURCE_MODES:
        raise EventLogWorkspaceError("resource_mode must be one of " + ", ".join(RESOURCE_MODES))
    if activity_mode not in ACTIVITY_MODES:
        raise EventLogWorkspaceError("activity_mode must be one of " + ", ".join(ACTIVITY_MODES))
    return min_cases


def _rows(log_id: str, case_type: str | None, cohort: dict[str, Any] | None) -> dict[str, Any]:
    def run(conn):
        rows = conn.execute("""SELECT list(activity ORDER BY ts, event_id), list(ts ORDER BY ts, event_id),
                list(resource ORDER BY ts, event_id) FROM ce GROUP BY case_id""").fetchall()
        return {"rows": rows}
    return workspace._query_filtered(log_id, case_type, cohort, run)


def _plan(data: dict[str, Any], k: int) -> tuple[list, dict[str, Any]]:
    rows = data["rows"]
    if not rows:
        raise EventLogWorkspaceError("no cases in this selection")
    counts = Counter(tuple(acts) for acts, _, _ in rows)
    kept_variants = {v for v, n in counts.items() if n >= k}
    kept = [r for r in rows if tuple(r[0]) in kept_variants]
    events = sum(len(r[0]) for r in kept)
    activities_all = {a for acts, _, _ in rows for a in acts}
    activities_kept = {a for v in kept_variants for a in v}
    summary = {
        "cases_total": len(rows), "cases_kept": len(kept), "cases_suppressed": len(rows) - len(kept),
        "cases_kept_share": round(len(kept) / len(rows), 4),
        "variants_total": len(counts), "variants_kept": len(kept_variants),
        "variants_suppressed": len(counts) - len(kept_variants),
        "events_kept": events,
        "activities_removed": sorted(activities_all - activities_kept)[:50],
        "activities_removed_total": len(activities_all - activities_kept),
        "smallest_kept_group": min((counts[v] for v in kept_variants), default=None),
    }
    return kept, summary


def _when(ts: datetime, case_start: datetime, time_mode: str, synthetic_start: datetime | None, step: int) -> str:
    if time_mode == "exact":
        return ts.isoformat(sep="T") + "Z"
    if time_mode == "day":
        return ts.date().isoformat()
    anchor = synthetic_start or case_start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elapsed = timedelta(seconds=step - 1) if time_mode == "sequence" else ts - case_start
    return (anchor + elapsed).isoformat(sep="T") + "Z"


def anonymized_export(log_id: str, case_type: str | None = None, *, min_cases: int = 5, time_mode: str = "relative",
                      resource_mode: str = "drop", cohort: dict[str, Any] | None = None,
                      summary_only: bool = False, activity_mode: str = "keep") -> dict[str, Any]:
    """The anonymized log as CSV text under ``csv`` (unless ``summary_only``), with what was removed."""
    k = _check(min_cases, time_mode, resource_mode, activity_mode)
    data = _rows(log_id, case_type, cohort)
    kept, summary = _plan(data, k)
    if not kept:
        raise EventLogWorkspaceError(f"no path is followed by at least {k} cases; lower min_cases or widen the selection")
    if summary["events_kept"] > MAX_EXPORT_EVENTS:
        raise EventLogWorkspaceError(f"the export would hold {summary['events_kept']:,} events; at most "
                                     f"{MAX_EXPORT_EVENTS:,} can be exported at once — narrow the selection")
    synthetic_start = SYNTHETIC_START if time_mode == "sequence" else None
    if time_mode == "elapsed":
        longest = max(stamps[-1] - stamps[0] for _, stamps, _ in kept)
        synthetic_start = SYNTHETIC_START if longest <= datetime.max - SYNTHETIC_START else datetime.min
    result = {
        "format": FORMAT, "log_id": log_id, "case_notion": data.get("case_notion"),
        "min_cases": k, "time_mode": time_mode, "resource_mode": resource_mode, "activity_mode": activity_mode, **summary,
        **({"synthetic_start": synthetic_start.isoformat() + "Z"} if synthetic_start else {}),
        "columns": ["case_id", "step", "activity", "timestamp"] + (["resource"] if resource_mode == "pseudonymize" else []),
        "method": ("cases kept only when at least min_cases cases share their exact path; case IDs replaced in random "
                   "order; " + {"exact": "timestamps kept", "day": "timestamps reduced to the date",
                                "relative": "each case moved to start on the first of its start month, gaps kept",
                                "elapsed": "each case moved to the same synthetic start, gaps kept",
                                "sequence": "synthetic timestamps one second apart, event order kept"}[time_mode]
                   + "; " + ("resources dropped" if resource_mode == "drop" else "resources replaced in random order")
                   + ("; activity names replaced with export-local codes" if activity_mode == "pseudonymize" else "")
                   + "; attributes never exported"),
        "caveat": ("Suppressing rare paths lowers the chance that a case can be singled out; it does not make the log "
                   "anonymous. Durations, dates and activity names can still identify a case to someone who knows it. "
                   "Review the file before sharing it."),
    }
    if summary_only:
        return result
    rng = secrets.SystemRandom()
    order = list(range(len(kept)))
    rng.shuffle(order)
    names: dict[str, str] = {}
    if resource_mode == "pseudonymize":
        found = sorted({r for _, _, res in kept for r in res if r})
        rng.shuffle(found)
        width = max(2, len(str(len(found))))
        names = {r: f"Resource {i + 1:0{width}d}" for i, r in enumerate(found)}
    activities: dict[str, str] = {}
    if activity_mode == "pseudonymize":
        labels = sorted({a for acts, _, _ in kept for a in acts})
        rng.shuffle(labels)
        activities = {a: f"A{i:06d}" for i, a in enumerate(labels, start=1)}
    width = max(6, len(str(len(kept))))
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(result["columns"])
    for n, index in enumerate(order, start=1):
        acts, stamps, res = kept[index]
        case_id = f"C{n:0{width}d}"
        for step, (activity, ts, resource) in enumerate(zip(acts, stamps, res), start=1):
            row = [case_id, step, _cell(activities.get(activity, activity)),
                   _when(ts, stamps[0], time_mode, synthetic_start, step)]
            if resource_mode == "pseudonymize":
                row.append(names.get(resource, ""))
            writer.writerow(row)
    result["csv"] = out.getvalue()
    return result


__all__ = ["anonymized_export", "FORMAT", "TIME_MODES", "RESOURCE_MODES"]
