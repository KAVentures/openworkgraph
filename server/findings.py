from __future__ import annotations

"""Findings: recurring patterns OWG noticed, with evidence, trend and outcome.

OWG analyses raw evidence locally and sends *findings* out, not raw rows:

* a periodic scan runs the existing detectors (repeated workflows, manual
  copy/paste transfers, tool waiting, friction, fragmentation, failing agent
  steps) per day over a rolling window;
* a pattern becomes a finding only with enough support (default: at least 3
  occurrences on at least 2 different days; fragmentation and waiting need 3
  days);
* each finding keeps first/last seen, occurrences, estimated time, a trend and
  example evidence IDs, plus a daily series for before/after comparisons;
* statuses: new, useful, fixed, ignored. After "fixed", OWG keeps measuring and
  records a verdict (improved / unchanged / regressed) once the after-window
  has passed; a pattern that comes back reopens as new.

Titles and summaries are built from templates plus the canonical surface and
safe action vocabulary. No free text from titles or labels is copied in.
Findings live in their own derived database (findings.db); deleting it loses
only statuses and history, never evidence.
"""

import hashlib
import json
import os
import re
import sqlite3
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

WINDOW_DAYS = 28
TREND_DAYS = 14
AFTER_DAYS = 14
SERIES_KEEP_DAYS = 120
STALE_AFTER_DAYS = 60
MIN_SCAN_INTERVAL_SECONDS = 30 * 60
STATUSES = ("new", "useful", "fixed", "ignored")
AI_SETTABLE_STATUSES = ("useful", "fixed", "ignored")

# kind -> (min occurrences, min distinct days)
DEFAULT_RULES: dict[str, tuple[int, int]] = {
    "repeated_workflow": (3, 2),
    "manual_transfer": (3, 2),
    "tool_waiting": (3, 3),
    "friction": (3, 2),
    "fragmentation": (3, 3),
    "agent_failure": (3, 2),
}

_LOCK = threading.RLock()
_SCAN_STATE: dict[str, Any] = {"last": 0.0}


# --- storage -------------------------------------------------------------------------

def _data_dir() -> Path:
    from .presentation import _data_dir as data_dir
    return data_dir()


def _db_path() -> Path:
    return _data_dir() / "findings.db"


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS findings (
            finding_id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            subject_key TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            occurrences INTEGER NOT NULL,
            active_days INTEGER NOT NULL,
            time_spent_seconds REAL,
            trend_json TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            series_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'new',
            status_note TEXT NOT NULL DEFAULT '',
            status_changed_at TEXT NOT NULL,
            status_changed_by TEXT NOT NULL DEFAULT '',
            surfaced_at TEXT NOT NULL,
            fixed_at TEXT,
            baseline_per_week REAL,
            after_per_week REAL,
            verdict TEXT,
            ignored_occurrences INTEGER,
            updated_at TEXT NOT NULL,
            detector_version INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS finding_observations (
            finding_id TEXT NOT NULL,
            scan_at TEXT NOT NULL,
            occurrences INTEGER NOT NULL,
            per_week REAL NOT NULL,
            time_spent_seconds REAL
        );
        CREATE TABLE IF NOT EXISTS finding_seen (
            finding_id TEXT NOT NULL,
            client TEXT NOT NULL,
            seen_at TEXT NOT NULL,
            PRIMARY KEY (finding_id, client)
        );
        CREATE INDEX IF NOT EXISTS idx_finding_obs ON finding_observations(finding_id, scan_at);
        """
    )
    return conn


def _now_iso(now: datetime) -> str:
    return now.astimezone(timezone.utc).isoformat()


# --- rules / config ------------------------------------------------------------------------

def rules() -> dict[str, tuple[int, int]]:
    """Thresholds, overridable in config.json: findings.min_occurrences / min_days."""
    from .presentation import _load_config

    section = _load_config().get("findings")
    section = section if isinstance(section, dict) else {}
    out = dict(DEFAULT_RULES)
    try:
        occ = int(section["min_occurrences"]) if "min_occurrences" in section else None
        days = int(section["min_days"]) if "min_days" in section else None
    except (TypeError, ValueError):
        occ = days = None
    for kind, (min_occ, min_days) in out.items():
        if occ is not None:
            min_occ = max(2, occ)
        if days is not None:
            # Noisy signals never drop below their own default day requirement.
            min_days = max(1, days, DEFAULT_RULES[kind][1] if kind in {"tool_waiting", "fragmentation"} else 1)
        out[kind] = (min_occ, min_days)
    return out


def after_days() -> int:
    from .presentation import _load_config

    section = _load_config().get("findings")
    try:
        return max(7, int((section or {}).get("after_days", AFTER_DAYS)))
    except (TypeError, ValueError, AttributeError):
        return AFTER_DAYS


# --- detection ---------------------------------------------------------------------------------

@dataclass
class DayStat:
    count: int = 0
    seconds: float = 0.0
    event_ids: list[str] = field(default_factory=list)


@dataclass
class Subject:
    kind: str
    key: str
    title: str
    describe: Callable[[int, int], str]  # (occurrences, days) -> short phrase
    days: dict[str, DayStat] = field(default_factory=dict)
    time_known: bool = True

    def add(self, day: str, count: int, seconds: float | None, event_ids: list[Any]) -> None:
        stat = self.days.setdefault(day, DayStat())
        stat.count += int(count)
        if seconds is None:
            self.time_known = False
        else:
            stat.seconds += float(seconds)
        for eid in event_ids:
            if eid and len(stat.event_ids) < 10 and str(eid) not in stat.event_ids:
                stat.event_ids.append(str(eid))


def _surface_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text.casefold() == "unknown":
        return "an app"
    return text


def _step_surface(token: str) -> str:
    if not token.startswith("surface:"):
        return ""
    name = token.split(":", 1)[1]
    if re.fullmatch(r"[0-9a-f]{8,}", name):
        return "another app"
    return " ".join(part.capitalize() for part in name.replace("-", "_").split("_") if part)


def _local_day(value: Any) -> str | None:
    try:
        dt = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().date().isoformat()


def _by_day(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        day = _local_day(row.get("observed_at"))
        if day:
            out[day].append(row)
    return out


def _short_hash(value: Any) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:12]


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def detect(since: str) -> dict[str, Subject]:
    """Run every detector per day over evidence since ``since``."""
    from . import work_profile as wp
    from . import work_profile_signals as signals

    subjects: dict[str, Subject] = {}

    def subject(kind: str, key: str, title: str, describe: Callable[[int, int], str]) -> Subject:
        full = f"{kind}:{key}"
        if full not in subjects:
            subjects[full] = Subject(kind=kind, key=full, title=title, describe=describe)
        return subjects[full]

    context = wp._context_rows(since)
    focus = wp._focus_rows(since)
    context_days = _by_day(context)

    for day, rows in context_days.items():
        # Manual copy/paste between different apps.
        for item in wp._transfer_patterns(rows):
            if not item.get("cross_surface"):
                continue
            src, dst = _surface_label(item["source_surface"]), _surface_label(item["destination_surface"])
            subject(
                "manual_transfer", f"{src}->{dst}".casefold(),
                f"You copy from {src} into {dst} by hand",
                lambda n, d: f"{_plural(n, 'manual copy-paste')} on {_plural(d, 'day')}",
            ).add(day, item["count"], None, item.get("example_event_ids") or [])

        # Waiting on slow pages (median load >= 3 s).
        for item in signals._tool_waiting(rows):
            if float(item.get("median_load_ms") or 0) < 3000:
                continue
            surface = _surface_label(item["surface"])
            subject(
                "tool_waiting", surface.casefold(),
                f"{surface} is often slow to load",
                lambda n, d: f"{_plural(n, 'slow page load')} on {_plural(d, 'day')}",
            ).add(day, item["navigation_count"], float(item.get("sum_observed_load_ms") or 0) / 1000.0, [])

        # Friction: bursts of repeated clicks, sign-in detours, hunting for a page.
        for item in signals._rapid_click_candidates(rows):
            surface = _surface_label(item["surface"])
            subject(
                "friction", f"rapid_clicks:{surface.casefold()}:{_short_hash(item.get('target_label'))}",
                f"Repeated rapid clicks on the same control in {surface}",
                lambda n, d: f"{_plural(n, 'burst')} of repeated clicks on {_plural(d, 'day')}",
            ).add(day, item["burst_count"], None, item.get("example_event_ids") or [])
        for item in signals._auth_flow_candidates(rows):
            surface = _surface_label(item["surface"])
            subject(
                "friction", f"sign_in:{surface.casefold()}",
                f"Frequent sign-in detours in {surface}",
                lambda n, d: f"{_plural(n, 'sign-in flow')} on {_plural(d, 'day')}",
            ).add(day, 1, float(item.get("duration_seconds") or 0), item.get("example_event_ids") or [])
        for item in wp._hunting_candidates(rows):
            surface = _surface_label(item["surface"])
            subject(
                "friction", f"revisits:{surface.casefold()}:{_short_hash(item.get('resource_locator'))}",
                f"You keep returning to the same page in {surface}",
                lambda n, d: f"{_plural(n, 'return visit')} on {_plural(d, 'day')}",
            ).add(day, item["visit_count"], None, item.get("example_event_ids") or [])

    # Fragmentation: days well above the user's own typical switch rate.
    focus_days = _by_day(focus)
    per_day = {day: wp._fragmentation(rows) for day, rows in focus_days.items()}
    rates = sorted(float(v.get("surface_switches_per_foreground_hour") or 0) for v in per_day.values() if v.get("foreground_seconds"))
    if len(rates) >= 3:
        median = rates[len(rates) // 2]
        for day, frag in per_day.items():
            rate = float(frag.get("surface_switches_per_foreground_hour") or 0)
            if rate >= max(20.0, 1.5 * median) and float(frag.get("foreground_seconds") or 0) >= 1800:
                subject(
                    "fragmentation", "daily",
                    "Some days are unusually fragmented",
                    lambda n, d: f"{_plural(d, 'day')} with far more app switching than your usual",
                ).add(day, 1, None, [])

    # Repeated routines and failing agent steps (procedural memory).
    try:
        from .procedural_memory import _event_ref, derive_executions, load_recent_evidence

        import bisect

        raw = load_recent_evidence(limit=100_000, since=since)
        ref_to_id = {_event_ref(e.get("event_id")): e.get("event_id") for e in raw if e.get("event_id")}
        # Work events (focus / browser / screen) by time, for readable routes and evidence.
        work = sorted(
            (e for e in raw if e.get("event_type") == "focus_span"
             or str(e.get("event_type") or "").startswith(("browser_", "screen_"))),
            key=lambda e: str(e.get("observed_at") or ""),
        )
        work_times = [str(e.get("observed_at") or "") for e in work]

        def window_events(start: Any, end: Any) -> list[dict[str, Any]]:
            lo = bisect.bisect_left(work_times, str(start or ""))
            hi = bisect.bisect_right(work_times, str(end or ""))
            return work[lo:hi]

        for execution in derive_executions(raw):
            day = _local_day(execution.get("started_at"))
            if not day:
                continue
            steps = [s for s in execution.get("steps") or [] if s]
            evidence = [ref_to_id.get(ref) for ref in execution.get("evidence_refs") or []]
            if execution.get("actor_kind") == "human" and len(steps) >= 3:
                in_window = window_events(execution.get("started_at"), execution.get("ended_at"))
                surfaces: list[str] = []
                for event in in_window:
                    label = _surface_label(wp._canonical_surface(event.get("app")))
                    if label and (not surfaces or surfaces[-1] != label):
                        surfaces.append(label)
                if not surfaces:
                    for step in steps:
                        label = _step_surface(step)
                        if label and (not surfaces or surfaces[-1] != label):
                            surfaces.append(label)
                evidence = [x for x in evidence if x] or [e.get("event_id") for e in in_window[:10]]
                route = " → ".join(surfaces[:4]) or "the same apps"
                subject(
                    "repeated_workflow", str(execution.get("family_key")),
                    f"A {len(steps)}-step routine you repeat: {route}",
                    lambda n, d: f"repeated {_plural(n, 'time')} on {_plural(d, 'day')}",
                ).add(day, 1, float(execution.get("duration_seconds") or 0), evidence)
            elif execution.get("actor_kind") == "agent" and execution.get("explicit_failure"):
                failing = steps[-1] if steps else "unknown"
                step_kind = failing.split(":", 1)[0] or "a step"
                subject(
                    "agent_failure", f"{execution.get('family_key')}:{_short_hash(failing)}",
                    f"An agent run keeps failing at the same {step_kind.replace('_', ' ')} step",
                    lambda n, d: f"{_plural(n, 'failed run')} on {_plural(d, 'day')}",
                ).add(day, 1, float(execution.get("duration_seconds") or 0), evidence)
    except Exception:
        # Procedural memory is optional input; the other detectors still run.
        pass

    return subjects


# --- aggregation -------------------------------------------------------------------------

def _finding_id(key: str) -> str:
    return "fnd_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _rate(series: dict[str, int], start: date, end: date) -> float:
    """Occurrences per week in [start, end)."""
    days = max(1, (end - start).days)
    total = sum(v for k, v in series.items() if start.isoformat() <= k < end.isoformat())
    return round(total * 7.0 / days, 2)


def _trend(series: dict[str, int], today: date) -> dict[str, Any]:
    current = _rate(series, today - timedelta(days=TREND_DAYS - 1), today + timedelta(days=1))
    previous = _rate(series, today - timedelta(days=2 * TREND_DAYS - 1), today - timedelta(days=TREND_DAYS - 1))
    if previous == 0 and current == 0:
        direction = "flat"
    elif previous == 0:
        direction = "new"
    elif current >= previous * 1.2:
        direction = "up"
    elif current <= previous * 0.8:
        direction = "down"
    else:
        direction = "flat"
    change = round((current - previous) / previous * 100) if previous else None
    return {"direction": direction, "current_per_week": current, "previous_per_week": previous, "change_percent": change}


def _fmt_duration(seconds: float | None) -> str:
    if not seconds:
        return ""
    minutes = seconds / 60.0
    if minutes < 90:
        return f"~{max(1, round(minutes))} min"
    return f"~{minutes / 60.0:.1f} h"


def _summary(subject: Subject, occurrences: int, days: int, seconds: float | None, trend: dict[str, Any]) -> str:
    parts = [subject.describe(occurrences, days).capitalize()]
    if seconds:
        parts.append(f"{_fmt_duration(seconds)} in total (estimate)")
    if trend["direction"] in {"up", "down"} and trend["change_percent"] is not None:
        sign = "+" if trend["change_percent"] > 0 else ""
        parts.append(f"{'growing' if trend['direction'] == 'up' else 'shrinking'} ({sign}{trend['change_percent']}% vs the previous {TREND_DAYS} days)")
    return ", ".join(parts) + "."


def scan(*, now: datetime | None = None, force: bool = False) -> dict[str, Any]:
    """Detect, then create/update findings. Idempotent; throttled unless forced."""
    import time

    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    with _LOCK:
        if not force and time.monotonic() - _SCAN_STATE["last"] < MIN_SCAN_INTERVAL_SECONDS:
            return {"status": "skipped", "reason": "scanned recently"}
        _SCAN_STATE["last"] = time.monotonic()
        today = now.astimezone().date()
        since = (now - timedelta(days=WINDOW_DAYS)).isoformat()
        subjects = detect(since)
        thresholds = rules()
        stamp = _now_iso(now)
        created = updated = 0
        with _connect() as conn:
            for subject in subjects.values():
                series_now = {d: s.count for d, s in subject.days.items() if s.count}
                fid = _finding_id(subject.key)
                row = conn.execute("SELECT * FROM findings WHERE finding_id = ?", (fid,)).fetchone()
                series = json.loads(row["series_json"]) if row else {}
                # The window replaces its own days; older days are kept for before/after.
                window_start = (today - timedelta(days=WINDOW_DAYS)).isoformat()
                series = {d: c for d, c in series.items() if d < window_start}
                series.update(series_now)
                keep_from = (today - timedelta(days=SERIES_KEEP_DAYS)).isoformat()
                series = {d: c for d, c in sorted(series.items()) if d >= keep_from}

                occurrences = sum(series_now.values())
                active_days = len(series_now)
                min_occ, min_days = thresholds.get(subject.kind, (3, 2))
                if row is None and (occurrences < min_occ or active_days < min_days):
                    continue
                seconds = sum(s.seconds for s in subject.days.values()) if subject.time_known else None
                trend = _trend(series, today)
                evidence: list[str] = []
                for day in sorted(subject.days, reverse=True):
                    for eid in subject.days[day].event_ids:
                        if eid not in evidence and len(evidence) < 10:
                            evidence.append(eid)
                title = subject.title
                summary = _summary(subject, occurrences, active_days, seconds, trend)
                first_seen = min(series) if series else today.isoformat()
                last_seen = max(series_now) if series_now else (row["last_seen"] if row else today.isoformat())

                if row is None:
                    conn.execute(
                        """INSERT INTO findings (finding_id, kind, subject_key, title, summary, first_seen, last_seen,
                           occurrences, active_days, time_spent_seconds, trend_json, evidence_json, series_json,
                           status, status_changed_at, surfaced_at, updated_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?, 'new', ?, ?, ?)""",
                        (fid, subject.kind, subject.key, title, summary, first_seen, last_seen, occurrences,
                         active_days, seconds, json.dumps(trend), json.dumps(evidence), json.dumps(series),
                         stamp, stamp, stamp),
                    )
                    created += 1
                else:
                    conn.execute(
                        """UPDATE findings SET title=?, summary=?, first_seen=?, last_seen=?, occurrences=?,
                           active_days=?, time_spent_seconds=?, trend_json=?, evidence_json=?, series_json=?,
                           updated_at=? WHERE finding_id=?""",
                        (title, summary, min(first_seen, row["first_seen"]), last_seen, occurrences, active_days,
                         seconds, json.dumps(trend), json.dumps(evidence), json.dumps(series), stamp, fid),
                    )
                    _apply_outcome(conn, fid, series, today, stamp)
                    updated += 1
                conn.execute(
                    "INSERT INTO finding_observations VALUES (?,?,?,?,?)",
                    (fid, stamp, occurrences, trend["current_per_week"], seconds),
                )
            # Findings whose subject vanished from the window still get outcome checks.
            for row in conn.execute("SELECT finding_id, series_json FROM findings WHERE status IN ('fixed','ignored')").fetchall():
                if not any(_finding_id(s.key) == row["finding_id"] for s in subjects.values()):
                    series = json.loads(row["series_json"])
                    window_start = (today - timedelta(days=WINDOW_DAYS)).isoformat()
                    series = {d: c for d, c in series.items() if d < window_start}
                    conn.execute("UPDATE findings SET series_json=? WHERE finding_id=?", (json.dumps(series), row["finding_id"]))
                    _apply_outcome(conn, row["finding_id"], series, today, stamp)
        return {"status": "ok", "created": created, "updated": updated, "scanned_at": stamp}


def _apply_outcome(conn: sqlite3.Connection, fid: str, series: dict[str, int], today: date, stamp: str) -> None:
    row = conn.execute("SELECT * FROM findings WHERE finding_id = ?", (fid,)).fetchone()
    if row is None:
        return
    if row["status"] == "ignored":
        # Reopen only if it has grown substantially since it was ignored.
        if row["ignored_occurrences"] and row["occurrences"] >= 2 * int(row["ignored_occurrences"]):
            conn.execute(
                "UPDATE findings SET status='new', status_note=?, status_changed_at=?, status_changed_by='owg', surfaced_at=? WHERE finding_id=?",
                ("Reopened: it has at least doubled since you ignored it.", stamp, stamp, fid),
            )
        return
    if row["status"] != "fixed" or not row["fixed_at"]:
        return
    fixed_day = datetime.fromisoformat(row["fixed_at"]).astimezone().date()
    window = after_days()
    baseline = row["baseline_per_week"] or 0.0
    if row["verdict"] is None and today >= fixed_day + timedelta(days=window):
        after = _rate(series, fixed_day + timedelta(days=1), fixed_day + timedelta(days=window + 1))
        if baseline and after <= baseline * 0.7:
            verdict, status, note = "improved", "fixed", ""
        elif baseline and after >= baseline * 1.1:
            verdict, status, note = "regressed", "new", "Still happening after you marked it fixed, and more often."
        else:
            verdict, status, note = "unchanged", "new", "Still happening at about the same rate after you marked it fixed."
        conn.execute(
            "UPDATE findings SET after_per_week=?, verdict=?, status=?, status_note=?, status_changed_at=?, "
            "status_changed_by=CASE WHEN ?='fixed' THEN status_changed_by ELSE 'owg' END, "
            "surfaced_at=CASE WHEN ?='new' THEN ? ELSE surfaced_at END WHERE finding_id=?",
            (after, verdict, status, note, stamp, status, status, stamp, fid),
        )
    elif row["verdict"] == "improved" and baseline:
        current = _rate(series, today - timedelta(days=TREND_DAYS - 1), today + timedelta(days=1))
        if current >= baseline * 0.8:
            conn.execute(
                "UPDATE findings SET status='new', verdict='regressed', status_note=?, status_changed_at=?, "
                "status_changed_by='owg', surfaced_at=? WHERE finding_id=?",
                ("It came back after it had improved.", stamp, stamp, fid),
            )


# --- reading / feedback --------------------------------------------------------------------

def _public(row: sqlite3.Row, *, include_series: bool = False) -> dict[str, Any]:
    out = {
        "finding_id": row["finding_id"],
        "kind": row["kind"],
        "title": row["title"],
        "summary": row["summary"],
        "first_seen": row["first_seen"],
        "last_seen": row["last_seen"],
        "occurrences": row["occurrences"],
        "active_days": row["active_days"],
        "time_spent_seconds": row["time_spent_seconds"],
        "time_spent_is_estimate": True,
        "trend": json.loads(row["trend_json"]),
        "evidence_event_ids": json.loads(row["evidence_json"]),
        "status": row["status"],
        "status_note": row["status_note"],
        "status_changed_at": row["status_changed_at"],
        "stale": row["last_seen"] < (date.today() - timedelta(days=STALE_AFTER_DAYS)).isoformat(),
    }
    if row["fixed_at"]:
        out["outcome"] = {
            "fixed_at": row["fixed_at"],
            "baseline_per_week": row["baseline_per_week"],
            "after_per_week": row["after_per_week"],
            "verdict": row["verdict"] or "measuring",
            "after_window_days": after_days(),
        }
        if row["verdict"] and row["baseline_per_week"] is not None and row["after_per_week"] is not None:
            b, a = row["baseline_per_week"], row["after_per_week"]
            pct = round((a - b) / b * 100) if b else 0
            out["outcome"]["statement"] = f"{b:g} per week before, {a:g} per week after ({'+' if pct > 0 else ''}{pct}%)."
    if include_series:
        out["daily_series"] = json.loads(row["series_json"])
    return out


_ORDER = "CASE status WHEN 'new' THEN 0 WHEN 'useful' THEN 1 WHEN 'fixed' THEN 2 ELSE 3 END, occurrences DESC"


def list_findings(*, status: str = "", include_stale: bool = False, limit: int = 100) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 500))
    with _connect() as conn:
        if status:
            rows = conn.execute(f"SELECT * FROM findings WHERE status=? ORDER BY {_ORDER}", (status,)).fetchall()
        else:
            rows = conn.execute(f"SELECT * FROM findings ORDER BY {_ORDER}").fetchall()
    items = [_public(r) for r in rows]
    if not include_stale:
        items = [i for i in items if not i["stale"]]
    return items[:limit]


def get_finding(finding_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM findings WHERE finding_id=?", (finding_id,)).fetchone()
        if row is None:
            return None
        history = conn.execute(
            "SELECT scan_at, occurrences, per_week FROM finding_observations WHERE finding_id=? ORDER BY scan_at DESC LIMIT 60",
            (finding_id,),
        ).fetchall()
    out = _public(row, include_series=True)
    out["history"] = [dict(h) for h in reversed(history)]
    return out


def new_for_client(client: str, *, limit: int = 10, mark_seen: bool = True, now: datetime | None = None) -> list[dict[str, Any]]:
    """New findings this client has not seen since they (re)surfaced."""
    client = (client or "default").strip()[:40] or "default"
    stamp = _now_iso(now or datetime.now(timezone.utc))
    with _connect() as conn:
        rows = conn.execute(
            f"""SELECT f.* FROM findings f LEFT JOIN finding_seen s
                ON s.finding_id = f.finding_id AND s.client = ?
                WHERE f.status = 'new' AND (s.seen_at IS NULL OR s.seen_at < f.surfaced_at)
                ORDER BY {_ORDER}""",
            (client,),
        ).fetchall()
        items = [_public(r) for r in rows if not _public(r)["stale"]][: max(1, min(int(limit), 50))]
        if mark_seen:
            conn.executemany(
                "INSERT INTO finding_seen VALUES (?,?,?) ON CONFLICT(finding_id, client) DO UPDATE SET seen_at=excluded.seen_at",
                [(item["finding_id"], client, stamp) for item in items],
            )
    return items


def digest(*, period: str = "week") -> dict[str, Any]:
    days = 7 if period == "week" else 30 if period == "month" else 1
    since = (date.today() - timedelta(days=days - 1)).isoformat()
    items = list_findings(limit=500)
    active = [i for i in items if i["last_seen"] >= since and i["status"] in {"new", "useful"}]
    fixed = [i for i in items if i["status"] == "fixed" or (i.get("outcome") or {}).get("verdict")]
    saved = 0.0
    for item in fixed:
        outcome = item.get("outcome") or {}
        if outcome.get("verdict") == "improved" and item.get("time_spent_seconds") and item.get("occurrences"):
            per = float(item["time_spent_seconds"]) / max(1, int(item["occurrences"]))
            saved += max(0.0, (outcome["baseline_per_week"] or 0) - (outcome["after_per_week"] or 0)) * per
    return {
        "period": period,
        "since": since,
        "active_findings": active[:20],
        "fixes": [
            {"finding_id": i["finding_id"], "title": i["title"], "outcome": i.get("outcome")}
            for i in fixed[:20]
        ],
        "estimated_time_saved_per_week_seconds": round(saved, 1),
        "estimates_are_labelled": True,
    }


def set_status(finding_id: str, status: str, *, note: str = "", actor: str = "dashboard", now: datetime | None = None) -> dict[str, Any]:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    stamp_dt = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamp = _now_iso(stamp_dt)
    note = " ".join(str(note or "").split())[:280]
    with _connect() as conn:
        row = conn.execute("SELECT * FROM findings WHERE finding_id=?", (finding_id,)).fetchone()
        if row is None:
            raise KeyError(finding_id)
        fields: dict[str, Any] = {
            "status": status, "status_note": note, "status_changed_at": stamp,
            "status_changed_by": str(actor or "")[:40],
        }
        if status == "fixed":
            series = json.loads(row["series_json"])
            fixed_day = stamp_dt.astimezone().date()
            fields.update(
                fixed_at=stamp,
                baseline_per_week=_rate(series, fixed_day - timedelta(days=TREND_DAYS), fixed_day + timedelta(days=1)),
                after_per_week=None, verdict=None,
            )
        elif status == "ignored":
            fields["ignored_occurrences"] = row["occurrences"]
        elif status == "new":
            fields["surfaced_at"] = stamp
        assignments = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE findings SET {assignments} WHERE finding_id=?", (*fields.values(), finding_id))
    return get_finding(finding_id) or {}


def evidence_rows(finding_id: str, *, limit: int = 20) -> dict[str, Any]:
    """Canonical evidence rows for a finding, shaped like get_workflow_trace rows."""
    from .db import connect
    from .mcp_trace import rich_evidence_row

    finding = get_finding(finding_id)
    if finding is None:
        raise KeyError(finding_id)
    ids = [str(x) for x in finding["evidence_event_ids"]][: max(1, min(int(limit), 50))]
    rows: list[dict[str, Any]] = []
    if ids:
        marks = ",".join("?" for _ in ids)
        with connect() as conn:
            found = conn.execute(
                f"SELECT * FROM events WHERE event_id IN ({marks}) ORDER BY observed_at ASC, id ASC", tuple(ids)
            ).fetchall()
        rows = [rich_evidence_row(dict(r), include_identity=False) for r in found]
    return {
        "finding_id": finding_id,
        "title": finding["title"],
        "rows": rows,
        "returned": len(rows),
        "data_layer": "privacy_hardened_raw_rich_evidence",
        "more_evidence": "Use get_workflow_trace with since/until around these rows for surrounding context.",
    }


# --- background scheduling -----------------------------------------------------------------

_STOP = threading.Event()
_THREAD: dict[str, threading.Thread | None] = {"t": None}
SCAN_EVERY_SECONDS = 6 * 3600
FIRST_SCAN_DELAY_SECONDS = 90


def start_background_scanner() -> None:
    if os.getenv("OWG_FINDINGS_DISABLED", "").strip() == "1":
        return
    if _THREAD["t"] and _THREAD["t"].is_alive():
        return
    _STOP.clear()

    def loop() -> None:
        if _STOP.wait(FIRST_SCAN_DELAY_SECONDS):
            return
        while not _STOP.is_set():
            try:
                scan(force=True)
            except Exception:
                pass
            if _STOP.wait(SCAN_EVERY_SECONDS):
                return

    thread = threading.Thread(target=loop, name="owg-findings-scanner", daemon=True)
    _THREAD["t"] = thread
    thread.start()


def stop_background_scanner() -> None:
    _STOP.set()
