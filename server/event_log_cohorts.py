"""Case cohorts for the event-log workspace: filters and cohort benchmarking.

A cohort is a set of cases chosen by the user (start period, resource,
activity or case-object attribute). Benchmarks compare two cohorts
descriptively, with intervals; differences are never attributed to a cause.
"""
from __future__ import annotations

from datetime import timezone
import random
from typing import Any

from shared.event_log_formats import parse_timestamp

from . import event_log_workspace as workspace
from .event_log_analytics import risk_difference
from .event_log_workspace import EventLogWorkspaceError

_SEP = workspace._SEP
_KEYS = {"label", "started_from", "started_before", "resource", "activity", "attribute", "follows"}
MAX_FOLLOW_RULES = 5
BOOTSTRAP_SAMPLE = 5000
BOOTSTRAP_ROUNDS = 400


def _short(value: Any, what: str) -> str:
    if not isinstance(value, str) or not 0 < len(value) <= 200:
        raise EventLogWorkspaceError(f"cohort {what} must be a short text")
    return value


def parse_cohort(spec: Any) -> dict[str, Any]:
    """Validate a cohort; an empty cohort means every case."""
    if spec is None:
        return {}
    if not isinstance(spec, dict) or set(spec) - _KEYS:
        raise EventLogWorkspaceError(
            "cohort accepts label, started_from, started_before, resource, activity, attribute, follows")
    cohort: dict[str, Any] = {}
    if spec.get("label") not in (None, ""):
        cohort["label"] = _short(spec["label"], "label")[:80]
    for key in ("started_from", "started_before"):
        if spec.get(key) not in (None, ""):
            stamp = parse_timestamp(spec[key])
            if stamp is None:
                raise EventLogWorkspaceError(f"cohort {key} must be a timezone-qualified ISO time")
            cohort[key] = stamp.astimezone(timezone.utc).replace(tzinfo=None)
    for key in ("resource", "activity"):
        if spec.get(key) not in (None, ""):
            cohort[key] = _short(spec[key], key)
    if spec.get("attribute") not in (None, ""):
        raw = _short(spec["attribute"], "attribute")
        name, sep, value = raw.partition("=")
        if not sep or not name.strip():
            raise EventLogWorkspaceError("cohort attribute must look like name=value")
        cohort["attribute"] = (name.strip(), value.strip())
    if spec.get("follows") not in (None, [], ""):
        cohort["follows"] = _parse_follows(spec["follows"])
    if "started_from" in cohort and "started_before" in cohort and cohort["started_from"] >= cohort["started_before"]:
        raise EventLogWorkspaceError("cohort period is empty")
    return cohort


def _parse_follows(rules: Any) -> list[dict[str, Any]]:
    """Directly/eventually-follows case rules (Disco-style follower filters), all of which must hold."""
    if not isinstance(rules, list) or not 0 < len(rules) <= MAX_FOLLOW_RULES:
        raise EventLogWorkspaceError(f"cohort follows must be a list of 1–{MAX_FOLLOW_RULES} rules")
    parsed = []
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) - {"from", "to", "mode", "negate"}:
            raise EventLogWorkspaceError("a follows rule has from, to, mode (direct or eventual) and negate")
        mode = rule.get("mode", "eventual")
        if mode not in ("direct", "eventual") or not isinstance(rule.get("negate", False), bool):
            raise EventLogWorkspaceError("a follows rule's mode is direct or eventual and negate is true or false")
        parsed.append({"from": _short(rule.get("from"), "follows activity"), "to": _short(rule.get("to"), "follows activity"),
                       "mode": mode, "negate": rule.get("negate", False)})
    return parsed


def describe(cohort: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in cohort.items():
        if key in ("started_from", "started_before"):
            out[key] = value.isoformat() + "Z"
        elif key == "attribute":
            out[key] = f"{value[0]}={value[1]}"
        else:
            out[key] = value
    return out


def _predicate(cohort: dict[str, Any], log_id: str) -> tuple[str, list[Any]]:
    clauses, params = [], []
    if "started_from" in cohort or "started_before" in cohort:
        having, extra = [], []
        if "started_from" in cohort:
            having.append("min(ts) >= ?"); extra.append(cohort["started_from"])
        if "started_before" in cohort:
            having.append("min(ts) < ?"); extra.append(cohort["started_before"])
        clauses.append(f"case_id IN (SELECT case_id FROM ce GROUP BY case_id HAVING {' AND '.join(having)})")
        params += extra
    if "resource" in cohort:
        clauses.append("case_id IN (SELECT case_id FROM ce WHERE resource = ?)"); params.append(cohort["resource"])
    if "activity" in cohort:
        clauses.append("case_id IN (SELECT case_id FROM ce WHERE activity = ?)"); params.append(cohort["activity"])
    if "attribute" in cohort:
        clauses.append("""case_id IN (SELECT object_id FROM objects WHERE log_id = ?
            AND list_contains(map_extract(json_transform(attributes, '"MAP(VARCHAR, VARCHAR)"'), ?), ?))""")
        params += [log_id, *cohort["attribute"]]
    for rule in cohort.get("follows", []):
        # Order within a case is time, then event identifier, as everywhere in the workspace.
        if rule["mode"] == "direct":
            cases = """SELECT case_id FROM (SELECT case_id, activity, lead(activity) OVER (
                PARTITION BY case_id ORDER BY ts, event_id) AS nxt FROM ce) WHERE activity = ? AND nxt = ?"""
        else:
            cases = """SELECT case_id FROM (SELECT case_id, activity, row_number() OVER (
                PARTITION BY case_id ORDER BY ts, event_id) AS k FROM ce) GROUP BY case_id
                HAVING min(k) FILTER (WHERE activity = ?) < max(k) FILTER (WHERE activity = ?)"""
        clauses.append(f"case_id {'NOT IN' if rule['negate'] else 'IN'} ({cases})")
        params += [rule["from"], rule["to"]]
    return (" AND ".join(clauses) or "TRUE"), params


def restrict(conn, cohort: dict[str, Any], log_id: str, table: str = "ce") -> None:
    """Materialize the cohort's cases from ``ce`` into ``table`` (``ce`` itself by default)."""
    predicate, params = _predicate(cohort, log_id)
    if table != "ce":
        conn.execute(f"CREATE OR REPLACE TEMP TABLE {table} AS SELECT * FROM ce WHERE {predicate}", params)
    elif predicate != "TRUE":
        conn.execute(f"CREATE OR REPLACE TEMP TABLE ce_cohort AS SELECT * FROM ce WHERE {predicate}", params)
        conn.execute("DROP TABLE ce")
        conn.execute("ALTER TABLE ce_cohort RENAME TO ce")


# ---------------------------------------------------------------- benchmark

def _median(values: list[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    return ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2


def median_difference(a: list[float], b: list[float], rounds: int = BOOTSTRAP_ROUNDS,
                      seed: int = 20261023) -> tuple[float, float, float]:
    """Difference of medians (b - a) with a percentile bootstrap 95% interval."""
    # SQL has no guaranteed row order. Canonicalize equal sample multisets
    # before seeded draws so bootstrap intervals do not depend on fetch order.
    a, b = sorted(a), sorted(b)
    rng = random.Random(seed)
    draws = []
    for _ in range(rounds):
        draws.append(_median(rng.choices(b, k=len(b))) - _median(rng.choices(a, k=len(a))))
    draws.sort()
    return _median(b) - _median(a), draws[int(0.025 * (rounds - 1))], draws[int(round(0.975 * (rounds - 1)))]


def _cohort_stats(conn, table: str, sla_seconds: int | None) -> dict[str, Any]:
    conn.execute(f"""CREATE OR REPLACE TEMP TABLE {table}_cs AS SELECT case_id,
          date_diff('second', min(ts), max(ts)) AS duration, count(*) > count(DISTINCT activity) AS rework,
          string_agg(activity, '{_SEP}' ORDER BY ts, event_id) AS path
        FROM {table} GROUP BY case_id""")
    cases, q, rework = conn.execute(f"""SELECT count(*), quantile_cont(duration, [0.5, 0.9]),
        count(*) FILTER (WHERE rework) FROM {table}_cs""").fetchone()
    stats: dict[str, Any] = {"cases": cases, "rework_cases": rework,
                             "rework_rate": round(rework / cases, 6) if cases else None,
                             "throughput_seconds": {"p50": q[0], "p90": q[1]} if cases else None}
    if sla_seconds is not None:
        breached = conn.execute(f"SELECT count(*) FROM {table}_cs WHERE duration > ?", [sla_seconds]).fetchone()[0]
        stats["sla_breached_cases"] = breached
        stats["sla_breach_rate"] = round(breached / cases, 6) if cases else None
    return stats


def benchmark(log_id: str, cohorts: Any, case_type: str | None = None, sla_seconds: int | None = None,
              limit: int = 15) -> dict[str, Any]:
    if not isinstance(cohorts, list) or len(cohorts) != 2:
        raise EventLogWorkspaceError("benchmark compares exactly two cohorts")
    first, second = parse_cohort(cohorts[0]), parse_cohort(cohorts[1])
    if sla_seconds is not None and (not isinstance(sla_seconds, int) or isinstance(sla_seconds, bool)
                                    or not 0 < sla_seconds <= 10 ** 9):
        raise EventLogWorkspaceError("sla_seconds must be a positive number of seconds")
    limit = max(1, min(int(limit), 50))

    def run(conn):
        restrict(conn, first, log_id, "ca")
        restrict(conn, second, log_id, "cb")
        a, b = _cohort_stats(conn, "ca", sla_seconds), _cohort_stats(conn, "cb", sla_seconds)
        if not a["cases"] or not b["cases"]:
            raise EventLogWorkspaceError("each cohort needs at least one case")
        overlap = conn.execute("SELECT count(*) FROM ca_cs JOIN cb_cs USING (case_id)").fetchone()[0]
        differences: dict[str, Any] = {}

        def rate(name, ya, yb):
            d, low, high = risk_difference(yb, b["cases"], ya, a["cases"])
            differences[name] = {"difference": round(d, 6), "ci95": [round(low, 6), round(high, 6)]}
        rate("rework_rate", a["rework_cases"], b["rework_cases"])
        if sla_seconds is not None:
            rate("sla_breach_rate", a["sla_breached_cases"], b["sla_breached_cases"])
        samples = [[r[0] for r in conn.execute(
            f"SELECT duration FROM {t}_cs USING SAMPLE reservoir({BOOTSTRAP_SAMPLE} ROWS) REPEATABLE (42)").fetchall()]
            for t in ("ca", "cb")]
        d, low, high = median_difference(*samples)
        differences["median_throughput_seconds"] = {
            "difference": d, "ci95": [low, high],
            "method": f"percentile bootstrap, {BOOTSTRAP_ROUNDS} rounds",
            # Full-cohort p50/p90 above and this sampled bootstrap can use
            # different denominators. Expose actual support, not just the cap.
            "sampled_cases_first": len(samples[0]),
            "sampled_cases_second": len(samples[1]),
            "sample_fraction_first": round(len(samples[0]) / a["cases"], 6),
            "sample_fraction_second": round(len(samples[1]) / b["cases"], 6),
            "sampled_median_throughput_seconds_first": _median(samples[0]),
            "sampled_median_throughput_seconds_second": _median(samples[1]),
            "sampling_truncated_first": len(samples[0]) < a["cases"],
            "sampling_truncated_second": len(samples[1]) < b["cases"],
        }
        presence = conn.execute("""WITH pa AS (SELECT activity, count(DISTINCT case_id) AS n FROM ca GROUP BY 1),
              pb AS (SELECT activity, count(DISTINCT case_id) AS n FROM cb GROUP BY 1)
            SELECT coalesce(pa.activity, pb.activity), coalesce(pa.n, 0), coalesce(pb.n, 0)
            FROM pa FULL JOIN pb ON pa.activity = pb.activity""").fetchall()
        activities = []
        for activity, na, nb in presence:
            d, low, high = risk_difference(nb, b["cases"], na, a["cases"])
            activities.append({"activity": activity, "share_in_first": round(na / a["cases"], 6),
                               "share_in_second": round(nb / b["cases"], 6), "difference": round(d, 6),
                               "ci95": [round(low, 6), round(high, 6)], "ci_excludes_zero": low > 0 or high < 0})
        activities.sort(key=lambda r: (not r["ci_excludes_zero"], -abs(r["difference"]), r["activity"]))
        variants = conn.execute(f"""WITH va AS (SELECT path, count(*) AS n FROM ca_cs GROUP BY 1),
              vb AS (SELECT path, count(*) AS n FROM cb_cs GROUP BY 1)
            SELECT coalesce(va.path, vb.path) AS path, coalesce(va.n, 0), coalesce(vb.n, 0)
            FROM va FULL JOIN vb ON va.path = vb.path
            ORDER BY greatest(coalesce(va.n, 0) / {a['cases']}, coalesce(vb.n, 0) / {b['cases']}) DESC, 1
            LIMIT {limit}""").fetchall()
        return {
            "cohorts": [{"definition": describe(first), **a}, {"definition": describe(second), **b}],
            "overlapping_cases": overlap,
            "differences_second_minus_first": differences,
            "activity_presence": activities[:limit * 2],
            "variants": [{"path": p.split(_SEP), "share_in_first": round(na / a["cases"], 6),
                          "share_in_second": round(nb / b["cases"], 6)} for p, na, nb in variants],
            "intervals_assume_independent_cohorts": True,
            "note": ("cohorts share cases, so intervals overstate certainty" if overlap else
                     "differences describe these cohorts; they are not explained or caused by the cohort definition"),
        }
    return workspace._query(log_id, case_type, run)
