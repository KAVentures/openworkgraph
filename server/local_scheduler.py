"""Opt-in local schedules for repeatable OWG jobs (#431 retained Jira, R10 signals).

Nothing is scheduled by default and nothing runs until the owner creates a
schedule with an explicit confirmation. Supported jobs:

- ``retained_jira_profile``: run an existing retained Jira profile (its own
  read/retention consent still applies, and its per-read site and account checks
  are unchanged);
- ``event_log_signals``: re-evaluate the saved signal rules of an event log so
  the signal inbox reflects the current data;
- ``local_backup``: at most daily, create a verified unencrypted snapshot
  outside the data folder, excluding credentials; rotate owned scheduled files
  only after success, with separate user acknowledgment.

Each schedule has an interval of at least 15 minutes. One job runs at a time.
A failure backs off exponentially (up to 24 hours, never earlier than a
provider's own retry-after), and five failures in a row pause the schedule until
the owner resumes it. Run history keeps outcomes and fixed error codes only, never
source content. Pausing recording pauses scheduled source reads too. The
scheduler is local: no cloud scheduler, no delivery to third parties and no
business writes.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .db import DATA_DIR

FORMAT = "openworkgraph.local-schedules.v1"
KINDS = ("retained_jira_profile", "event_log_signals", "local_backup")
MIN_BACKUP_INTERVAL_MINUTES = 24 * 60
MIN_INTERVAL_MINUTES = 15
MAX_INTERVAL_MINUTES = 7 * 24 * 60
MAX_SCHEDULES = 20
MAX_HISTORY = 50
MAX_FAILURES = 5
MAX_BACKOFF_SECONDS = 24 * 3600
TICK_SECONDS = 30
_TARGET = re.compile(r"^[A-Za-z0-9_:-]{1,80}$")
_LOCK = threading.RLock()
_RUN_LOCK = threading.Lock()
_STOP = threading.Event()
_THREAD: threading.Thread | None = None


class ScheduleError(ValueError):
    """Safe error; never echoes job output."""


def _path() -> Path:
    return DATA_DIR / "local_schedules.json"


def _iso(epoch: float | None) -> str | None:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat() if epoch else None


def _read() -> dict[str, Any]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": 1, "schedules": {}}
    if not isinstance(data, dict) or not isinstance(data.get("schedules"), dict):
        return {"version": 1, "schedules": {}}
    return data


def _write(data: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".owg-schedules-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(data, out, separators=(",", ":"))
        if os.name == "posix":
            os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass


def _public(item: dict[str, Any]) -> dict[str, Any]:
    return {**{k: v for k, v in item.items() if k not in {"next_run_epoch", "last_run_epoch"}},
            "next_run_at": _iso(item.get("next_run_epoch")) if item["enabled"] else None,
            "last_run_at": _iso(item.get("last_run_epoch")),
            "history": list(item.get("history", []))[-10:]}


def status() -> dict[str, Any]:
    with _LOCK:
        data = _read()
    return {"format": FORMAT, "schedules": [_public(s) for s in data["schedules"].values()],
            "scheduler_running": bool(_THREAD and _THREAD.is_alive()), "kinds": list(KINDS),
            "min_interval_minutes": MIN_INTERVAL_MINUTES, "local_only": True,
            "business_writes": False, "delivers_to_third_parties": False}


def create(kind: str, target_id: str, *, interval_minutes: int, confirm_scheduled_runs: bool,
           confirm_unencrypted_local_backup: bool = False) -> dict[str, Any]:
    if confirm_scheduled_runs is not True:
        raise ScheduleError("explicit confirmation required to run this job on a schedule")
    if kind not in KINDS:
        raise ScheduleError("unknown job kind")
    if not isinstance(target_id, str) or not _TARGET.fullmatch(target_id):
        raise ScheduleError("invalid target")
    if type(interval_minutes) is not int or not MIN_INTERVAL_MINUTES <= interval_minutes <= MAX_INTERVAL_MINUTES:
        raise ScheduleError(f"interval must be {MIN_INTERVAL_MINUTES}-{MAX_INTERVAL_MINUTES} minutes")
    if kind == "local_backup":
        if interval_minutes < MIN_BACKUP_INTERVAL_MINUTES:
            raise ScheduleError("local backup interval must be at least 1440 minutes")
        if confirm_unencrypted_local_backup is not True:
            raise ScheduleError("explicit unencrypted local backup acknowledgement required")
    _check_target(kind, target_id)
    if kind == "local_backup":
        _check_backup_location()
    now = time.time()
    with _LOCK:
        data = _read()
        if any(s["kind"] == kind and s["target_id"] == target_id for s in data["schedules"].values()):
            raise ScheduleError("this job already has a schedule")
        if len(data["schedules"]) >= MAX_SCHEDULES:
            raise ScheduleError("schedule limit reached")
        schedule_id = "sch_" + secrets.token_hex(8)
        item = {"schedule_id": schedule_id, "kind": kind, "target_id": target_id,
                "interval_minutes": interval_minutes, "enabled": True, "created_at": _iso(now),
                "next_run_epoch": now + interval_minutes * 60, "last_run_epoch": None,
                "last_result": None, "consecutive_failures": 0, "paused_reason": None, "history": [],
                **({"unencrypted_local_backup_acknowledged": True} if kind == "local_backup" else {})}
        data["schedules"][schedule_id] = item
        _write(data)
    return _public(item)


def update(schedule_id: str, *, enabled: bool | None = None, interval_minutes: int | None = None) -> dict[str, Any]:
    with _LOCK:
        data = _read()
        item = data["schedules"].get(str(schedule_id))
        if item is None:
            raise ScheduleError("unknown schedule")
        if interval_minutes is not None:
            if type(interval_minutes) is not int or not MIN_INTERVAL_MINUTES <= interval_minutes <= MAX_INTERVAL_MINUTES:
                raise ScheduleError(f"interval must be {MIN_INTERVAL_MINUTES}-{MAX_INTERVAL_MINUTES} minutes")
            if item["kind"] == "local_backup" and interval_minutes < MIN_BACKUP_INTERVAL_MINUTES:
                raise ScheduleError("local backup interval must be at least 1440 minutes")
            item["interval_minutes"] = interval_minutes
        if enabled is not None:
            if type(enabled) is not bool:
                raise ScheduleError("enabled must be true or false")
            item["enabled"] = enabled
            if enabled:
                item.update(consecutive_failures=0, paused_reason=None,
                            next_run_epoch=time.time() + item["interval_minutes"] * 60)
        _write(data)
        return _public(item)


def delete(schedule_id: str) -> bool:
    with _LOCK:
        data = _read()
        removed = data["schedules"].pop(str(schedule_id), None) is not None
        if removed:
            _write(data)
    return removed


def forget_target(kind: str, target_id: str) -> None:
    """Remove schedules whose target was deleted (profile, dataset or log)."""
    with _LOCK:
        data = _read()
        keep = {k: v for k, v in data["schedules"].items() if not (v["kind"] == kind and v["target_id"] == target_id)}
        if len(keep) != len(data["schedules"]):
            data["schedules"] = keep
            _write(data)


def _check_backup_location() -> None:
    from . import local_backup
    backup = local_backup.backup_folder()
    if (backup.is_symlink() or backup.resolve().is_relative_to(local_backup.data_dir().resolve())
            or backup.resolve().is_relative_to(local_backup.auth_dir().resolve())):
        raise ScheduleError("backup folder cannot be inside local data or credentials")


def _check_target(kind: str, target_id: str) -> None:
    if kind == "local_backup":
        if target_id != "local":
            raise ScheduleError("local backup target must be local")
        # The local target exists as a capability regardless of current disk
        # configuration. A failed *run* should back off, not delete the job
        # as though a source profile or event-log dataset were removed.
        return
    if kind == "retained_jira_profile":
        from . import jira_retained_profiles as profiles
        ids = {p["profile_id"] for p in profiles.status().get("profiles", [])}
    else:
        from . import event_log_signals as signals
        try:
            signals.list_rules(target_id)
        except Exception:
            raise ScheduleError("unknown event log") from None
        return
    if target_id not in ids:
        raise ScheduleError("unknown retained Jira profile")


def _recording() -> bool:
    try:
        from shared.capture_control import read_state
        return read_state().get("state") == "recording"
    except Exception:
        return False


def _run_job(kind: str, target_id: str) -> tuple[str, float]:
    """Run one job; returns (outcome code, provider retry-after seconds)."""
    if kind == "local_backup":
        if target_id != "local":
            return "failed:invalid_local_backup_target", 0.0
        from . import local_backup_schedule
        try:
            local_backup_schedule.run()
            return "succeeded:local_backup_verified", 0.0
        except local_backup_schedule.ScheduledBackupError:
            return "failed:local_backup_unavailable", 0.0
    if kind == "retained_jira_profile":
        if not _recording():
            return "skipped_recording_paused", 0.0
        from . import jira_retained_profiles as profiles
        from .jira_retained_source import JiraIngestionError
        try:
            profiles.run(target_id, confirm_reads=True)
            return "succeeded", 0.0
        except JiraIngestionError as error:
            code = re.sub(r"[^a-z0-9_]", "", str(error).lower())[:80] or "jira_error"
            return "failed:" + code, float(getattr(error, "retry_seconds", 0) or 0)
    from . import event_log_signals as signals
    result = signals.evaluate(target_id)
    return f"succeeded:{len(result.get('signals', []))}_signals", 0.0


Runner = Callable[[str, str], tuple[str, float]]


def run_due(now: float | None = None, runner: Runner | None = None) -> list[dict[str, Any]]:
    """Run every due schedule once, sequentially; returns what ran."""
    runner = runner or _run_job
    ran: list[dict[str, Any]] = []
    if not _RUN_LOCK.acquire(blocking=False):
        return ran
    try:
        current = time.time() if now is None else now
        with _LOCK:
            due = [dict(s) for s in _read()["schedules"].values()
                   if s["enabled"] and (s.get("next_run_epoch") or 0) <= current]
        for item in sorted(due, key=lambda s: s.get("next_run_epoch") or 0):
            started = time.time() if now is None else now
            if runner is _run_job:
                try:
                    _check_target(item["kind"], item["target_id"])
                except ScheduleError:
                    delete(item["schedule_id"])  # the profile or log was deleted
                    ran.append({"schedule_id": item["schedule_id"], "outcome": "removed_target_gone"})
                    continue
            try:
                outcome, retry_after = runner(item["kind"], item["target_id"])
            except Exception as exc:  # never let one job stop the scheduler
                outcome, retry_after = "failed:" + type(exc).__name__.lower()[:60], 0.0
            with _LOCK:
                data = _read()
                live = data["schedules"].get(item["schedule_id"])
                if live is None:
                    continue
                failed = outcome.startswith("failed")
                live["consecutive_failures"] = live["consecutive_failures"] + 1 if failed else 0
                interval = live["interval_minutes"] * 60
                delay = interval
                if failed:
                    delay = min(MAX_BACKOFF_SECONDS, interval * 2 ** (live["consecutive_failures"] - 1))
                    delay = max(delay, retry_after)
                live.update(last_run_epoch=started, last_result=outcome, next_run_epoch=started + delay)
                if live["consecutive_failures"] >= MAX_FAILURES:
                    live.update(enabled=False, paused_reason="paused_after_repeated_failures")
                live["history"] = (live.get("history", []) + [
                    {"at": _iso(started), "outcome": outcome}])[-MAX_HISTORY:]
                _write(data)
                ran.append({"schedule_id": live["schedule_id"], "outcome": outcome})
    finally:
        _RUN_LOCK.release()
    return ran


def _loop() -> None:
    while not _STOP.wait(TICK_SECONDS):
        try:
            run_due()
        except Exception:
            pass


def start() -> None:
    """Start the tick thread; it does nothing until a schedule exists."""
    global _THREAD
    if _THREAD and _THREAD.is_alive():
        return
    _STOP.clear()
    _THREAD = threading.Thread(target=_loop, name="owg-local-scheduler", daemon=True)
    _THREAD.start()


def scheduled(kind: str, target_id: str) -> bool:
    with _LOCK:
        return any(s["kind"] == kind and s["target_id"] == target_id and s["enabled"]
                   for s in _read()["schedules"].values())


def stop() -> None:
    _STOP.set()


__all__ = ["KINDS", "MIN_BACKUP_INTERVAL_MINUTES", "ScheduleError", "create", "delete", "forget_target", "run_due", "start", "status",
           "stop", "update"]
