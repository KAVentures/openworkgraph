"""A deterministic synthetic IT service management CSV log with known deviations, for the domain pack tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

START = datetime(2024, 9, 2, 8, tzinfo=timezone.utc)
NAMES = {"log_incident": "New", "assign": "Assigned to group", "start_work": "In progress",
         "reassign": "Reassigned", "resolve": "Resolved", "reopen": "Reopened", "close": "Closed"}
LATE_ASSIGN = {3, 13, 23, 33, 43, 53}          # assigned after 6 h (target 4 h)
LATE_RESOLVE = {7, 37, 52}                     # resolved after 100 h (target 72 h)
PING_PONG = {5, 17, 29, 41}                    # reassigned three times (more than two)
REOPENED = {4, 13, 22, 31, 40, 49, 58}         # reopened once and resolved again 30 h later
RESOLVED_BEFORE_ASSIGN = {50}
CLOSED_BEFORE_RESOLVED = {11}


def incidents(count: int = 60) -> str:
    """Incident k is logged every 2 hours; one row per event: incident, activity, time, group."""
    rows = ["incident,status,time,group"]

    def add(k, role, hours):
        rows.append(f"INC{k:04d},{NAMES[role]},{(START + timedelta(hours=2 * k + hours)).isoformat()},g{k % 3}")
    for k in range(count):
        add(k, "log_incident", 0)
        assign_at = 6 if k in LATE_ASSIGN else 1
        resolve_at = 100 if k in LATE_RESOLVE else 24
        if k in RESOLVED_BEFORE_ASSIGN:
            add(k, "resolve", 0.5)
            add(k, "assign", assign_at)
        else:
            add(k, "assign", assign_at)
            if k % 2:
                add(k, "start_work", assign_at + 1)
            for n in range(3 if k in PING_PONG else 1 if k % 6 == 1 else 0):
                add(k, "reassign", assign_at + 1.5 + n)
            add(k, "resolve", resolve_at)
        if k in REOPENED:
            add(k, "reopen", resolve_at + 2)
            add(k, "resolve", resolve_at + 30)
        add(k, "close", 20 if k in CLOSED_BEFORE_RESOLVED else resolve_at + (32 if k in REOPENED else 48))
    return "\n".join(rows)


MAPPING = {"object_types": {"incident": "case"}, "activities": dict(NAMES)}
CSV_MAPPING = {"case_column": "incident", "activity_column": "status", "timestamp_column": "time",
               "resource_column": "group"}
