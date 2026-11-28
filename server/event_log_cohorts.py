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
from .event_log_variant_coverage import VARIANT_KEY_SQL
from .event_log_workspace import EventLogWorkspaceError

_KEYS = {"label", "started_from", "started_before", "resource", "activity", "attribute", "attribute_range", "event_attribute", "tag",
         "follows", "variants", "duration", "starts_with", "ends_with", "ended_from", "ended_before", "events",
         "path_cases", "rework", "without_tag", "without_activity", "all_of"}
MAX_FOLLOW_RULES = 5
MAX_FOLLOW_HOURS = 24 * 3650
MAX_COHORT_VARIANTS = 500
MAX_VARIANT_LENGTH = 1000
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
            "cohort accepts label, started_from, started_before, resource, activity, attribute, attribute_range, event_attribute, "
            "tag, follows, variants, duration, starts_with, ends_with, ended_from, ended_before, events, path_cases, rework, without_tag, without_activity, all_of")
    cohort: dict[str, Any] = {}
    if spec.get("label") not in (None, ""):
        cohort["label"] = _short(spec["label"], "label")[:80]
    for key in ("started_from", "started_before", "ended_from", "ended_before"):
        if spec.get(key) not in (None, ""):
            stamp = parse_timestamp(spec[key])
            if stamp is None:
                raise EventLogWorkspaceError(f"cohort {key} must be a timezone-qualified ISO time")
            cohort[key] = stamp.astimezone(timezone.utc).replace(tzinfo=None)
    for key in ("resource", "activity", "without_activity", "tag", "without_tag", "starts_with", "ends_with"):
        if spec.get(key) not in (None, ""):
            cohort[key] = _short(spec[key], key)
    # ``attribute`` matches the case object; ``event_attribute`` matches any event of the case,
    # which is where columns kept from a CSV import live.
    for key, what in (("attribute", "attribute"), ("event_attribute", "event attribute")):
        if spec.get(key) not in (None, ""):
            raw = _short(spec[key], key)
            name, sep, value = raw.partition("=")
            if not sep or not name.strip():
                raise EventLogWorkspaceError(f"cohort {what} must look like name=value")
            cohort[key] = (name.strip(), value.strip())
    if spec.get("attribute_range") not in (None, {}, ""):
        cohort["attribute_range"] = _parse_range(spec["attribute_range"])
    if spec.get("follows") not in (None, [], ""):
        cohort["follows"] = _parse_follows(spec["follows"])
    if spec.get("duration") not in (None, {}, ""):
        cohort["duration"] = _parse_duration(spec["duration"])
    if spec.get("variants") not in (None, []):
        cohort["variants"] = _parse_variants(spec["variants"])
    for key, what in (("events", "events"), ("path_cases", "path_cases")):
        if spec.get(key) not in (None, {}, ""):
            cohort[key] = _parse_count(spec[key], what)
    if spec.get("rework") not in (None, ""):
        if not isinstance(spec["rework"], bool):
            raise EventLogWorkspaceError("cohort rework must be true (cases that repeat an activity) or false")
        cohort["rework"] = spec["rework"]
    if "all_of" in spec:
        parts = spec["all_of"]
        if (not isinstance(parts, list) or not 2 <= len(parts) <= 4
                or any(not isinstance(part, dict) or "all_of" in part for part in parts)):
            raise EventLogWorkspaceError("cohort all_of needs 2–4 case filters without nested all_of")
        filters = [parse_cohort(part) for part in parts]
        if any(not (part.keys() - {"label"}) for part in filters):
            raise EventLogWorkspaceError("each cohort all_of member needs a case filter")
        cohort["all_of"] = filters
    if "started_from" in cohort and "started_before" in cohort and cohort["started_from"] >= cohort["started_before"]:
        raise EventLogWorkspaceError("cohort period is empty")
    if "ended_from" in cohort and "ended_before" in cohort and cohort["ended_from"] >= cohort["ended_before"]:
        raise EventLogWorkspaceError("cohort end period is empty")
    return cohort


def _parse_follows(rules: Any) -> list[dict[str, Any]]:
    """Directly/eventually-follows case rules (Disco-style follower filters), all of which must hold."""
    if not isinstance(rules, list) or not 0 < len(rules) <= MAX_FOLLOW_RULES:
        raise EventLogWorkspaceError(f"cohort follows must be a list of 1–{MAX_FOLLOW_RULES} rules")
    parsed = []
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) - {"from", "to", "mode", "negate", "min_hours", "max_hours"}:
            raise EventLogWorkspaceError("a follows rule has from, to, mode (direct or eventual), negate, "
                                         "and optionally min_hours and max_hours")
        mode = rule.get("mode", "eventual")
        if mode not in ("direct", "eventual") or not isinstance(rule.get("negate", False), bool):
            raise EventLogWorkspaceError("a follows rule's mode is direct or eventual and negate is true or false")
        entry = {"from": _short(rule.get("from"), "follows activity"), "to": _short(rule.get("to"), "follows activity"),
                 "mode": mode, "negate": rule.get("negate", False)}
        # Optional time between the two steps (Disco/Celonis-style throughput filter on a segment).
        for key in ("min_hours", "max_hours"):
            value = rule.get(key)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value \
                    or not 0 <= value <= MAX_FOLLOW_HOURS:
                raise EventLogWorkspaceError(f"a follows rule's {key} must be a number of hours from 0 to {MAX_FOLLOW_HOURS}")
            entry[key] = float(value)
        if ("min_hours" in entry or "max_hours" in entry) and entry["negate"]:
            raise EventLogWorkspaceError("a time limit applies only to a rule that keeps cases where one step follows another")
        if "min_hours" in entry and "max_hours" in entry and entry["min_hours"] >= entry["max_hours"]:
            raise EventLogWorkspaceError("a follows rule's min_hours must be below its max_hours")
        parsed.append(entry)
    return parsed


def _parse_variants(variants: Any) -> list[list[str]]:
    """Exact variants (each an ordered list of activities), e.g. one trace cluster; a case matches any one."""
    if not isinstance(variants, list) or not 0 < len(variants) <= MAX_COHORT_VARIANTS:
        raise EventLogWorkspaceError(f"cohort variants must be a list of 1–{MAX_COHORT_VARIANTS} activity lists")
    parsed = []
    for variant in variants:
        if not isinstance(variant, list) or not 0 < len(variant) <= MAX_VARIANT_LENGTH:
            raise EventLogWorkspaceError(f"each cohort variant is a list of 1–{MAX_VARIANT_LENGTH} activities")
        parsed.append([_short(a, "variant activity") for a in variant])
    return parsed


def _parse_duration(spec: Any) -> dict[str, float]:
    """Case duration (first to last recorded event) in seconds: ``min`` inclusive, ``max`` exclusive."""
    if not isinstance(spec, dict) or set(spec) - {"min", "max"}:
        raise EventLogWorkspaceError("cohort duration is {min, max} in seconds")
    bounds: dict[str, float] = {}
    for key in ("min", "max"):
        value = spec.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value \
                or not 0 <= value <= 10 ** 10:
            raise EventLogWorkspaceError(f"cohort duration {key} must be a number of seconds from 0 to 10000000000")
        bounds[key] = float(value)
    if not bounds:
        raise EventLogWorkspaceError("cohort duration needs min, max or both")
    if "min" in bounds and "max" in bounds and bounds["min"] >= bounds["max"]:
        raise EventLogWorkspaceError("cohort duration min must be below max")
    return bounds


def _parse_count(spec: Any, what: str) -> dict[str, int]:
    """A whole-number range, both ends inclusive: events per case, or cases that share the case's path."""
    if not isinstance(spec, dict) or set(spec) - {"min", "max"}:
        raise EventLogWorkspaceError(f"cohort {what} is {{min, max}}")
    bounds: dict[str, int] = {}
    for key in ("min", "max"):
        value = spec.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 10 ** 9:
            raise EventLogWorkspaceError(f"cohort {what} {key} must be a whole number from 1 to 1000000000")
        bounds[key] = value
    if not bounds:
        raise EventLogWorkspaceError(f"cohort {what} needs min, max or both")
    if "min" in bounds and "max" in bounds and bounds["min"] > bounds["max"]:
        raise EventLogWorkspaceError(f"cohort {what} min must not be above max")
    return bounds


def _parse_range(spec: Any) -> dict[str, Any]:
    """A numeric case-attribute range: ``min`` inclusive, ``max`` exclusive, either may be omitted."""
    if not isinstance(spec, dict) or set(spec) - {"name", "min", "max"}:
        raise EventLogWorkspaceError("cohort attribute_range is {name, min, max}")
    name = _short(spec.get("name"), "attribute_range name").strip()
    bounds = {}
    for key in ("min", "max"):
        value = spec.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or abs(value) == float("inf"):
            raise EventLogWorkspaceError(f"cohort attribute_range {key} must be a finite number")
        bounds[key] = float(value)
    if not bounds:
        raise EventLogWorkspaceError("cohort attribute_range needs min, max or both")
    if "min" in bounds and "max" in bounds and bounds["min"] >= bounds["max"]:
        raise EventLogWorkspaceError("cohort attribute_range min must be below max")
    return {"name": name, **bounds}


def describe(cohort: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in cohort.items():
        if key in ("started_from", "started_before", "ended_from", "ended_before"):
            out[key] = value.isoformat() + "Z"
        elif key == "all_of":
            out[key] = [describe(part) for part in value]
        elif key in ("attribute", "event_attribute"):
            out[key] = f"{value[0]}={value[1]}"
        elif key in ("attribute_range", "duration", "events", "path_cases"):
            out[key] = dict(value)
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
    if "without_activity" in cohort:
        # Cases that never do the activity (for example every case without an escalation).
        clauses.append("case_id NOT IN (SELECT case_id FROM ce WHERE activity = ?)"); params.append(cohort["without_activity"])
    if "without_tag" in cohort:
        # Cases that do not carry the tag (for example everything except the off-model cases).
        clauses.append("case_id NOT IN (SELECT case_id FROM case_tags WHERE log_id = ? AND tag = ?)")
        params += [log_id, cohort["without_tag"].strip()]
    if "tag" in cohort:
        # Cases the analyst tagged (event_log_case_tags); a tag is a judgement, not source data.
        clauses.append("case_id IN (SELECT case_id FROM case_tags WHERE log_id = ? AND tag = ?)")
        params += [log_id, cohort["tag"].strip()]
    if "attribute" in cohort:
        clauses.append("""case_id IN (SELECT object_id FROM objects WHERE log_id = ?
            AND list_contains(map_extract(json_transform(attributes, '"MAP(VARCHAR, VARCHAR)"'), ?), ?))""")
        params += [log_id, *cohort["attribute"]]
    if "attribute_range" in cohort:
        # Values that are not numbers never match a range.
        r = cohort["attribute_range"]
        cond = ["isfinite(x)"] + (["x >= ?"] if "min" in r else []) + (["x < ?"] if "max" in r else [])
        clauses.append(f"""case_id IN (SELECT object_id FROM (SELECT object_id,
            try_cast(map_extract_value(json_transform(attributes, '"MAP(VARCHAR, VARCHAR)"'), ?) AS DOUBLE) AS x
            FROM objects WHERE log_id = ?) WHERE {' AND '.join(cond)})""")
        params += [r["name"], log_id] + ([r["min"]] if "min" in r else []) + ([r["max"]] if "max" in r else [])
    if "event_attribute" in cohort:
        # Values are compared as the stored text (imports keep attribute values as text).
        clauses.append("""case_id IN (SELECT case_id FROM ce, json_each(ce.attributes) j
            WHERE j.key = ? AND (CASE WHEN j.type = 'VARCHAR' THEN j.value->>'$' ELSE j.value::VARCHAR END) = ?)""")
        params += list(cohort["event_attribute"])
    for rule in cohort.get("follows", []):
        if "min_hours" in rule or "max_hours" in rule:
            # Time from the first occurrence of `from` to the next `to` after it (or, for direct rules, to the
            # first `to` right after a `from`): min_hours inclusive, max_hours exclusive.
            if rule["mode"] == "direct":
                pairs = """SELECT case_id, arg_min(gap, k) AS gap FROM (SELECT case_id, k, activity,
                      lead(activity) OVER w AS nxt, epoch(lead(ts) OVER w - ts) AS gap FROM (SELECT case_id, activity,
                      ts, row_number() OVER (PARTITION BY case_id ORDER BY ts, event_id) AS k FROM ce) WINDOW w AS (
                      PARTITION BY case_id ORDER BY k)) WHERE activity = ? AND nxt = ? GROUP BY case_id"""
            else:
                pairs = """SELECT f.case_id, epoch(min(t.ts) FILTER (WHERE t.k > f.k) - f.ts) AS gap FROM (
                      SELECT case_id, arg_min(ts, k) AS ts, min(k) AS k FROM (SELECT case_id, activity, ts,
                        row_number() OVER (PARTITION BY case_id ORDER BY ts, event_id) AS k FROM ce)
                      WHERE activity = ? GROUP BY case_id) f JOIN (SELECT case_id, activity, ts,
                        row_number() OVER (PARTITION BY case_id ORDER BY ts, event_id) AS k FROM ce) t
                      ON t.case_id = f.case_id AND t.activity = ? GROUP BY f.case_id, f.ts"""
            cond = (["gap >= ?"] if "min_hours" in rule else []) + (["gap < ?"] if "max_hours" in rule else [])
            clauses.append(f"case_id IN (SELECT case_id FROM ({pairs}) WHERE gap IS NOT NULL AND {' AND '.join(cond)})")
            params += [rule["from"], rule["to"]] + [rule[k] * 3600 for k in ("min_hours", "max_hours") if k in rule]
            continue
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
    if "duration" in cohort:
        # Disco-style performance filter on case duration, first to last recorded event.
        d = cohort["duration"]
        having = (["epoch(max(ts) - min(ts)) >= ?"] if "min" in d else []) + (["epoch(max(ts) - min(ts)) < ?"] if "max" in d else [])
        clauses.append(f"case_id IN (SELECT case_id FROM ce GROUP BY case_id HAVING {' AND '.join(having)})")
        params += ([d["min"]] if "min" in d else []) + ([d["max"]] if "max" in d else [])
    for key, pick in (("starts_with", "arg_min"), ("ends_with", "arg_max")):
        # Endpoints filter: the case's first or last activity in recorded order (time, then event id).
        if key in cohort:
            clauses.append(f"""case_id IN (SELECT case_id FROM ce GROUP BY case_id
                HAVING {pick}(activity, (ts, event_id)) = ?)""")
            params.append(cohort[key])
    if "ended_from" in cohort or "ended_before" in cohort:
        # Timeframe filter on when the case's last recorded event happened.
        having = (["max(ts) >= ?"] if "ended_from" in cohort else []) + (["max(ts) < ?"] if "ended_before" in cohort else [])
        clauses.append(f"case_id IN (SELECT case_id FROM ce GROUP BY case_id HAVING {' AND '.join(having)})")
        params += [cohort[k] for k in ("ended_from", "ended_before") if k in cohort]
    if "events" in cohort:
        # Number of recorded events in the case, both ends inclusive.
        e = cohort["events"]
        having = (["count(*) >= ?"] if "min" in e else []) + (["count(*) <= ?"] if "max" in e else [])
        clauses.append(f"case_id IN (SELECT case_id FROM ce GROUP BY case_id HAVING {' AND '.join(having)})")
        params += [e[k] for k in ("min", "max") if k in e]
    if "rework" in cohort:
        # Cases that repeat at least one activity (or, with false, that repeat none).
        clauses.append(f"""case_id {'IN' if cohort['rework'] else 'NOT IN'} (SELECT case_id FROM ce GROUP BY case_id
            HAVING count(*) > count(DISTINCT activity))""")
    if "path_cases" in cohort:
        # Disco-style variation filter: how many cases follow exactly this case's path, counted over all
        # cases under the case notion before the other filters apply. Both ends inclusive.
        p = cohort["path_cases"]
        cond = (["n >= ?"] if "min" in p else []) + (["n <= ?"] if "max" in p else [])
        clauses.append(f"""case_id IN (SELECT case_id FROM (SELECT case_id, count(*) OVER (PARTITION BY path) AS n
            FROM (SELECT case_id, list(activity ORDER BY ts, event_id) AS path FROM ce GROUP BY case_id))
            WHERE {' AND '.join(cond)})""")
        params += [p[k] for k in ("min", "max") if k in p]
    if "variants" in cohort:
        clauses.append(f"""case_id IN (SELECT case_id FROM ce GROUP BY case_id
            HAVING list(activity ORDER BY ts, event_id) IN (SELECT unnest(?::VARCHAR[][])))""")
        params.append(cohort["variants"])
    for part in cohort.get("all_of", ()):
        predicate, arguments = _predicate(part, log_id)
        clauses.append(f"({predicate})")
        params.extend(arguments)
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
          epoch(max(ts) - min(ts)) AS duration, count(*) > count(DISTINCT activity) AS rework,
          list(activity ORDER BY ts, event_id) AS path
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
    # Direct callers must not silently truncate/coerce a malformed display cap.
    # This is an internal-only parameter; the REST benchmark route does not expose it.
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise EventLogWorkspaceError("limit must be an integer")
    limit = max(1, min(limit, 50))

    variant_key = VARIANT_KEY_SQL.format(path="coalesce(va.path, vb.path)")

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
        # DuckDB reservoir(... ) REPEATABLE(seed) is not guaranteed to select
        # the same rows across query plans/parallel scans. A fixed hash-priority
        # sample is stable for the same case IDs, independent of scan order.
        # Only the bounded durations reach Python; no case IDs are returned.
        samples = [[r[0] for r in conn.execute(
            f"SELECT duration FROM {t}_cs ORDER BY md5(case_id), case_id LIMIT ?",
            [BOOTSTRAP_SAMPLE]).fetchall()]
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
            activities.append({"activity": activity, "cases_in_first": na, "cases_in_second": nb,
                               "share_in_first": round(na / a["cases"], 6),
                               "share_in_second": round(nb / b["cases"], 6), "difference": round(d, 6),
                               "ci95": [round(low, 6), round(high, 6)], "ci_excludes_zero": low > 0 or high < 0})
        activities.sort(key=lambda r: (not r["ci_excludes_zero"], -abs(r["difference"]), r["activity"]))
        variants = conn.execute(f"""WITH va AS (SELECT path, count(*) AS n FROM ca_cs GROUP BY 1),
              vb AS (SELECT path, count(*) AS n FROM cb_cs GROUP BY 1)
            SELECT coalesce(va.path, vb.path) AS path, coalesce(va.n, 0), coalesce(vb.n, 0), {variant_key}
            FROM va FULL JOIN vb ON va.path = vb.path
            ORDER BY greatest(coalesce(va.n, 0) / {a['cases']}, coalesce(vb.n, 0) / {b['cases']}) DESC, 1
            LIMIT {limit}""").fetchall()
        return {
            "cohorts": [{"definition": describe(first), **a}, {"definition": describe(second), **b}],
            "overlapping_cases": overlap,
            "differences_second_minus_first": differences,
            "activity_presence": activities[:limit * 2],
            "variants": [{"path": path, "variant_id": key, "cases_in_first": na, "cases_in_second": nb,
                          "share_in_first": round(na / a["cases"], 6), "share_in_second": round(nb / b["cases"], 6)}
                         for path, na, nb, key in variants],
            "intervals_assume_independent_cohorts": True,
            "note": ("cohorts share cases; independent-cohort intervals may understate or overstate uncertainty" if overlap else
                     "differences describe these cohorts; they are not explained or caused by the cohort definition"),
        }
    return workspace._query(log_id, case_type, run)
