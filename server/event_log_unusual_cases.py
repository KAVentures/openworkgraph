"""Unusual cases: which cases stand out from the rest of the log, and why.

Anomaly views in process mining tools (Celonis's and UiPath's outlier and anomaly views, ProM's
trace outlier plug-ins) point reviewers at the few cases worth opening. This module scores every
case on a handful of transparent signals, each compared with the rest of the selection:

* **rare path**: few other cases followed exactly the same sequence of activities (under 1%, or none),
  and paths that rare cover at most 5% of cases, so in a log where most paths are unique, being
  unique is not unusual;
* **unusually slow / unusually fast**: the case's duration (first to last event) is far from the
  usual, measured as a robust z-score of log-duration (median and MAD, so a few extreme cases do
  not hide each other);
* **unusually long**: far more events than usual, same robust z-score;
* **heavy rework**: the case repeats activities much more than others do;
* **rare activity**: the case contains an activity that almost no other case has (under 1%, or no other
  case at all).

Each signal contributes between 0 and 1 to the score and is reported as a reason with its
measurement, so a reviewer sees *why* a case is listed. The score ranks cases for review; it is not
a verdict that anything went wrong, and an unusual case may be perfectly legitimate.
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Any

from . import event_log_workspace as workspace
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.event_log_unusual_cases.v1"
MIN_CASES = 20
RARE_PATH_SHARE = 0.01
RARE_ACTIVITY_SHARE = 0.01
RARE_PATH_COVERAGE = 0.05
Z_FLAG = 3.0
MAX_LIMIT = 200
MAX_OFFSET = 10_000_000
SIGNALS = {"rare_path", "unusually_slow", "unusually_fast", "unusually_long", "heavy_rework", "rare_activity"}
_MAD_SCALE = 1.4826


def _robust(values: list[float]) -> tuple[float, float]:
    ordered = sorted(values)
    n = len(ordered)
    median = ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2
    deviations = sorted(abs(v - median) for v in ordered)
    mad = deviations[n // 2] if n % 2 else (deviations[n // 2 - 1] + deviations[n // 2]) / 2
    if mad == 0:
        # Most cases identical: fall back to the mean absolute deviation so outliers still register.
        mad = sum(deviations) / n
    return median, _MAD_SCALE * mad


def _z(value: float, centre: float, spread: float) -> float:
    return 0.0 if spread == 0 else (value - centre) / spread


def _strength(z: float) -> float:
    """0.25 at the flag threshold, rising to 1 at twice the threshold; 0 below it."""
    return min(1.0, 0.25 + 0.75 * (abs(z) - Z_FLAG) / Z_FLAG) if abs(z) >= Z_FLAG else 0.0


def _hours(seconds: float) -> float:
    return round(seconds / 3600, 2)


def unusual_cases(log_id: str, case_type: str | None = None, *, limit: int = 25, include_case_ids: bool = True,
                  cohort: dict[str, Any] | None = None, signal: str | None = None, offset: int = 0,
                  all_cases: bool = False) -> dict[str, Any]:
    """``all_cases`` returns every scored case (for the CSV worklist) instead of one page."""
    if all_cases:
        limit, offset = MAX_LIMIT, 0
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise EventLogWorkspaceError(f"limit must be 1 to {MAX_LIMIT}")
    if signal is not None and (not isinstance(signal, str) or signal not in SIGNALS):
        raise EventLogWorkspaceError("unknown unusual-case reason")
    if type(offset) is not int or not 0 <= offset <= MAX_OFFSET:
        raise EventLogWorkspaceError(f"offset must be 0 to {MAX_OFFSET}")

    def run(conn):
        rows = conn.execute("""SELECT case_id, list(activity ORDER BY ts, event_id),
              epoch(max(ts) - min(ts)) FROM ce GROUP BY case_id ORDER BY case_id""").fetchall()
        return {"rows": rows}

    data = workspace._query_filtered(log_id, case_type, cohort, run)
    rows = data.pop("rows")
    cases = len(rows)
    if cases < MIN_CASES:
        raise EventLogWorkspaceError(f"at least {MIN_CASES} cases are needed to say what is unusual; "
                                     f"this selection has {cases}")
    variants = Counter(tuple(path) for _, path, _ in rows)
    # Cases on paths taken by at most n cases. A path is rare only if paths that rare cover few cases in
    # total; in a log where most paths are unique, being unique is not unusual.
    cases_at_or_below: dict[int, int] = {}
    running = 0
    for count, paths in sorted(Counter(variants.values()).items()):
        running += count * paths
        cases_at_or_below[count] = running
    activity_cases = Counter(a for _, path, _ in rows for a in set(path))
    log_durations = [math.log1p(max(d or 0.0, 0.0)) for _, _, d in rows]
    log_lengths = [math.log(len(path)) for _, path, _ in rows]
    repeats = [len(path) - len(set(path)) for _, path, _ in rows]
    dur_c, dur_s = _robust(log_durations)
    len_c, len_s = _robust(log_lengths)
    rep_c, rep_s = _robust([float(r) for r in repeats])
    median_hours = _hours(math.expm1(dur_c))
    median_events = round(math.exp(len_c), 1)
    reason_cases: Counter = Counter()
    scored = []
    unusual = 0
    for (case_id, path, duration), log_d, log_n, rep in zip(rows, log_durations, log_lengths, repeats):
        reasons = []
        share = variants[tuple(path)] / cases
        taken = variants[tuple(path)]
        if (share < RARE_PATH_SHARE or taken == 1) and cases_at_or_below[taken] / cases <= RARE_PATH_COVERAGE:
            strength = min(1.0, -math.log10(share) / 3)
            reasons.append({"signal": "rare_path", "strength": round(strength, 3),
                            "detail": f"{variants[tuple(path)]} of {cases} cases followed this exact path"})
        z = _z(log_d, dur_c, dur_s)
        if abs(z) >= Z_FLAG:
            duration_signal = "unusually_slow" if z > 0 else "unusually_fast"
            reasons.append({"signal": duration_signal, "strength": round(_strength(z), 3), "z": round(z, 2),
                            "detail": f"took {_hours(duration or 0.0)} h; the median case takes {median_hours} h"})
        z = _z(log_n, len_c, len_s)
        if z >= Z_FLAG:
            reasons.append({"signal": "unusually_long", "strength": round(_strength(z), 3), "z": round(z, 2),
                            "detail": f"{len(path)} events; the median case has {median_events}"})
        z = _z(float(rep), rep_c, rep_s)
        if rep >= 2 and z >= Z_FLAG:
            reasons.append({"signal": "heavy_rework", "strength": round(_strength(z), 3), "z": round(z, 2),
                            "detail": f"{rep} repeated activities"})
        rare = sorted(a for a in set(path) if activity_cases[a] == 1 or activity_cases[a] / cases < RARE_ACTIVITY_SHARE)
        if rare:
            reasons.append({"signal": "rare_activity", "strength": 1.0,
                            "detail": "contains " + ", ".join(f"{a} ({activity_cases[a]} cases)" for a in rare[:5])})
        if not reasons:
            continue
        unusual += 1
        for reason in reasons:
            reason_cases[reason["signal"]] += 1
        if signal is not None and not any(reason["signal"] == signal for reason in reasons):
            continue
        score = round(sum(r["strength"] for r in reasons), 3)
        reasons.sort(key=lambda r: -r["strength"])
        scored.append({"case_id": case_id, "score": score, "reasons": reasons,
                       "events": len(path), "hours": _hours(duration or 0.0), "path": list(path)[:50],
                       "path_truncated": len(path) > 50})
    scored.sort(key=lambda c: (-c["score"], str(c["case_id"])))
    top = scored if all_cases else scored[offset:offset + limit]
    if not include_case_ids:
        top = [{k: v for k, v in c.items() if k != "case_id"} for c in top]
    return {
        "format": FORMAT, "log_id": log_id, "case_notion": data.get("case_notion"),
        "cases": cases, "unusual_cases": unusual, "unusual_share": round(unusual / cases, 4),
        "page": {"signal": signal, "total": len(scored), "offset": offset, "limit": limit,
                 "next_offset": None if all_cases else (offset + len(top) if offset + len(top) < len(scored) else None)},
        "by_signal": dict(sorted(reason_cases.items(), key=lambda kv: (-kv[1], kv[0]))),
        "baseline": {"median_hours": median_hours, "median_events": median_events,
                     "distinct_paths": len(variants)},
        "thresholds": {"rare_path_share": RARE_PATH_SHARE, "rare_path_coverage": RARE_PATH_COVERAGE, "rare_activity_share": RARE_ACTIVITY_SHARE,
                       "robust_z": Z_FLAG},
        "top": top, "case_ids_included": include_case_ids,
        "method": ("each case is compared with the selection: path frequency, robust z-scores (median and MAD) of "
                   "log-duration, event count and repeated activities, and activities found in under 1% of cases or in one case; "
                   "each signal adds 0 to 1 to the score"),
        "caveat": ("Unusual is not wrong. The score ranks cases for a look; rare paths and long durations are often "
                   "legitimate exceptions. Signals describe recorded events only, not who worked on the case."),
    }


CSV_COLUMNS = ["case_id", "score", "signals", "reasons", "events", "hours", "path", "path_truncated"]


def unusual_cases_csv(log_id: str, case_type: str | None = None, *, cohort: dict[str, Any] | None = None,
                      signal: str | None = None) -> tuple[str, dict[str, Any]]:
    """Every unusual case as a CSV worklist, highest score first, with formula-like cells neutralized."""
    import csv
    import io
    from .event_log_variant_export import _cell
    result = unusual_cases(log_id, case_type, cohort=cohort, signal=signal, all_cases=True)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(CSV_COLUMNS)
    for c in result["top"]:
        writer.writerow([_cell(c["case_id"]), c["score"], ";".join(r["signal"] for r in c["reasons"]),
                         _cell(" | ".join(r["detail"] for r in c["reasons"])), c["events"], c["hours"],
                         _cell(" → ".join(c["path"])), c["path_truncated"]])
    return out.getvalue(), {"cases": result["cases"], "unusual_cases": result["unusual_cases"],
                            "rows": len(result["top"]), "signal": signal}


__all__ = ["unusual_cases", "unusual_cases_csv", "CSV_COLUMNS", "FORMAT"]
