"""Milestone funnel: how many cases reach each chosen step in order, and where the rest stop.

Funnel or milestone analysis (Celonis process explorer conversion views, UiPath Process Mining's
milestones, product-analytics funnels) follows cases through an ordered list of activities. A case
reaches step ``k`` when it has done steps ``1 … k`` in that order, each after the previous one (not
necessarily directly). For each step this module reports how many cases got there, the conversion
from the previous step, the time between the two steps, and, for cases that reached the previous
step but not this one, what they did last instead and whether they are still open (their last event
is near the end of the log).

An optional conversion window counts a step only if it happens within the given hours of the
previous one. The funnel describes recorded order and timing; it does not say why cases stopped.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from . import event_log_workspace as workspace
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.event_log_funnel.v1"
MIN_STEPS, MAX_STEPS = 2, 8
MAX_WINDOW_HOURS = 24 * 3650
TOP_STOPS = 8


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo, hi = int(pos), min(int(pos) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def _hours(seconds: float | None) -> float | None:
    return None if seconds is None else round(seconds / 3600, 3)


def _seconds(ts: Any) -> float:
    if isinstance(ts, datetime):
        return ts.timestamp()
    return float(ts)


def funnel(log_id: str, steps: list[str], case_type: str | None = None, *, within_hours: float | None = None,
           cohort: dict[str, Any] | None = None, evidence_step: int | None = None,
           evidence_status: str = "reached") -> dict[str, Any]:
    if not isinstance(steps, list) or not MIN_STEPS <= len(steps) <= MAX_STEPS:
        raise EventLogWorkspaceError(f"choose {MIN_STEPS} to {MAX_STEPS} steps")
    if any(not isinstance(s, str) or not s or len(s) > 500 for s in steps):
        raise EventLogWorkspaceError("each step must name an activity")
    if within_hours is not None and (isinstance(within_hours, bool) or not isinstance(within_hours, (int, float))
                                     or not 0 < within_hours <= MAX_WINDOW_HOURS):
        raise EventLogWorkspaceError("the conversion window must be a positive number of hours")
    if evidence_step is not None and (type(evidence_step) is not int or not 1 <= evidence_step <= len(steps)):
        raise EventLogWorkspaceError("evidence step must identify a chosen step")
    if evidence_status not in ("reached", "dropped") or (evidence_status == "dropped" and evidence_step == 1):
        raise EventLogWorkspaceError("choose reached or dropped after a previous step")
    window = within_hours * 3600 if within_hours is not None else None

    def run(conn):
        rows = conn.execute("""SELECT case_id, list(activity ORDER BY ts, event_id), list(epoch(ts) ORDER BY ts, event_id)
            FROM ce GROUP BY case_id ORDER BY case_id""").fetchall()
        known = {a for (a,) in conn.execute("SELECT DISTINCT activity FROM ce WHERE activity IN (SELECT unnest(?))",
                                            [list(set(steps))]).fetchall()}
        log_end = conn.execute("SELECT epoch(max(ts)) FROM ce").fetchone()[0]
        return {"rows": rows, "known": known, "log_end": log_end}

    data = workspace._query_filtered(log_id, case_type, cohort, run)
    rows, known, log_end = data.pop("rows"), data.pop("known"), data.pop("log_end")
    missing = [s for s in steps if s not in known]
    if missing:
        raise EventLogWorkspaceError("not in this selection: " + ", ".join(missing[:5]))
    reached = [0] * len(steps)
    gaps: list[list[float]] = [[] for _ in steps]
    stopped_last: list[Counter] = [Counter() for _ in steps]
    stopped_recent = [0] * len(steps)
    total_seconds: list[float] = []
    # A case whose last event falls in the final 5% of the log's time span may still be in progress.
    first_ts = min((_seconds(t[0]) for _, _, t in rows if t), default=None)
    recent_cut = None if first_ts is None or log_end is None else log_end - 0.05 * (log_end - first_ts)
    evidence = []
    evidence_total = 0
    for case_id, activities, stamps in rows:
        times = [_seconds(t) for t in stamps]
        k, at, start_at = 0, None, None
        for activity, t in zip(activities, times):
            if k == len(steps):
                break
            if activity != steps[k] or (k and window is not None and t - at > window):
                # With a window, a later occurrence of the previous step restarts the clock.
                if k and window is not None and activity == steps[k - 1]:
                    at = t
                continue
            if k:
                gaps[k].append(t - at)
            else:
                start_at = t
            at = t
            reached[k] += 1
            k += 1
        if evidence_step is not None and ((evidence_status == "reached" and k >= evidence_step) or
                (evidence_status == "dropped" and k == evidence_step - 1)):
            evidence_total += 1
            if len(evidence) < 50:
                evidence.append({"case_id": case_id, "last_activity": activities[-1],
                                 "steps_reached": k})
        if k == len(steps):
            total_seconds.append(at - start_at)
        elif k:
            # Reached step k-1 (0-based) but not step k: what did it do last?
            stopped_last[k][activities[-1]] += 1
            if recent_cut is not None and times[-1] >= recent_cut:
                stopped_recent[k] += 1
    cases = len(rows)
    out = []
    for k, step in enumerate(steps):
        prev = reached[k - 1] if k else cases
        dropped = prev - reached[k] if k else None
        out.append({
            "step": k + 1, "activity": step, "cases": reached[k],
            "share_of_all_cases": round(reached[k] / cases, 4) if cases else None,
            "share_of_first_step": round(reached[k] / reached[0], 4) if reached[0] else None,
            "conversion_from_previous": (round(reached[k] / prev, 4) if prev else None) if k else None,
            "dropped_after_previous": dropped,
            "hours_from_previous": None if not k else {
                "median": _hours(_quantile(gaps[k], 0.5)), "p90": _hours(_quantile(gaps[k], 0.9)),
                "mean": _hours(sum(gaps[k]) / len(gaps[k]) if gaps[k] else None)},
            "stopped_cases_last_activity": None if not k else [
                {"activity": a, "cases": n} for a, n in sorted(stopped_last[k].items(), key=lambda x: (-x[1], x[0]))[:TOP_STOPS]],
            "stopped_cases_possibly_open": stopped_recent[k] if k else None,
        })
    biggest = max(out[1:], key=lambda s: (s["dropped_after_previous"] or 0, -s["step"]), default=None)
    return {
        "format": FORMAT, "log_id": log_id, "case_notion": data.get("case_notion"),
        "cases": cases, "steps": out, "within_hours": within_hours,
        "evidence": ({"step": evidence_step, "status": evidence_status, "total": evidence_total,
                      "cases": evidence, "truncated": evidence_total > len(evidence)}
                     if evidence_step is not None else None),
        "completed": {"cases": reached[-1], "share_of_first_step": out[-1]["share_of_first_step"],
                      "hours_first_to_last": {"median": _hours(_quantile(total_seconds, 0.5)),
                                              "p90": _hours(_quantile(total_seconds, 0.9))}},
        "largest_drop": ({"after_step": biggest["step"] - 1, "to_step": biggest["step"],
                          "cases": biggest["dropped_after_previous"]}
                         if biggest and biggest["dropped_after_previous"] else None),
        "method": ("each case is followed through the steps in order, taking the first occurrence of each step after "
                   "the previous one" + ("" if within_hours is None else
                                         f", within {within_hours:g} hours of its latest occurrence") +
                   "; other activities in between are allowed"),
        "caveat": ("A funnel shows how far cases got and how long it took, not why the others stopped. Cases marked "
                   "possibly open ended in the last 5% of the log's time span and may still continue."),
    }


__all__ = ["funnel", "FORMAT"]
