"""What-if simulation of process designs, calibrated from an imported event log.

A discrete-event simulation of cases flowing through activities, each with a
pool of interchangeable resources served first come, first served. The
baseline uses parameters measured from the log; a scenario overrides arrivals,
routing, durations or resources. Both runs share random numbers, so the
difference reflects the changed assumptions. Every result is a model output
under stated assumptions, not a forecast or a measured outcome.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import heapq
import math
import random
import re
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import event_log_workspace as workspace
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.simulation-scenario.v1"
MAX_CASES = 20000
MAX_ACTIVITIES = 100
MAX_PATHS = 20
MAX_PATH_LENGTH = 100
MAX_RESOURCES = 1000
WARM_UP_SHARE = 0.1
ASSUMPTIONS = [
    "Cases arrive as a Poisson process at the stated rate.",
    "Each activity has a pool of interchangeable resources that serve one case at a time, first come, first served.",
    "Calibrated activity times are the median gap before each activity in the log; those gaps include any waiting, so the baseline can overstate processing time.",
    "Calibrated resources are the distinct resources observed per activity (1 when none is recorded).",
    "Calibration uses only the selected complete cases; routing, arrivals and activity parameters do not represent omitted cases.",
    "Resources work around the clock; no calendars, breaks, batching, priorities or abandonment are modeled.",
    "The first 10% of simulated cases are a warm-up and excluded from the statistics.",
]


DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DEFAULT_START = "2025-01-06T00:00:00"      # a Monday, in the calendar's own time zone
MAX_HOLIDAYS = 400
_CLOCK = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$|^24:00$")


class Calendar:
    """Working time for a scenario's resources: weekly hours in one IANA time zone, minus holidays.

    Simulation time is seconds from ``start``. Local opening hours are converted per date with the
    zone's rules, so daylight-saving changes shift them in UTC exactly as the clocks do. A working
    period covers exactly the real instants whose local wall-clock time falls inside it:

    - a wall time skipped when clocks go forward maps to the instant of the jump, so a period inside
      the skipped hour has no working time and one crossing it starts at the jump;
    - a wall time repeated when clocks go back counts from its first occurrence at a period's start
      and to its last occurrence at a period's end;
    - periods with no real length are dropped, so every interval handed out has positive length."""

    def __init__(self, zone: str, start: datetime, hours: dict[int, list[tuple[int, int]]], holidays: set[date]):
        self.zone, self.tz, self.hours, self.holidays = zone, ZoneInfo(zone), hours, holidays
        self.start = start
        self.origin = start.timestamp()
        self._days: dict[int, list[tuple[float, float]]] = {}
        self.weekly_share = sum(b - a for spans in hours.values() for a, b in spans) / (7 * 24 * 60)

    def _instant(self, day: date, minute: int, latest: bool) -> float:
        """The real instant (seconds since the origin) for a local wall time on ``day``."""
        if minute == 1440:
            day, minute = day + timedelta(days=1), 0
        wall = datetime.combine(day, time(minute // 60, minute % 60))
        early, late = (wall.replace(tzinfo=self.tz, fold=f) for f in (0, 1))
        exists = early.astimezone(timezone.utc).astimezone(self.tz).replace(tzinfo=None) == wall
        if exists:
            # Repeated wall times have two instants; take the latest one only for a period's end.
            return (late if latest else early).timestamp() - self.origin
        # Skipped wall time: the jump happens between the two readings of this wall time.
        lo, hi = sorted((early.timestamp(), late.timestamp()))
        while hi - lo > 1:
            mid = (lo + hi) / 2
            if datetime.fromtimestamp(mid, self.tz).replace(tzinfo=None) >= wall:
                hi = mid
            else:
                lo = mid
        return round(hi) - self.origin

    def _intervals(self, day: date) -> list[tuple[float, float]]:
        key = day.toordinal()
        if key not in self._days:
            spans = [] if day in self.holidays else self.hours.get(day.weekday(), [])
            out = []
            for a, b in spans:
                lo, hi = self._instant(day, a, latest=False), self._instant(day, b, latest=True)
                if hi > lo:
                    out.append((lo, hi))
            self._days[key] = out
        return self._days[key]

    def _local_day(self, t: float) -> date:
        return datetime.fromtimestamp(self.origin + t, self.tz).date()

    def _next(self, t: float) -> tuple[float, float]:
        """The working interval containing ``t`` or the next one after it."""
        day = self._local_day(t) - timedelta(days=1)
        for _ in range(400):
            for lo, hi in self._intervals(day):
                if hi > t:
                    return lo, hi
            day += timedelta(days=1)
        raise EventLogWorkspaceError("the working calendar has no working time within a year of this point")

    def open_at(self, t: float) -> float:
        lo, _ = self._next(t)
        return max(t, lo)

    def finish(self, t: float, seconds: float) -> float:
        """When ``seconds`` of work started at ``t`` ends, pausing outside working time."""
        while True:
            lo, hi = self._next(t)
            t = max(t, lo)
            if seconds <= hi - t:
                return t + seconds
            seconds -= hi - t
            t = hi

    def working_seconds(self, t0: float, t1: float) -> float:
        total, t = 0.0, t0
        while t < t1:
            lo, hi = self._next(t)
            if lo >= t1:
                break
            total += min(hi, t1) - max(lo, t)
            t = hi
        return total

    def describe(self) -> dict[str, Any]:
        def clock(m: int) -> str:
            return f"{m // 60:02d}:{m % 60:02d}"
        return {"timezone": self.zone, "start": self.start.isoformat(),
                "hours": {DAYS[d]: [[clock(a), clock(b)] for a, b in spans] for d, spans in sorted(self.hours.items())},
                "holidays": sorted(d.isoformat() for d in self.holidays),
                "working_share_of_week": round(self.weekly_share, 6)}


def parse_calendar(spec: Any) -> Calendar | None:
    if spec is None:
        return None
    if not isinstance(spec, dict) or set(spec) - {"timezone", "hours", "holidays", "start"}:
        raise EventLogWorkspaceError("a calendar has timezone, hours, holidays and start")
    zone = spec.get("timezone")
    try:
        if not isinstance(zone, str) or not 0 < len(zone) <= 64 or ".." in zone:
            raise ZoneInfoNotFoundError
        tz = ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        raise EventLogWorkspaceError("calendar timezone must be an IANA time zone such as Europe/Berlin") from None
    hours = spec.get("hours")
    if not isinstance(hours, dict) or not hours or set(hours) - set(DAYS):
        raise EventLogWorkspaceError("calendar hours map mon … sun to lists of [start, end] times")
    weekly: dict[int, list[tuple[int, int]]] = {}
    for name, spans in hours.items():
        if not isinstance(spans, list) or len(spans) > 4:
            raise EventLogWorkspaceError("each day has at most 4 working periods")
        parsed = []
        for span in spans:
            if not (isinstance(span, list) and len(span) == 2 and all(isinstance(x, str) and _CLOCK.match(x) for x in span)):
                raise EventLogWorkspaceError("working periods are [\"HH:MM\", \"HH:MM\"] in 24-hour time")
            a, b = (int(x[:2]) * 60 + int(x[3:]) for x in span)
            if b <= a:
                raise EventLogWorkspaceError("a working period must end after it starts")
            parsed.append((a, b))
        parsed.sort()
        if any(p[1] > q[0] for p, q in zip(parsed, parsed[1:])):
            raise EventLogWorkspaceError("working periods on a day must not overlap")
        if parsed:
            weekly[DAYS.index(name)] = parsed
    if not weekly:
        raise EventLogWorkspaceError("the calendar needs at least one working period")
    holidays = spec.get("holidays", [])
    if not isinstance(holidays, list) or len(holidays) > MAX_HOLIDAYS or not all(isinstance(d, str) for d in holidays):
        raise EventLogWorkspaceError(f"holidays must be a list of at most {MAX_HOLIDAYS} dates")
    try:
        closed = {date.fromisoformat(d) for d in holidays if isinstance(d, str) and len(d) == 10}
    except ValueError:
        raise EventLogWorkspaceError("holidays are dates such as 2025-12-25") from None
    if len(closed) != len(set(holidays)):
        raise EventLogWorkspaceError("holidays are dates such as 2025-12-25")
    start = spec.get("start", DEFAULT_START)
    try:
        moment = datetime.fromisoformat(start) if isinstance(start, str) and len(start) <= 40 else None
    except ValueError:
        moment = None
    if moment is None:
        raise EventLogWorkspaceError("calendar start must be a date and time such as 2025-01-06T00:00:00")
    moment = moment.replace(tzinfo=tz) if moment.tzinfo is None else moment.astimezone(tz)
    return Calendar(zone, moment, weekly, closed)


def calibrate(log_id: str, case_type: str | None = None, max_paths: int = 10) -> dict[str, Any]:
    """Measure arrival rate, routing, activity times and resources from the log."""
    max_paths = max(1, min(int(max_paths), MAX_PATHS))

    def run(conn):
        # Count first: never build or simulate a truncated long-case prefix.
        conn.execute("""CREATE TEMP TABLE simulation_case_counts AS
            SELECT case_id, count(*) AS events, min(ts) AS started FROM ce GROUP BY case_id""")
        cases, too_long = conn.execute("""SELECT count(*), count(*) FILTER (WHERE events > ?)
            FROM simulation_case_counts""", [MAX_PATH_LENGTH]).fetchone()
        names = [r[0] for r in conn.execute(f"""SELECT activity FROM ce JOIN simulation_case_counts USING(case_id)
            WHERE events <= ? GROUP BY activity ORDER BY count(*) DESC, activity LIMIT {MAX_ACTIVITIES}""",
            [MAX_PATH_LENGTH]).fetchall()]
        conn.execute("""CREATE TEMP TABLE simulation_case_paths AS
            SELECT case_id, list(activity ORDER BY ts, event_id) AS path
            FROM ce JOIN simulation_case_counts USING(case_id) WHERE events <= ?
            GROUP BY case_id HAVING bool_and(list_contains(?, activity))""", [MAX_PATH_LENGTH, names])
        conn.execute(f"""CREATE TEMP TABLE simulation_paths AS SELECT path, count(*) AS n
            FROM simulation_case_paths GROUP BY path ORDER BY n DESC, path LIMIT {max_paths}""")
        variants = conn.execute("SELECT path, n FROM simulation_paths ORDER BY n DESC, path").fetchall()
        covered = sum(n for _, n in variants)
        eligible = conn.execute("SELECT count(*) FROM simulation_case_paths").fetchone()[0]
        conn.execute("""CREATE TEMP TABLE simulation_selected AS
            SELECT case_id FROM simulation_case_paths JOIN simulation_paths USING(path)""")
        first, last = conn.execute("""SELECT min(started), max(started) FROM simulation_case_counts
            JOIN simulation_selected USING(case_id)""").fetchone()
        span_hours = (last - first).total_seconds() / 3600 if covered and last > first else 0
        arrivals = round((covered - 1) / span_hours, 6) if span_hours > 0 else None
        rows = conn.execute(f"""SELECT activity, quantile_cont(gap, 0.5), count(*), count(DISTINCT nullif(resource, ''))
            FROM (SELECT activity, resource,
                    date_diff('second', lag(ts) OVER w, ts) AS gap
                  FROM ce JOIN simulation_selected USING(case_id)
                  WINDOW w AS (PARTITION BY case_id ORDER BY ts, event_id))
            GROUP BY activity ORDER BY count(*) DESC LIMIT {MAX_ACTIVITIES}""").fetchall()
        return {
            "format": FORMAT,
            "arrivals_per_hour": arrivals,
            "paths": [{"activities": p, "weight": n / covered}
                      for p, n in variants],
            "paths_cover_cases_share": round(covered / cases, 6) if cases else 0,
            "observed_cases": cases, "calibrated_cases": covered,
            "excluded_case_reasons": {"path_length_limit": too_long,
                "activity_limit": cases - too_long - eligible, "variant_limit": eligible - covered},
            "limits": {"path_events": MAX_PATH_LENGTH, "activities": MAX_ACTIVITIES, "paths": max_paths},
            "arrival_rate_basis": "selected_complete_cases",
            "activities": {a: {"minutes": round((g or 0) / 60, 3), "resources": max(1, r),
                               "time_source": "median observed gap" if g is not None else "first activity; no gap"}
                           for a, g, _, r in rows},
            "distribution": "exponential",
        }
    return workspace._query(log_id, case_type, run)


def _number(value: Any, low: float, high: float, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise EventLogWorkspaceError(f"{what} must be a number between {low:g} and {high:g}")
    return float(value)


def parse_scenario(spec: Any, base: dict[str, Any]) -> dict[str, Any]:
    """Merge a user scenario over the calibrated baseline."""
    if not isinstance(spec, dict) or spec.get("format", FORMAT) != FORMAT:
        raise EventLogWorkspaceError(f"scenario must use format {FORMAT}")
    unknown = set(spec) - {"format", "label", "arrivals_per_hour", "paths", "activities", "distribution", "cases", "seed",
                           "calendar", "scenario_calendar"}
    if unknown:
        raise EventLogWorkspaceError("unknown scenario fields: " + ", ".join(sorted(unknown))[:200])
    arrivals = spec.get("arrivals_per_hour", base["arrivals_per_hour"])
    if arrivals is None:
        raise EventLogWorkspaceError("the log has too few cases to measure arrivals; enter arrivals_per_hour")
    arrivals = _number(arrivals, 1e-6, 1e6, "arrivals_per_hour")
    paths = spec.get("paths", base["paths"])
    if not isinstance(paths, list) or not 0 < len(paths) <= MAX_PATHS:
        raise EventLogWorkspaceError(f"scenario needs 1 to {MAX_PATHS} paths")
    routing = []
    for path in paths:
        if not isinstance(path, dict) or not isinstance(path.get("activities"), list):
            raise EventLogWorkspaceError("each path needs activities and a weight")
        steps = path["activities"]
        if not 0 < len(steps) <= MAX_PATH_LENGTH or not all(isinstance(a, str) and 0 < len(a) <= 200 for a in steps):
            raise EventLogWorkspaceError(f"each path needs 1 to {MAX_PATH_LENGTH} short activity names")
        routing.append((steps, _number(path.get("weight", 1), 0, 1e6, "path weight")))
    total = sum(w for _, w in routing)
    if total <= 0:
        raise EventLogWorkspaceError("path weights must not all be zero")
    overrides = spec.get("activities", {})
    if not isinstance(overrides, dict) or len(overrides) > MAX_ACTIVITIES:
        raise EventLogWorkspaceError("activities must map activity names to minutes and resources")
    params: dict[str, dict[str, float]] = {}
    for name in {a for steps, _ in routing for a in steps}:
        known = base["activities"].get(name, {})
        given = overrides.get(name, {})
        if not isinstance(given, dict) or set(given) - {"minutes", "resources"}:
            raise EventLogWorkspaceError("each activity takes minutes and resources")
        minutes = given.get("minutes", known.get("minutes"))
        if minutes is None:
            raise EventLogWorkspaceError("enter minutes for activities not seen in the log")
        resources = given.get("resources", known.get("resources", 1))
        resources = _number(resources, 1, MAX_RESOURCES, "resources")
        if resources != int(resources):
            raise EventLogWorkspaceError("resources must be a whole number")
        params[name] = {"minutes": _number(minutes, 0, 525600, "minutes"), "resources": int(resources)}
    if len(params) > MAX_ACTIVITIES:
        raise EventLogWorkspaceError(f"at most {MAX_ACTIVITIES} distinct activities")
    distribution = spec.get("distribution", base.get("distribution", "exponential"))
    if distribution not in ("exponential", "fixed"):
        raise EventLogWorkspaceError("distribution must be exponential or fixed")
    cases = int(_number(spec.get("cases", 2000), 10, MAX_CASES, "cases"))
    seed = int(_number(spec.get("seed", 1), 0, 2 ** 31, "seed"))
    label = spec.get("label") if isinstance(spec.get("label"), str) else ""
    calendar = parse_calendar(spec["scenario_calendar"] if "scenario_calendar" in spec else spec.get("calendar"))
    return {"label": label[:80], "arrivals_per_hour": arrivals, "paths": [(s, w / total) for s, w in routing],
            "activities": params, "distribution": distribution, "cases": cases, "seed": seed, "calendar": calendar}


def _percentile(ordered: list[float], q: float) -> float | None:
    if not ordered:
        return None
    k = (len(ordered) - 1) * q
    lo = math.floor(k)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def simulate(model: dict[str, Any]) -> dict[str, Any]:
    """Run one deterministic simulation; times are in seconds."""
    rng_arrival = random.Random(model["seed"])
    rng_route = random.Random(model["seed"] + 1)
    rng_service = random.Random(model["seed"] + 2)
    rate = model["arrivals_per_hour"] / 3600
    paths, cumulative, acc = [p for p, _ in model["paths"]], [], 0.0
    for _, weight in model["paths"]:
        acc += weight
        cumulative.append(acc)
    params, fixed = model["activities"], model["distribution"] == "fixed"
    free = {a: p["resources"] for a, p in params.items()}
    queues: dict[str, list[tuple[float, int]]] = {a: [] for a in params}
    busy = {a: 0.0 for a in params}
    waits = {a: [] for a in params}
    events: list[tuple[float, int, int, int]] = []  # (time, order, case, step) for arrivals at a step
    seq = 0
    start, finish, route, step_arrival = {}, {}, {}, {}
    clock = 0.0
    for case in range(model["cases"]):
        clock += rng_arrival.expovariate(rate)
        draw = rng_route.random()
        route[case] = paths[next((i for i, c in enumerate(cumulative) if draw <= c), len(paths) - 1)]
        start[case] = clock
        heapq.heappush(events, (clock, seq, case, 0)); seq += 1
    completions: list[tuple[float, int, int, int, str]] = []  # (time, order, case, step, activity)

    def service(activity: str) -> float:
        mean = params[activity]["minutes"] * 60
        return mean if fixed or mean == 0 else rng_service.expovariate(1 / mean)

    calendar = model.get("calendar")

    def begin(now: float, case: int, step: int, activity: str) -> None:
        nonlocal seq
        duration = service(activity)
        busy[activity] += duration
        if calendar is None:
            waits[activity].append(now - step_arrival[(case, step)])
            heapq.heappush(completions, (now + duration, seq, case, step, activity)); seq += 1
            return
        # The resource is reserved now; work starts when the calendar opens and pauses when it closes.
        started = calendar.open_at(now)
        waits[activity].append(started - step_arrival[(case, step)])
        heapq.heappush(completions, (calendar.finish(started, duration), seq, case, step, activity)); seq += 1

    while events or completions:
        if completions and (not events or completions[0][0] <= events[0][0]):
            now, _, case, step, activity = heapq.heappop(completions)
            if queues[activity]:
                _, (waiting_case, waiting_step) = heapq.heappop(queues[activity])
                begin(now, waiting_case, waiting_step, activity)
            else:
                free[activity] += 1
            if step + 1 < len(route[case]):
                heapq.heappush(events, (now, seq, case, step + 1)); seq += 1
            else:
                finish[case] = now
            continue
        now, order, case, step = heapq.heappop(events)
        activity = route[case][step]
        step_arrival[(case, step)] = now
        if free[activity]:
            free[activity] -= 1
            begin(now, case, step, activity)
        else:
            heapq.heappush(queues[activity], (order, (case, step)))
    warm = int(model["cases"] * WARM_UP_SHARE)
    measured = [c for c in range(warm, model["cases"])]
    cycle = sorted(finish[c] - start[c] for c in measured)
    horizon = max(finish.values()) - start[0] if finish else 0
    if calendar is not None and finish:
        horizon = calendar.working_seconds(start[0], max(finish.values()))
    window = (finish[measured[-1]] - start[measured[0]]) if measured else 0
    activity_rows = []
    share = calendar.weekly_share if calendar is not None else 1.0
    for activity, p in sorted(params.items()):
        ordered = sorted(waits[activity])
        utilization = busy[activity] / (p["resources"] * horizon) if horizon else 0
        offered = model["arrivals_per_hour"] / 3600 * p["minutes"] * 60 * sum(
            w * steps.count(activity) for steps, w in model["paths"]) / p["resources"] / share
        activity_rows.append({"activity": activity, "minutes": p["minutes"], "resources": p["resources"],
                              "utilization": round(utilization, 4), "offered_load": round(offered, 4),
                              "mean_wait_seconds": round(sum(ordered) / len(ordered), 3) if ordered else 0,
                              "p90_wait_seconds": _percentile(ordered, 0.9) or 0,
                              "overloaded": offered >= 1})
    return {
        "cases_simulated": model["cases"],
        "cases_measured": len(cycle),
        "cycle_time_seconds": {"p50": _percentile(cycle, 0.5), "p90": _percentile(cycle, 0.9),
                               "mean": sum(cycle) / len(cycle) if cycle else None},
        "completed_per_day": round(len(cycle) / (window / 86400), 3) if window > 0 else None,
        "activities": activity_rows,
        "unstable": any(r["overloaded"] for r in activity_rows),
        **({"calendar": calendar.describe(), "utilization_basis": "working_time"} if calendar is not None else {}),
    }


CALENDAR_ASSUMPTIONS = [
    "Resources work only during the calendar's working periods in its time zone; work in progress pauses outside them "
    "and resumes at the next opening. Daylight-saving changes follow the time zone's rules.",
    "Cases keep arriving around the clock; holidays close every resource for the whole local day.",
    "Utilization is measured against working time. Offered load spreads arrivals over the average weekly working "
    "share and ignores holidays.",
]


def _assumptions(baseline: dict[str, Any], scenario: dict[str, Any]) -> list[str]:
    if baseline.get("calendar") is None and scenario.get("calendar") is None:
        return list(ASSUMPTIONS)
    base = [a for a in ASSUMPTIONS if not a.startswith("Resources work around the clock")]
    which = ("Both runs use a working calendar." if baseline.get("calendar") is not None and scenario.get("calendar") is not None
             else "Only the run with a calendar is limited to working time; the other works around the clock.")
    return base + [which] + CALENDAR_ASSUMPTIONS + [
        "No breaks inside working periods, batching, priorities or abandonment are modeled."]


def what_if(log_id: str, scenario: Any, case_type: str | None = None) -> dict[str, Any]:
    base = calibrate(log_id, case_type)
    if not base["paths"]:
        raise EventLogWorkspaceError("no complete case paths fit the simulation limits; inspect calibration coverage")
    baseline_spec = {"cases": scenario.get("cases", 2000) if isinstance(scenario, dict) else 2000,
                     "seed": scenario.get("seed", 1) if isinstance(scenario, dict) else 1}
    if isinstance(scenario, dict) and scenario.get("calendar") is not None:
        baseline_spec["calendar"] = scenario["calendar"]      # shared by both runs; scenario_calendar overrides
    assumed_arrivals = base["arrivals_per_hour"] is None
    if assumed_arrivals and isinstance(scenario, dict) and "arrivals_per_hour" in scenario:
        baseline_spec["arrivals_per_hour"] = scenario["arrivals_per_hour"]
    baseline_model = parse_scenario(baseline_spec, base)
    scenario_model = parse_scenario(scenario, base)
    baseline, changed = simulate(baseline_model), simulate(scenario_model)
    b, s = baseline["cycle_time_seconds"]["p50"], changed["cycle_time_seconds"]["p50"]
    return {
        "log": base["log"], "case_notion": base["case_notion"],
        "calibration": {k: base[k] for k in ("arrivals_per_hour", "paths", "paths_cover_cases_share", "activities",
            "observed_cases", "calibrated_cases", "excluded_case_reasons", "limits", "arrival_rate_basis")},
        "scenario": {"label": scenario_model["label"], "arrivals_per_hour": scenario_model["arrivals_per_hour"],
                     "paths": [{"activities": p, "weight": round(w, 6)} for p, w in scenario_model["paths"]],
                     "activities": scenario_model["activities"], "distribution": scenario_model["distribution"],
                     "cases": scenario_model["cases"], "seed": scenario_model["seed"],
                     "calendar": scenario_model["calendar"].describe() if scenario_model["calendar"] else None},
        "baseline": baseline,
        "baseline_calendar": baseline_model["calendar"].describe() if baseline_model["calendar"] else None,
        "baseline_arrivals_per_hour": baseline_model["arrivals_per_hour"],
        "baseline_arrival_rate_source": "user_assumption" if assumed_arrivals else "selected_case_start_times",
        "result": changed,
        "median_cycle_time_change_seconds": (s - b) if b is not None and s is not None else None,
        "arrivals_and_routing_share_random_numbers": True,
        "assumptions": _assumptions(baseline_model, scenario_model) + (["No arrival rate could be measured; the user's explicit rate is assumed for both baseline and scenario."] if assumed_arrivals else []),
        "simulated_not_observed": True,
        "forecast_or_guarantee": False,
        "execution_authorized": False,
    }
