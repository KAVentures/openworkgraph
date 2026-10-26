"""What-if simulation of process designs, calibrated from an imported event log.

A discrete-event simulation of cases flowing through activities, each with a
pool of interchangeable resources served first come, first served. The
baseline uses parameters measured from the log; a scenario overrides arrivals
(optionally with a seasonal pattern), routing, durations or resources. Both runs share random numbers, so the
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

from . import event_log_simulation_routing as routing_model
from . import event_log_simulation_uncertainty as uncertainty
from . import event_log_workspace as workspace
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.simulation-scenario.v1"
MAX_CASES = 20000
MAX_ACTIVITIES = 100
MAX_PATHS = 20
MAX_PATH_LENGTH = 100
MAX_RESOURCES = 1000
MAX_POOLS = 20
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
            if _at(mid, self.tz).replace(tzinfo=None) >= wall:
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
        return _at(self.origin + t, self.tz).date()

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
    _check_start_year(moment, "calendar start")
    return Calendar(zone, moment, weekly, closed)


SLOT = 900                      # every real UTC offset and clock change falls on a quarter hour
MAX_PATTERN_HOURS = 10 * 8760   # simulated arrival span a seasonal pattern may cover
MAX_MEASURED_SLOTS = 400_000    # about 11 years of quarter hours
MIN_MEASURED_DAYS = 14
MONTH_DAYS = (31, 28.25, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


class ArrivalPattern:
    """Seasonal arrivals: the long-run average rate scaled by local hour of week and month.

    Weights are normalized so a week (and a year) of arrivals averages exactly the stated
    ``arrivals_per_hour``. Local time comes from one IANA time zone; the rate is constant
    within each UTC quarter hour, so arrivals are drawn exactly by inverting the
    piecewise-constant cumulative rate with one random number per case."""

    def __init__(self, zone: str, start: datetime, weekly: list[list[float]], monthly: list[float], source: str,
                 basis: dict[str, Any] | None = None):
        self.zone, self.tz, self.start, self.origin = zone, ZoneInfo(zone), start, start.timestamp()
        week_mean = sum(map(sum, weekly)) / 168
        year_mean = sum(m * d for m, d in zip(monthly, MONTH_DAYS)) / sum(MONTH_DAYS)
        self.weekly = [[w / week_mean for w in day] for day in weekly]
        self.monthly = [m / year_mean for m in monthly]
        self.source, self.basis = source, basis or {}
        self.peak = max(map(max, self.weekly)) * max(self.monthly)
        self._cache: dict[int, float] = {}

    def factor(self, slot: int) -> float:
        """Rate multiplier for the quarter hour starting at ``slot * SLOT`` seconds since the Unix epoch."""
        if slot not in self._cache:
            if len(self._cache) > 100_000:
                self._cache.clear()
            local = _at(slot * SLOT, self.tz)
            self._cache[slot] = self.weekly[local.weekday()][local.hour] * self.monthly[local.month - 1]
        return self._cache[slot]

    def local(self, t: float) -> datetime:
        return _at(self.origin + t, self.tz)

    def next_arrival(self, t: float, rate: float, need: float) -> float:
        """The time after ``t`` at which the cumulative rate grows by ``need`` (an Exp(1) draw)."""
        now = self.origin + t
        while True:
            slot = math.floor(now / SLOT)
            end = (slot + 1) * SLOT
            r = rate * self.factor(slot)
            if r > 0 and need < r * (end - now):
                # Stay inside this quarter hour even when rounding would land on its end.
                return min(now + need / r, math.nextafter(end, -math.inf)) - self.origin
            need -= r * (end - now)
            now = end

    def describe(self) -> dict[str, Any]:
        hourly = [sum(self.weekly[d][h] for d in range(7)) / 7 for h in range(24)]
        daily = [sum(day) / 24 for day in self.weekly]
        return {"timezone": self.zone, "start": self.start.isoformat(), "source": self.source,
                "hourly_factors": [round(x, 4) for x in hourly],
                "daily_factors": {DAYS[d]: round(x, 4) for d, x in enumerate(daily)},
                "monthly_factors": [round(x, 4) for x in self.monthly],
                "peak_factor": round(self.peak, 4), **({"basis": self.basis} if self.basis else {})}


EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
START_YEARS = (1971, 2200)


def _check_start_year(moment: datetime, what: str) -> None:
    if not START_YEARS[0] <= moment.year <= START_YEARS[1]:
        raise EventLogWorkspaceError(f"{what} must be between the years {START_YEARS[0]} and {START_YEARS[1]}")


def _at(seconds: float, tz: ZoneInfo) -> datetime:
    """Local time for seconds since the Unix epoch, by arithmetic rather than the platform's clock functions.

    ``datetime.fromtimestamp`` fails on Windows before 1970 and far in the future with an OSError; this
    works everywhere and raises OverflowError past year 9999 on every platform."""
    return (EPOCH + timedelta(seconds=seconds)).astimezone(tz)


def _weights(values: Any, count: int, what: str) -> list[float]:
    if not isinstance(values, list) or len(values) != count:
        raise EventLogWorkspaceError(f"{what} must be a list of {count} non-negative weights")
    out = [_number(v, 0, 1e6, what) for v in values]
    if not any(out):
        raise EventLogWorkspaceError(f"{what} must not all be zero")
    return out


def _zone(value: Any, what: str) -> ZoneInfo:
    try:
        if not isinstance(value, str) or not 0 < len(value) <= 64 or ".." in value:
            raise ZoneInfoNotFoundError
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise EventLogWorkspaceError(f"{what} must be an IANA time zone such as Europe/Berlin") from None


def parse_arrival_pattern(spec: Any, measured: dict[str, Any] | None = None,
                          calendar: Calendar | None = None) -> ArrivalPattern | None:
    """``{"timezone", "hourly": [24], "daily": [7], "monthly": [12], "start"}`` or ``{"from_log": true, "timezone"}``."""
    if spec is None:
        return None
    if not isinstance(spec, dict) or set(spec) - {"timezone", "hourly", "daily", "monthly", "start", "from_log"}:
        raise EventLogWorkspaceError("an arrival pattern has timezone, hourly, daily, monthly, start or from_log")
    zone = spec.get("timezone", calendar.zone if calendar is not None else "UTC")
    tz = _zone(zone, "arrival pattern timezone")
    start = spec.get("start")
    if start is None:
        moment = calendar.start if calendar is not None else datetime.fromisoformat(DEFAULT_START).replace(tzinfo=tz)
    else:
        try:
            moment = datetime.fromisoformat(start) if isinstance(start, str) and len(start) <= 40 else None
        except ValueError:
            moment = None
        if moment is None:
            raise EventLogWorkspaceError("arrival pattern start must be a date and time such as 2025-01-06T00:00:00")
        moment = moment.replace(tzinfo=tz) if moment.tzinfo is None else moment
        _check_start_year(moment, "arrival pattern start")
        # One simulated clock per run: with a calendar, both must start at the same instant (any offset).
        if calendar is not None and moment.timestamp() != calendar.origin:
            raise EventLogWorkspaceError("the arrival pattern start must be the same instant as the calendar start, "
                                         "because both share the run's clock; omit it to use the calendar's")
    monthly = _weights(spec["monthly"], 12, "monthly arrival weights") if "monthly" in spec else [1.0] * 12
    if spec.get("from_log", False) is not False:
        if spec["from_log"] is not True or "hourly" in spec or "daily" in spec:
            raise EventLogWorkspaceError("from_log measures the hourly and daily pattern; do not also give them")
        if measured is None or measured.get("timezone") != zone:
            raise EventLogWorkspaceError("the arrival pattern could not be measured from the log")
        if measured.get("refused"):
            raise EventLogWorkspaceError(measured["refused"])
        return ArrivalPattern(zone, moment, measured["weekly"], monthly, "measured_case_starts", measured["basis"])
    if not any(k in spec for k in ("hourly", "daily", "monthly")):
        raise EventLogWorkspaceError("an arrival pattern needs hourly, daily or monthly weights, or from_log")
    hourly = _weights(spec["hourly"], 24, "hourly arrival weights") if "hourly" in spec else [1.0] * 24
    daily = _weights(spec["daily"], 7, "daily arrival weights") if "daily" in spec else [1.0] * 7
    return ArrivalPattern(zone, moment, [[d * h for h in hourly] for d in daily], monthly, "user_weights")


def _measure_pattern(conn, zone: str) -> dict[str, Any]:
    """Case-start rate per local hour of week, divided by how often that hour occurs in the measured span."""
    tz = _zone(zone, "arrival pattern timezone")
    first, last, distinct = conn.execute("""SELECT min(slot), max(slot), count(DISTINCT slot) FROM (
        SELECT floor(epoch(started) / ?)::BIGINT AS slot FROM simulation_case_counts JOIN simulation_selected USING(case_id))""",
        [SLOT]).fetchone()
    if first is None:
        return {"timezone": zone, "refused": "no selected cases to measure an arrival pattern from"}
    if (last - first + 1) * SLOT < MIN_MEASURED_DAYS * 86400:
        return {"timezone": zone, "refused": f"measuring an arrival pattern needs case starts spanning at least {MIN_MEASURED_DAYS} days"}
    if last - first + 1 > MAX_MEASURED_SLOTS:
        return {"timezone": zone, "refused": "the case starts span too long to measure an arrival pattern; filter to a shorter period"}
    counts = [[0] * 24 for _ in range(7)]
    exposure = [[0] * 24 for _ in range(7)]
    for slot, n in conn.execute("""SELECT floor(epoch(started) / ?)::BIGINT AS slot, count(*)
            FROM simulation_case_counts JOIN simulation_selected USING(case_id) GROUP BY slot""", [SLOT]).fetchall():
        local = _at(slot * SLOT, tz)
        counts[local.weekday()][local.hour] += n
    # Exposure: every quarter hour of the whole local days spanned counts once toward its local hour of week.
    first_day = _at(first * SLOT, tz).date()
    last_day = _at(last * SLOT, tz).date() + timedelta(days=1)
    first = math.floor(datetime.combine(first_day, time(0), tz).timestamp() / SLOT)
    last = math.ceil(datetime.combine(last_day, time(0), tz).timestamp() / SLOT) - 1
    for slot in range(first, last + 1):
        local = _at(slot * SLOT, tz)
        exposure[local.weekday()][local.hour] += 1
    weekly = [[counts[d][h] / exposure[d][h] if exposure[d][h] else 0.0 for h in range(24)] for d in range(7)]
    total = sum(map(sum, counts))
    return {"timezone": zone, "weekly": weekly, "basis": {
        "case_starts": total, "measured_days": round((last - first + 1) * SLOT / 86400, 3),
        "case_start_resolution_seconds": SLOT, "monthly_measured": False,
        "hours_of_week_without_starts": sum(1 for d in range(7) for h in range(24) if counts[d][h] == 0)}}


def calibrate(log_id: str, case_type: str | None = None, max_paths: int = 10,
              pattern_zones: tuple[str, ...] = (), until: datetime | None = None,
              routing: str = "paths") -> dict[str, Any]:
    """Measure arrival rate, routing, activity times and resources from the log.

    ``pattern_zones`` also measures the local hour-of-week arrival pattern of the selected cases.
    ``until`` learns only from cases whose every event is at or before that time (a held-out validation).
    ``routing="transitions"`` selects every eligible case, not only the most frequent paths, and measures
    the step-by-step transitions between activities."""
    max_paths = max(1, min(int(max_paths), MAX_PATHS))
    if routing not in routing_model.ROUTINGS:
        raise EventLogWorkspaceError("routing must be paths or transitions")

    def run(conn):
        if until is not None:
            conn.execute("DELETE FROM ce WHERE case_id IN (SELECT case_id FROM ce GROUP BY case_id HAVING max(ts) > ?)",
                         [until])
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
        if routing == "transitions":
            conn.execute("CREATE TEMP TABLE simulation_selected AS SELECT case_id FROM simulation_case_paths")
        else:
            conn.execute("""CREATE TEMP TABLE simulation_selected AS
                SELECT case_id FROM simulation_case_paths JOIN simulation_paths USING(path)""")
        selected = conn.execute("SELECT count(*) FROM simulation_selected").fetchone()[0]
        first, last = conn.execute("""SELECT min(started), max(started) FROM simulation_case_counts
            JOIN simulation_selected USING(case_id)""").fetchone()
        span_hours = (last - first).total_seconds() / 3600 if selected and last > first else 0
        arrivals = round((selected - 1) / span_hours, 6) if span_hours > 0 else None
        rows = conn.execute(f"""SELECT activity, quantile_cont(gap, 0.5), count(gap), count(DISTINCT nullif(resource, '')), avg(gap)
            FROM (SELECT activity, resource,
                    date_diff('second', lag(ts) OVER w, ts) AS gap
                  FROM ce JOIN simulation_selected USING(case_id)
                  WINDOW w AS (PARTITION BY case_id ORDER BY ts, event_id))
            GROUP BY activity ORDER BY count(*) DESC LIMIT {MAX_ACTIVITIES}""").fetchall()
        measured = {zone: _measure_pattern(conn, zone) for zone in pattern_zones}
        return {
            **({"measured_arrival_patterns": measured} if measured else {}),
            "format": FORMAT,
            "arrivals_per_hour": arrivals,
            "paths": [{"activities": p, "weight": n / covered}
                      for p, n in variants],
            "paths_cover_cases_share": round(selected / cases, 6) if cases else 0,
            "observed_cases": cases, "calibrated_cases": selected,
            "excluded_case_reasons": {"path_length_limit": too_long,
                "activity_limit": cases - too_long - eligible, "variant_limit": eligible - selected},
            "routing": routing,
            **({"transitions": routing_model.measure(conn)} if routing == "transitions" else {}),
            "limits": {"path_events": MAX_PATH_LENGTH, "activities": MAX_ACTIVITIES, "paths": max_paths},
            "arrival_rate_basis": "selected_complete_cases",
            "activities": {a: {"minutes": round((g or 0) / 60, 3), "mean_minutes": round((m or 0) / 60, 3),
                               "observed_gaps": n, "resources": max(1, r),
                               "time_source": "median observed gap" if g is not None else "first activity; no gap"}
                           for a, g, n, r, m in rows},
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
                           "calendar", "scenario_calendar", "pools", "arrival_pattern", "scenario_arrival_pattern",
                           "activity_time_basis", "routing", "transitions"}
    if unknown:
        raise EventLogWorkspaceError("unknown scenario fields: " + ", ".join(sorted(unknown))[:200])
    arrivals = spec.get("arrivals_per_hour", base["arrivals_per_hour"])
    if arrivals is None:
        raise EventLogWorkspaceError("the log has too few cases to measure arrivals; enter arrivals_per_hour")
    arrivals = _number(arrivals, 1e-6, 1e6, "arrivals_per_hour")
    route_kind = spec.get("routing", "paths")
    if route_kind not in routing_model.ROUTINGS:
        raise EventLogWorkspaceError("routing must be paths or transitions")
    if route_kind == "paths" and "transitions" in spec:
        raise EventLogWorkspaceError("transitions apply only with routing transitions")
    if route_kind == "transitions" and "paths" in spec:
        raise EventLogWorkspaceError("with routing transitions, change transitions instead of paths")
    steps_model = routing_model.build(base, spec.get("transitions")) if route_kind == "transitions" else None
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
    # Which measured gap stands for an activity's time: the median (robust, the default) or the mean, which is the
    # right parameter for exponential times and does not shorten skewed ones.
    basis = spec.get("activity_time_basis", "median")
    if basis not in ("median", "mean"):
        raise EventLogWorkspaceError("activity_time_basis must be median or mean")
    measured_key = "minutes" if basis == "median" else "mean_minutes"
    params: dict[str, dict[str, float]] = {}
    names = routing_model.activities(steps_model) if steps_model else {a for steps, _ in routing for a in steps}
    for name in names:
        known = base["activities"].get(name, {})
        given = overrides.get(name, {})
        if not isinstance(given, dict) or set(given) - {"minutes", "resources"}:
            raise EventLogWorkspaceError("each activity takes minutes and resources")
        minutes = given.get("minutes", known.get(measured_key, known.get("minutes")))
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
    pattern_spec = spec["scenario_arrival_pattern"] if "scenario_arrival_pattern" in spec else spec.get("arrival_pattern")
    zone = _pattern_zone(pattern_spec, calendar.zone if calendar is not None else None)
    pattern = parse_arrival_pattern(pattern_spec, (base.get("measured_arrival_patterns") or {}).get(zone), calendar)
    if pattern is not None and cases / arrivals > MAX_PATTERN_HOURS:
        raise EventLogWorkspaceError("with an arrival pattern, the simulated cases must arrive within 10 years; "
                                     "raise arrivals_per_hour or lower cases")
    return {"arrival_pattern": pattern,
            "label": label[:80], "arrivals_per_hour": arrivals, "paths": [(s, w / total) for s, w in routing],
            "activities": params, "distribution": distribution, "cases": cases, "seed": seed, "calendar": calendar,
            "activity_time_basis": basis, "routing": route_kind, "transitions": steps_model,
            "pools": _pools(spec.get("pools"), params)}


def _pools(spec: Any, params: dict[str, dict[str, float]]) -> dict[str, dict[str, Any]]:
    """Shared resource pools: each pool's activities draw on one team through one first-come, first-served queue."""
    if spec is None:
        return {}
    if not isinstance(spec, dict) or not 0 < len(spec) <= MAX_POOLS:
        raise EventLogWorkspaceError(f"pools map 1 to {MAX_POOLS} pool names to resources and activities")
    pools, owner = {}, {}
    for name, pool in spec.items():
        if not isinstance(name, str) or not 0 < len(name.strip()) <= 80:
            raise EventLogWorkspaceError("pool names are 1 to 80 characters")
        if not isinstance(pool, dict) or set(pool) != {"resources", "activities"}:
            raise EventLogWorkspaceError("each pool has resources and activities")
        resources = _number(pool["resources"], 1, MAX_RESOURCES, "pool resources")
        if resources != int(resources):
            raise EventLogWorkspaceError("pool resources must be a whole number")
        members = pool["activities"]
        if (not isinstance(members, list) or not 0 < len(members) <= MAX_ACTIVITIES
                or not all(isinstance(m, str) for m in members) or len(set(members)) != len(members)):
            raise EventLogWorkspaceError("each pool lists distinct activities")
        for activity in members:
            if activity not in params:
                raise EventLogWorkspaceError(f"pool {name!r} names an activity the scenario's paths do not use")
            if activity in owner:
                raise EventLogWorkspaceError(f"activity {activity!r} is in more than one pool")
            owner[activity] = name
        pools[name] = {"resources": int(resources), "activities": list(members)}
    return pools


def _percentile(ordered: list[float], q: float) -> float | None:
    if not ordered:
        return None
    k = (len(ordered) - 1) * q
    lo = math.floor(k)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def simulate(model: dict[str, Any], samples: list[float] | None = None) -> dict[str, Any]:
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
    pools = model.get("pools") or {}
    # Each activity draws on its own resources or on its shared pool; one FCFS queue per team.
    team = {a: ("activity", a) for a in params}
    team.update({a: ("pool", name) for name, pool in pools.items() for a in pool["activities"]})
    size = {("activity", a): p["resources"] for a, p in params.items()}
    size.update({("pool", name): pool["resources"] for name, pool in pools.items()})
    free = dict(size)
    # Waiting work per team, first come first served by arrival time at the step; event order breaks ties.
    queues: dict[tuple[str, str], list[tuple[float, int, tuple[int, int]]]] = {t: [] for t in size}
    busy = {a: 0.0 for a in params}
    waits = {a: [] for a in params}
    events: list[tuple[float, int, int, int]] = []  # (time, order, case, step) for arrivals at a step
    seq = 0
    start, finish, route, step_arrival = {}, {}, {}, {}
    clock = 0.0
    pattern = model.get("arrival_pattern")
    steps_model, cut = model.get("transitions"), 0
    for case in range(model["cases"]):
        # Same random number per case with or without a pattern, so runs stay paired.
        clock = pattern.next_arrival(clock, rate, rng_arrival.expovariate(1.0)) if pattern else clock + rng_arrival.expovariate(rate)
        if steps_model:
            # Each case walks with its own random numbers, so a different route in one case never shifts others.
            route[case], was_cut = routing_model.walk(steps_model, random.Random(model["seed"] * 1_000_003 + case),
                                                      MAX_PATH_LENGTH)
            cut += was_cut
        else:
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
            if queues[team[activity]]:
                _, _, (waiting_case, waiting_step) = heapq.heappop(queues[team[activity]])
                begin(now, waiting_case, waiting_step, route[waiting_case][waiting_step])
            else:
                free[team[activity]] += 1
            if step + 1 < len(route[case]):
                heapq.heappush(events, (now, seq, case, step + 1)); seq += 1
            else:
                finish[case] = now
            continue
        now, order, case, step = heapq.heappop(events)
        activity = route[case][step]
        step_arrival[(case, step)] = now
        if free[team[activity]]:
            free[team[activity]] -= 1
            begin(now, case, step, activity)
        else:
            heapq.heappush(queues[team[activity]], (now, order, (case, step)))
    warm = int(model["cases"] * WARM_UP_SHARE)
    measured = [c for c in range(warm, model["cases"])]
    cycle = sorted(finish[c] - start[c] for c in measured)
    if samples is not None:
        samples.extend(cycle)
    horizon = max(finish.values()) - start[0] if finish else 0
    if calendar is not None and finish:
        horizon = calendar.working_seconds(start[0], max(finish.values()))
    window = (finish[measured[-1]] - start[measured[0]]) if measured else 0
    activity_rows = []
    share = calendar.weekly_share if calendar is not None else 1.0

    if steps_model:
        visits = {a: 0.0 for a in params}
        for steps in route.values():
            for a in steps:
                visits[a] += 1 / model["cases"]
    else:
        visits = {a: sum(w * steps.count(a) for steps, w in model["paths"]) for a in params}

    def work_per_second(activity: str) -> float:
        return model["arrivals_per_hour"] / 3600 * params[activity]["minutes"] * 60 * visits[activity]
    team_busy, team_work = {t: 0.0 for t in size}, {t: 0.0 for t in size}
    for activity in params:
        team_busy[team[activity]] += busy[activity]
        team_work[team[activity]] += work_per_second(activity)
    utilization_of = {t: team_busy[t] / (size[t] * horizon) if horizon else 0 for t in size}
    offered_of = {t: team_work[t] / size[t] / share for t in size}
    for activity, p in sorted(params.items()):
        ordered = sorted(waits[activity])
        t = team[activity]
        row = {"activity": activity, "minutes": p["minutes"], "resources": size[t],
               "utilization": round(utilization_of[t], 4), "offered_load": round(offered_of[t], 4),
               "mean_wait_seconds": round(sum(ordered) / len(ordered), 3) if ordered else 0,
               "p90_wait_seconds": _percentile(ordered, 0.9) or 0,
               "overloaded": offered_of[t] >= 1}
        if t[0] == "pool":
            row["pool"] = t[1]
        activity_rows.append(row)
    pool_rows = []
    for name, pool in sorted(pools.items()):
        t = ("pool", name)
        ordered = sorted(x for a in pool["activities"] for x in waits[a])
        pool_rows.append({"pool": name, "resources": pool["resources"], "activities": pool["activities"],
                          "utilization": round(utilization_of[t], 4), "offered_load": round(offered_of[t], 4),
                          "mean_wait_seconds": round(sum(ordered) / len(ordered), 3) if ordered else 0,
                          "p90_wait_seconds": _percentile(ordered, 0.9) or 0, "overloaded": offered_of[t] >= 1})
    return {
        "cases_simulated": model["cases"],
        "cases_measured": len(cycle),
        "cycle_time_seconds": {"p50": _percentile(cycle, 0.5), "p90": _percentile(cycle, 0.9),
                               "mean": sum(cycle) / len(cycle) if cycle else None},
        "completed_per_day": round(len(cycle) / (window / 86400), 3) if window > 0 else None,
        "activities": activity_rows,
        **({"pools": pool_rows} if pools else {}),
        "unstable": any(r["overloaded"] for r in activity_rows),
        **({"routes_cut_at_path_limit": cut, "visits_per_case": {a: round(v, 4) for a, v in sorted(visits.items())}}
           if steps_model else {}),
        **({"calendar": calendar.describe(), "utilization_basis": "working_time"} if calendar is not None else {}),
        **(_arrival_counts(pattern, start) if pattern is not None else {}),
    }


def _arrival_counts(pattern: ArrivalPattern, start: dict[int, float]) -> dict[str, Any]:
    """Simulated arrivals by local hour and weekday, so the applied pattern can be checked."""
    hours, days = [0] * 24, {d: 0 for d in DAYS}
    for t in start.values():
        local = pattern.local(t)
        hours[local.hour] += 1
        days[DAYS[local.weekday()]] += 1
    return {"arrival_pattern": pattern.describe(), "arrivals_by_local_hour": hours, "arrivals_by_local_weekday": days}


def _pattern_zone(spec: Any, calendar_zone: str | None) -> str | None:
    """The zone a measured pattern is read in: its own, else the run's calendar zone, else UTC."""
    if not isinstance(spec, dict) or spec.get("from_log") is not True:
        return None
    zone = spec.get("timezone", calendar_zone or "UTC")
    return zone if isinstance(zone, str) else None


ARRIVAL_PATTERN_ASSUMPTIONS = [
    "Arrivals follow a seasonal pattern: the stated rate is the long-run average, scaled by local hour of week and "
    "month in the pattern's time zone (constant within each quarter hour) as a non-homogeneous Poisson process.",
    "Offered load and the overload flag use the long-run average rate; queues can still build during peaks.",
]


def _uses_pattern(spec: Any) -> bool:
    return isinstance(spec, dict) and (spec.get("arrival_pattern") is not None or spec.get("scenario_arrival_pattern") is not None)


POOL_ASSUMPTIONS = [
    "In the scenario, the activities of each shared pool are done by one team of interchangeable resources that serve "
    "the pool's single first-come, first-served queue; a pooled activity's own resource count is not used. The "
    "measured baseline keeps one dedicated pool per activity.",
]


CALENDAR_ASSUMPTIONS = [
    "Resources work only during the calendar's working periods in its time zone; work in progress pauses outside them "
    "and resumes at the next opening. Daylight-saving changes follow the time zone's rules.",
    "Cases keep arriving around the clock; holidays close every resource for the whole local day.",
    "Utilization is measured against working time. Offered load spreads arrivals over the average weekly working "
    "share and ignores holidays.",
]


MEAN_BASIS_ASSUMPTION = ("Calibrated activity times are the mean gap before each activity in the log "
                         "(activity_time_basis: mean); those gaps include any waiting, so the baseline can overstate "
                         "processing time.")


def _time_basis(lines: list[str], model: dict[str, Any]) -> list[str]:
    if model.get("activity_time_basis") != "mean":
        return lines
    return [MEAN_BASIS_ASSUMPTION if a.startswith("Calibrated activity times are the median") else a for a in lines]


def _assumptions(baseline: dict[str, Any], scenario: dict[str, Any]) -> list[str]:
    if baseline.get("calendar") is None and scenario.get("calendar") is None:
        return list(ASSUMPTIONS)
    base = [a for a in ASSUMPTIONS if not a.startswith("Resources work around the clock")]
    which = ("Both runs use a working calendar." if baseline.get("calendar") is not None and scenario.get("calendar") is not None
             else "Only the run with a calendar is limited to working time; the other works around the clock.")
    return base + [which] + CALENDAR_ASSUMPTIONS + [
        "No breaks inside working periods, batching, priorities or abandonment are modeled."]


def what_if(log_id: str, scenario: Any, case_type: str | None = None) -> dict[str, Any]:
    uncertainty_spec = None
    if isinstance(scenario, dict) and "uncertainty" in scenario:
        uncertainty_spec = scenario["uncertainty"]
        scenario = {k: v for k, v in scenario.items() if k != "uncertainty"}
    zones: tuple[str, ...] = ()
    if isinstance(scenario, dict):
        def calendar_zone(cal):
            return cal.get("timezone") if isinstance(cal, dict) and isinstance(cal.get("timezone"), str) else None
        scenario_cal = scenario["scenario_calendar"] if "scenario_calendar" in scenario else scenario.get("calendar")
        wanted = {_pattern_zone(scenario.get("arrival_pattern"), calendar_zone(scenario.get("calendar"))),
                  _pattern_zone(scenario.get("scenario_arrival_pattern", scenario.get("arrival_pattern")),
                                calendar_zone(scenario_cal))}
        zones = tuple(sorted(z for z in wanted if z))
        for zone in zones:
            _zone(zone, "arrival pattern timezone")
    route_kind = scenario.get("routing", "paths") if isinstance(scenario, dict) else "paths"
    if route_kind not in routing_model.ROUTINGS:
        raise EventLogWorkspaceError("routing must be paths or transitions")
    base = calibrate(log_id, case_type, pattern_zones=zones, routing=route_kind)
    if not base["paths"]:
        raise EventLogWorkspaceError("no complete case paths fit the simulation limits; inspect calibration coverage")
    baseline_spec = {"cases": scenario.get("cases", 2000) if isinstance(scenario, dict) else 2000,
                     "seed": scenario.get("seed", 1) if isinstance(scenario, dict) else 1}
    if isinstance(scenario, dict) and "activity_time_basis" in scenario:
        baseline_spec["activity_time_basis"] = scenario["activity_time_basis"]   # both runs measure times alike
    if route_kind != "paths":
        baseline_spec["routing"] = route_kind      # both runs route alike; the scenario's own transitions change it
    if isinstance(scenario, dict) and scenario.get("calendar") is not None:
        baseline_spec["calendar"] = scenario["calendar"]      # shared by both runs; scenario_calendar overrides
    if isinstance(scenario, dict) and scenario.get("arrival_pattern") is not None:
        baseline_spec["arrival_pattern"] = scenario["arrival_pattern"]  # demand is shared; scenario_arrival_pattern overrides
    assumed_arrivals = base["arrivals_per_hour"] is None
    if assumed_arrivals and isinstance(scenario, dict) and "arrivals_per_hour" in scenario:
        baseline_spec["arrivals_per_hour"] = scenario["arrivals_per_hour"]
    baseline_model = parse_scenario(baseline_spec, base)
    scenario_model = parse_scenario(scenario, base)
    replications = uncertainty.parse(uncertainty_spec, scenario_model["cases"])

    def run_pair(calibration: dict[str, Any], seed: int | None = None):
        if seed is None:
            models = baseline_model, scenario_model
        else:
            models = (parse_scenario({**baseline_spec, "seed": seed}, calibration),
                      parse_scenario({**scenario, "seed": seed}, calibration))
        try:
            return simulate(models[0]), simulate(models[1]), models[0], models[1]
        except (OverflowError, ValueError) as error:
            # Dates past year 9999: the calendar or pattern clock left the representable range.
            if isinstance(error, ValueError) and "out of range" not in str(error):
                raise
            raise EventLogWorkspaceError("the simulated period runs past the supported date range; "
                                         "simulate fewer cases or raise the arrival rate") from None
    baseline, changed, _, _ = run_pair(base)
    spread = uncertainty.replicate(base, scenario_model["seed"], replications, run_pair) if replications else None
    b, s = baseline["cycle_time_seconds"]["p50"], changed["cycle_time_seconds"]["p50"]
    return {
        "log": base["log"], "case_notion": base["case_notion"],
        "calibration": {k: base[k] for k in ("arrivals_per_hour", "paths", "paths_cover_cases_share", "activities",
            "observed_cases", "calibrated_cases", "excluded_case_reasons", "limits", "arrival_rate_basis", "routing")
            if k in base},
        "scenario": {"label": scenario_model["label"], "arrivals_per_hour": scenario_model["arrivals_per_hour"],
                     "routing": scenario_model["routing"],
                     **({"transitions": routing_model.describe(scenario_model["transitions"])}
                        if scenario_model["transitions"] else
                        {"paths": [{"activities": p, "weight": round(w, 6)} for p, w in scenario_model["paths"]]}),
                     "activities": scenario_model["activities"], "distribution": scenario_model["distribution"],
                     "cases": scenario_model["cases"], "seed": scenario_model["seed"],
                     "activity_time_basis": scenario_model["activity_time_basis"],
                     "calendar": scenario_model["calendar"].describe() if scenario_model["calendar"] else None,
                     "pools": scenario_model["pools"] or None,
                     "arrival_pattern": scenario_model["arrival_pattern"].describe() if scenario_model["arrival_pattern"] else None},
        "baseline": baseline,
        "baseline_calendar": baseline_model["calendar"].describe() if baseline_model["calendar"] else None,
        "baseline_arrival_pattern": baseline_model["arrival_pattern"].describe() if baseline_model["arrival_pattern"] else None,
        "baseline_arrivals_per_hour": baseline_model["arrivals_per_hour"],
        "baseline_arrival_rate_source": "user_assumption" if assumed_arrivals else "selected_case_start_times",
        "result": changed,
        "median_cycle_time_change_seconds": (s - b) if b is not None and s is not None else None,
        "arrivals_and_routing_share_random_numbers": True,
        **({"uncertainty": spread} if spread else {}),
        "assumptions": _time_basis(_assumptions(baseline_model, scenario_model), scenario_model) + (POOL_ASSUMPTIONS if scenario_model["pools"] else []) + (ARRIVAL_PATTERN_ASSUMPTIONS if _uses_pattern(scenario) else []) + (["No arrival rate could be measured; the user's explicit rate is assumed for both baseline and scenario."] if assumed_arrivals else []) + (uncertainty.ASSUMPTIONS if spread else []) + (routing_model.ASSUMPTIONS if route_kind == "transitions" else []),
        "simulated_not_observed": True,
        "forecast_or_guarantee": False,
        "execution_authorized": False,
    }


MAX_OBSERVED_SAMPLE = 20000
VALIDATION_ASSUMPTIONS = [
    "The model is calibrated only from cases whose every event is at or before the cutoff; cases that span the "
    "cutoff are left out of both the calibration and the held-out comparison.",
    "Held-out cases are those that started after the cutoff. Cases still running when the log ends contribute "
    "their duration so far, so observed held-out times can be too short.",
    "The simulated cycle times use the calibrated parameters as they are, including their stated biases.",
    "Agreement on one held-out period is evidence about that period, not a forecast guarantee for others.",
]


def _ks(a: list[float], b: list[float]) -> float:
    """Two-sample Kolmogorov–Smirnov distance between two sorted samples."""
    i = j = 0
    gap = 0.0
    while i < len(a) and j < len(b):
        x = min(a[i], b[j])
        while i < len(a) and a[i] <= x:
            i += 1
        while j < len(b) and b[j] <= x:
            j += 1
        gap = max(gap, abs(i / len(a) - j / len(b)))
    return gap


def validate(log_id: str, spec: Any, case_type: str | None = None) -> dict[str, Any]:
    """Calibrate on the earlier cases, simulate, and compare with the cases that started after the cutoff."""
    if not isinstance(spec, dict) or set(spec) - {"holdout_share", "cases", "seed", "distribution", "calendar",
                                                   "arrival_pattern", "activity_time_basis", "routing"}:
        raise EventLogWorkspaceError("validation takes holdout_share, cases, seed, distribution, calendar, arrival_pattern, "
                                     "activity_time_basis and routing")
    route_kind = spec.get("routing", "paths")
    if route_kind not in routing_model.ROUTINGS:
        raise EventLogWorkspaceError("routing must be paths or transitions")
    share = _number(spec.get("holdout_share", 0.3), 0.05, 0.5, "holdout_share")
    if isinstance(spec.get("arrival_pattern"), dict) and spec["arrival_pattern"].get("from_log"):
        raise EventLogWorkspaceError("validation uses entered arrival weights; a pattern measured from the log would "
                                     "include the held-out period")

    def held_out(conn):
        conn.execute("""CREATE TEMP TABLE validation_cases AS
            SELECT case_id, min(ts) AS started, max(ts) AS ended FROM ce GROUP BY case_id""")
        cutoff = conn.execute("SELECT quantile_disc(started, ?) FROM validation_cases", [1 - share]).fetchone()[0]
        if cutoff is None:
            raise EventLogWorkspaceError("the log has no cases to validate against")
        counts = conn.execute("""SELECT count(*) FILTER (WHERE ended <= $c), count(*) FILTER (WHERE started <= $c AND ended > $c),
              count(*) FILTER (WHERE started > $c), min(started) FILTER (WHERE started > $c),
              max(started) FILTER (WHERE started > $c),
              quantile_cont(epoch(ended - started), 0.5) FILTER (WHERE started > $c),
              quantile_cont(epoch(ended - started), 0.9) FILTER (WHERE started > $c),
              avg(epoch(ended - started)) FILTER (WHERE started > $c), max(ended)
            FROM validation_cases""", {"c": cutoff}).fetchone()
        sample = sorted(r[0] for r in conn.execute(f"""SELECT epoch(ended - started) FROM validation_cases
            WHERE started > ? USING SAMPLE reservoir({MAX_OBSERVED_SAMPLE} ROWS) REPEATABLE (1)""", [cutoff]).fetchall())
        return {"cutoff": cutoff, "counts": counts, "sample": sample}

    observed = workspace._query(log_id, case_type, held_out)
    cutoff = observed["cutoff"]
    training, spanning, holdout, first, last, p50, p90, mean, log_end = observed["counts"]
    if holdout < 10 or training < 10:
        raise EventLogWorkspaceError("validation needs at least 10 cases finished before the cutoff and 10 started after it")
    base = calibrate(log_id, case_type, until=cutoff, routing=route_kind)
    if not base["paths"]:
        raise EventLogWorkspaceError("no cases finished before the cutoff fit the simulation limits")
    model = parse_scenario({k: spec[k] for k in ("cases", "seed", "distribution", "calendar", "arrival_pattern",
                                                 "activity_time_basis", "routing") if k in spec},
                           base)
    samples: list[float] = []
    try:
        simulated = simulate(model, samples)
    except (OverflowError, ValueError) as error:
        if isinstance(error, ValueError) and "out of range" not in str(error):
            raise
        raise EventLogWorkspaceError("the simulated period runs past the supported date range") from None
    ks = _ks(samples, observed["sample"]) if samples and observed["sample"] else None
    n, m = len(samples), len(observed["sample"])
    critical = 1.358 * math.sqrt((n + m) / (n * m)) if n and m else None
    span_hours = (last - first).total_seconds() / 3600 if last and first and last > first else 0
    sim_c = simulated["cycle_time_seconds"]

    def relative(sim: float | None, obs: float | None) -> float | None:
        return round(sim / obs - 1, 6) if sim is not None and obs else None
    stepwise = model["routing"] == "transitions"
    stopped = simulated.get("routes_cut_at_path_limit", 0)
    return {
        "log": base["log"], "case_notion": base["case_notion"],
        "cutoff": cutoff.isoformat() + "Z", "holdout_share": share,
        "cases": {"calibration": base["calibrated_cases"], "finished_before_cutoff": training,
                  "spanning_cutoff_left_out": spanning, "held_out": holdout,
                  "held_out_sampled_for_distribution": m, "held_out_sample_limit": MAX_OBSERVED_SAMPLE},
        "cycle_time_seconds": {
            "simulated": sim_c,
            "observed_held_out": {"p50": p50, "p90": p90, "mean": mean},
            "relative_error": {"p50": relative(sim_c["p50"], p50), "p90": relative(sim_c["p90"], p90),
                               "mean": relative(sim_c["mean"], mean)},
        },
        "distribution_distance": {"kolmogorov_smirnov": round(ks, 6) if ks is not None else None,
                                  "critical_value_5pct": round(critical, 6) if critical else None,
                                  "distributions_differ_at_5pct": (ks > critical) if ks is not None and critical else None},
        "arrivals_per_hour": {"calibrated": base["arrivals_per_hour"],
                              "observed_held_out": round((holdout - 1) / span_hours, 6) if span_hours > 0 else None},
        "calibration": {k: base[k] for k in ("paths", "activities", "paths_cover_cases_share", "excluded_case_reasons")},
        **({"simulated_routes": {"cases_simulated": simulated["cases_simulated"],
                                 "stopped_at_path_limit": stopped, "path_event_limit": MAX_PATH_LENGTH,
                                 "stopped_routes_in_comparison": stopped > 0}} if stepwise else {}),
        "activity_time_basis": model["activity_time_basis"], "routing": model["routing"],
        "assumptions": VALIDATION_ASSUMPTIONS + _time_basis(list(ASSUMPTIONS) if model["calendar"] is None else
                        [a for a in ASSUMPTIONS if not a.startswith("Resources work around the clock")] + CALENDAR_ASSUMPTIONS,
                                                            model)
                       + (ARRIVAL_PATTERN_ASSUMPTIONS if model["arrival_pattern"] is not None else [])
                       + (routing_model.ASSUMPTIONS if stepwise else [])
                       + ([f"{stopped} of {simulated['cases_simulated']} simulated cases reached the {MAX_PATH_LENGTH}-event "
                           "path limit and were stopped there. These stopped prefixes are compared as if they had "
                           "finished, which shortens the simulated cycle times; the fit metrics do not assess "
                           "completed routes."] if stopped else []),
        "simulated_not_observed": True, "forecast_or_guarantee": False, "execution_authorized": False,
    }
