"""Pure descriptive rule summaries over reconstructed retained signal episodes."""
from collections import Counter, defaultdict
from datetime import datetime
from statistics import median

COUNTS = (
    "retained_triggers", "retained_clearances", "cleared_within_deadline",
    "cleared_after_deadline", "unresolved_window_past_deadline",
    "unresolved_window_not_past_deadline", "interrupted_triggers",
    "ambiguous_triggers", "ambiguous_clearances", "leading_clearances",
    "observed_retrigger_pairs", "retrigger_within_horizon",
)


def stamp(value):
    return datetime.fromisoformat(value.removesuffix("Z"))


def micros(delta):
    return (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds


def distribution(values):
    return {"count": len(values), "minimum_us": min(values) if values else None,
            "median_us": median(values) if values else None,
            "maximum_us": max(values) if values else None}


def summarize(episodes, end, deadline_seconds, recurrence_seconds):
    """Partition every retained trigger and clearance, without imputing ties.

    Episodes must be the canonical reconstruction of the same selected window.
    Retrigger gaps only use adjacent evidence rows of the exact same rule:
    a cleared pair followed by an unambiguous trigger, regardless of its later
    outcome. Leading clearances, ties and interruptions cannot bridge gaps.
    """
    groups = defaultdict(list)
    for row in episodes:
        groups[(row["log_id"], row["rule_id"])].append(row)
    deadline = deadline_seconds * 1000000
    horizon = recurrence_seconds * 1000000
    result = []
    for (log_id, rule_id), rows in groups.items():
        rows.sort(key=lambda r: (r["sort_at"], r["sort_id"]))
        counts = Counter(dict.fromkeys(COUNTS, 0))
        durations, gaps = [], []
        previous_clear = None
        for row in rows:
            status = row["status"]
            trigger = stamp(row["trigger_at"]) if row["trigger_at"] else None
            clear = stamp(row["clear_at"]) if row["clear_at"] else None
            counts["retained_triggers"] += int(trigger is not None) + row["ambiguous_trigger_count"]
            counts["retained_clearances"] += int(clear is not None) + row["ambiguous_clear_count"]
            if previous_clear is not None and trigger is not None:
                gap = micros(trigger - previous_clear)
                if gap <= 0:
                    raise ValueError("Invalid adjacent retained trigger chronology")
                gaps.append(gap)
                counts["observed_retrigger_pairs"] += 1
                counts["retrigger_within_horizon"] += int(gap <= horizon)
            previous_clear = None
            if status == "cleared":
                elapsed = micros(clear - trigger)
                if elapsed <= 0:
                    raise ValueError("Invalid retained clearance chronology")
                durations.append(elapsed)
                counts["cleared_within_deadline" if elapsed <= deadline else "cleared_after_deadline"] += 1
                previous_clear = clear
            elif status == "unresolved_at_window_end":
                # Exclusive boundary: exactly at the deadline is not a full
                # observed window through the deadline. No clearance is imputed.
                key = ("unresolved_window_past_deadline" if micros(end - trigger) > deadline
                       else "unresolved_window_not_past_deadline")
                counts[key] += 1
            elif status in ("interrupted_by_repeated_trigger", "interrupted_by_ambiguous_timestamp"):
                counts["interrupted_triggers"] += 1
            elif status == "clearance_without_retained_trigger":
                counts["leading_clearances"] += 1
            elif status == "ambiguous_timestamp":
                counts["ambiguous_triggers"] += row["ambiguous_trigger_count"]
                counts["ambiguous_clearances"] += row["ambiguous_clear_count"]
            else:
                raise ValueError("Unknown retained episode status")
        accounted_triggers = sum(counts[k] for k in COUNTS[2:8])
        accounted_clearances = (counts["cleared_within_deadline"] + counts["cleared_after_deadline"]
                                + counts["ambiguous_clearances"] + counts["leading_clearances"])
        if accounted_triggers != counts["retained_triggers"] or accounted_clearances != counts["retained_clearances"]:
            raise ValueError("Retained rule transition accounting failed")
        result.append({"log_id": log_id, "rule_id": rule_id, "log_name": rows[-1]["log_name"],
                       "rule_name": rows[-1]["rule_name"], **counts,
                       "clearance_elapsed": distribution(durations), "adjacent_retrigger_gap": distribution(gaps)})
    return result


def totals(rows):
    return {key: sum(row[key] for row in rows) for key in COUNTS}


def ordered(rows, sort):
    if sort == "deadline":
        key = lambda r: (-(r["cleared_after_deadline"] + r["unresolved_window_past_deadline"]),
                         -r["retained_triggers"], r["log_id"], r["rule_id"])
    elif sort == "recurrence":
        key = lambda r: (-r["retrigger_within_horizon"], -r["retained_triggers"], r["log_id"], r["rule_id"])
    elif sort == "volume":
        key = lambda r: (-r["retained_triggers"], r["log_id"], r["rule_id"])
    else:
        raise ValueError("Unknown rule review ordering")
    return sorted(rows, key=key)
