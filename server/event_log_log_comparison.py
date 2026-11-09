"""Compare two saved event logs: KPIs, activities, paths and variants.

Typical uses are two regions or business units exported separately, or the same
process before and after a system migration. Each side is one saved log with an
optional case notion and case filter. Activities are matched by exact name after
each log's applied activity mapping, so a mapping can harmonize names that differ
between source systems.

Differences in case shares come with Newcombe 95% intervals; durations are
compared with quantiles and the probability that a random comparison case takes
longer than a random baseline case. All of this is descriptive: the two logs may
differ in extraction, coverage and case mix, and nothing here attributes a
difference to a cause.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

from . import event_log_analytics as analytics
from . import event_log_cohorts as cohorts
from . import event_log_workspace as workspace

FORMAT = "openworkgraph.event-log-comparison.v1"
_SEP = "\x1f"


class Side(BaseModel):
    model_config = ConfigDict(extra="forbid")
    log_id: str = Field(min_length=1, max_length=64)
    case_type: str | None = Field(default=None, min_length=1, max_length=200)
    cohort: dict[str, Any] = Field(default_factory=dict)


class ComparisonSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    baseline: Side
    comparison: Side
    limit: StrictInt = Field(default=25, ge=1, le=200)


def _parse(spec: Any) -> ComparisonSpec:
    try:
        parsed = ComparisonSpec.model_validate(spec)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise workspace.EventLogWorkspaceError(
            "comparison settings are invalid: " + ".".join(str(p) for p in first["loc"]) + " " + first["msg"]) from None
    for side in (parsed.baseline, parsed.comparison):
        cohorts.parse_cohort(side.cohort)
    if parsed.baseline == parsed.comparison:
        raise workspace.EventLogWorkspaceError("baseline and comparison are the same log, case notion and filter")
    return parsed


def _profile(side: Side) -> dict[str, Any]:
    def run(conn):
        cohorts.restrict(conn, cohorts.parse_cohort(side.cohort), side.log_id)
        conn.execute(f"""CREATE OR REPLACE TEMP TABLE lc_cases AS SELECT case_id, count(*) AS events,
              epoch(max(ts) - min(ts)) AS duration, count(*) > count(DISTINCT activity) AS repeats,
              string_agg(activity, '{_SEP}' ORDER BY ts, event_id) AS path
            FROM ce GROUP BY case_id""")
        cases, events, repeats, first, last = conn.execute("""SELECT count(*), coalesce(sum(events), 0),
              count(*) FILTER (WHERE repeats), (SELECT min(ts) FROM ce), (SELECT max(ts) FROM ce) FROM lc_cases""").fetchone()
        durations = [float(d) for (d,) in conn.execute("SELECT duration FROM lc_cases ORDER BY duration").fetchall()]
        activity_cases = dict(conn.execute("SELECT activity, count(DISTINCT case_id) FROM ce GROUP BY 1").fetchall())
        edges = {(a, b): n for a, b, n in conn.execute("""SELECT activity, nxt, count(DISTINCT case_id) FROM (
              SELECT case_id, activity, lead(activity) OVER (PARTITION BY case_id ORDER BY ts, event_id) AS nxt FROM ce)
            WHERE nxt IS NOT NULL GROUP BY 1, 2""").fetchall()}
        variants = dict(conn.execute("SELECT path, count(*) FROM lc_cases GROUP BY 1").fetchall())
        return {"cases": cases, "events": int(events), "repeat_cases": repeats,
                "first_event_at": first.isoformat() + "Z" if first else None,
                "last_event_at": last.isoformat() + "Z" if last else None,
                "durations": durations, "activity_cases": activity_cases, "edges": edges, "variants": variants}
    profile = workspace._query(side.log_id, side.case_type, run)
    profile["log"] = next((log for log in workspace.list_logs()["logs"] if log["log_id"] == side.log_id), {})
    return profile


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    position = (len(values) - 1) * q
    low = int(position)
    high = min(low + 1, len(values) - 1)
    return values[low] + (values[high] - values[low]) * (position - low)


def _longer_probability(baseline: list[float], comparison: list[float]) -> float | None:
    """P(a random comparison case lasts longer than a random baseline case), ties counted half."""
    if not baseline or not comparison:
        return None
    total = 0.0
    for value in comparison:
        below = bisect_left(baseline, value)
        ties = bisect_right(baseline, value) - below
        total += below + ties / 2
    return round(total / (len(baseline) * len(comparison)), 6)


def _share_row(key: dict[str, Any], y0: int, n0: int, y1: int, n1: int) -> dict[str, Any]:
    row = {**key, "baseline_cases": y0, "comparison_cases": y1,
           "baseline_share": round(y0 / n0, 6) if n0 else None, "comparison_share": round(y1 / n1, 6) if n1 else None,
           "presence": "both" if y0 and y1 else ("only_baseline" if y0 else "only_comparison")}
    if n0 and n1:
        d, low, high = analytics.risk_difference(y1, n1, y0, n0)
        row.update({"share_difference": round(d, 6), "interval_95": [round(low, 6), round(high, 6)],
                    "interval_excludes_zero": low > 0 or high < 0})
    else:
        row.update({"share_difference": None, "interval_95": None, "interval_excludes_zero": False})
    return row


def _ranked(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda r: (-abs(r["share_difference"] or 0), -(r["baseline_cases"] + r["comparison_cases"]),
                                       str(r.get("activity") or r.get("from")), str(r.get("to") or "")))[:limit]


def _kpis(p: dict[str, Any]) -> dict[str, Any]:
    d, cases = p["durations"], p["cases"]
    return {"cases": cases, "events": p["events"], "activities": len(p["activity_cases"]),
            "distinct_variants": len(p["variants"]),
            "events_per_case": round(p["events"] / cases, 3) if cases else None,
            "repeat_rate": round(p["repeat_cases"] / cases, 6) if cases else None,
            "duration_seconds": {"p25": _quantile(d, 0.25), "median": _quantile(d, 0.5), "p75": _quantile(d, 0.75),
                                 "p90": _quantile(d, 0.9), "mean": (sum(d) / len(d)) if d else None},
            "first_event_at": p["first_event_at"], "last_event_at": p["last_event_at"]}


def compare(spec: Any) -> dict[str, Any]:
    settings = _parse(spec)
    base, comp = _profile(settings.baseline), _profile(settings.comparison)
    n0, n1 = base["cases"], comp["cases"]
    if not n0 or not n1:
        raise workspace.EventLogWorkspaceError("each side needs at least one case after its filter")
    kb, kc = _kpis(base), _kpis(comp)
    delta = {"cases": n1 - n0,
             "events_per_case": round(kc["events_per_case"] - kb["events_per_case"], 3),
             "repeat_rate": round(kc["repeat_rate"] - kb["repeat_rate"], 6),
             "median_duration_seconds": kc["duration_seconds"]["median"] - kb["duration_seconds"]["median"],
             "p90_duration_seconds": kc["duration_seconds"]["p90"] - kb["duration_seconds"]["p90"]}
    repeat = analytics.risk_difference(comp["repeat_cases"], n1, base["repeat_cases"], n0)
    delta["repeat_rate_interval_95"] = [round(repeat[1], 6), round(repeat[2], 6)]

    names = sorted(set(base["activity_cases"]) | set(comp["activity_cases"]))
    activity_rows = [_share_row({"activity": a}, base["activity_cases"].get(a, 0), n0, comp["activity_cases"].get(a, 0), n1)
                     for a in names]
    edge_keys = sorted(set(base["edges"]) | set(comp["edges"]))
    edge_rows = [_share_row({"from": a, "to": b}, base["edges"].get((a, b), 0), n0, comp["edges"].get((a, b), 0), n1)
                 for a, b in edge_keys]

    shared = set(base["variants"]) & set(comp["variants"])
    top = sorted(set(sorted(base["variants"], key=lambda v: (-base["variants"][v], v))[:settings.limit])
                 | set(sorted(comp["variants"], key=lambda v: (-comp["variants"][v], v))[:settings.limit]))
    variant_rows = _ranked([_share_row({"path": v.split(_SEP)}, base["variants"].get(v, 0), n0,
                                       comp["variants"].get(v, 0), n1) for v in top], settings.limit)

    def side_info(side: Side, profile: dict[str, Any]) -> dict[str, Any]:
        log = profile["log"]
        return {"log_id": side.log_id, "name": log.get("name"), "source_format": log.get("source_format"),
                "case_type": side.case_type, "cohort": cohorts.describe(cohorts.parse_cohort(side.cohort))}

    return {
        "format": FORMAT,
        "baseline": side_info(settings.baseline, base),
        "comparison": side_info(settings.comparison, comp),
        "kpis": {"baseline": kb, "comparison": kc, "difference": delta},
        "duration": {"probability_comparison_case_takes_longer": _longer_probability(base["durations"], comp["durations"]),
                     "definition": "chance that a random comparison case lasts longer than a random baseline case; "
                                   "0.5 means no shift, ties count half"},
        "activities": {"only_baseline": [r["activity"] for r in activity_rows if r["presence"] == "only_baseline"],
                       "only_comparison": [r["activity"] for r in activity_rows if r["presence"] == "only_comparison"],
                       "shared": sum(r["presence"] == "both" for r in activity_rows),
                       "largest_share_differences": _ranked(activity_rows, settings.limit)},
        "paths": {"only_baseline": sum(r["presence"] == "only_baseline" for r in edge_rows),
                  "only_comparison": sum(r["presence"] == "only_comparison" for r in edge_rows),
                  "shared": sum(r["presence"] == "both" for r in edge_rows),
                  "largest_share_differences": _ranked(edge_rows, settings.limit)},
        "variants": {"shared_variants": len(shared),
                     "baseline_cases_in_shared_variants": round(sum(base["variants"][v] for v in shared) / n0, 6),
                     "comparison_cases_in_shared_variants": round(sum(comp["variants"][v] for v in shared) / n1, 6),
                     "largest_share_differences": variant_rows},
        "activity_names_matched_exactly": True,
        "interpretation": ("Descriptive comparison of two saved logs. Activities are matched by exact name after each "
                           "log's activity mapping. The logs may differ in extraction, coverage and case mix, so a "
                           "difference is not evidence of its cause. Intervals are 95% Newcombe intervals for "
                           "differences in case shares and are not corrected for the number of rows shown."),
    }
