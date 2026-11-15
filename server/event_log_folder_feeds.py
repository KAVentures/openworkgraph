"""Folder feeds: append new CSV exports from a local folder to a saved log.

Many systems can only drop a nightly or hourly export file into a folder. A
feed points a saved log at such a folder: each run appends every new file that
matches the pattern, with the column mapping recorded for the log's latest CSV
import or append, so the log, its signals and its scheduled reports stay
current without anyone re-importing by hand. Runs happen on request or on the
local scheduler (job kind ``event_log_folder_feed``).

Rules:

* a feed is created explicitly for one log, one absolute folder and one file
  pattern (no path separators, CSV-like extension); only that folder is read,
  not its subfolders;
* the mapping is copied when the feed is created and shown with it; a later
  change to the log's mapping needs a new feed;
* a file is appended once, identified by the SHA-256 of its content; a file
  renamed or touched without changes is not appended again, and an edited file
  is a new file;
* files modified in the last ``SETTLE_SECONDS`` are left for the next run, as
  the exporter may still be writing them;
* events already in the log are skipped by the ordinary append rules, so
  overlapping exports are safe;
* a file that cannot be appended is recorded as failed and not retried with
  the same content.

Feeds and their file records are deleted with the log. Files in the folder are
never modified or deleted.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import event_log_workspace as workspace
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.event-log-folder-feed.v1"
MAX_FEEDS_PER_LOG = 5
MAX_FILES_PER_RUN = 20
SETTLE_SECONDS = 60
_PATTERN = re.compile(r"^[A-Za-z0-9 _.*?\[\]-]{1,80}\.(csv|tsv|txt)$", re.IGNORECASE)
_FEED_ID = re.compile(r"^feed_[0-9a-f]{16}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _latest_mapping(conn, log_id: str) -> dict[str, Any]:
    from .event_log_mapping_reuse import _mapping
    for (report,) in conn.execute("""SELECT report FROM log_quality WHERE log_id = ?
            ORDER BY created_at DESC, batch DESC""", [log_id]).fetchall():
        try:
            mapping = _mapping(json.loads(report).get("transform") or {})
        except (ValueError, AttributeError):
            mapping = None
        if mapping:
            return mapping
    raise EventLogWorkspaceError("this log has no CSV import or append whose mapping a feed could reuse")


def _folder(value: Any) -> Path:
    if not isinstance(value, str) or not value.strip() or len(value) > 1000 or "\x00" in value:
        raise EventLogWorkspaceError("give the folder as an absolute path")
    path = Path(value.strip()).expanduser()
    if not path.is_absolute():
        raise EventLogWorkspaceError("give the folder as an absolute path")
    if not path.is_dir():
        raise EventLogWorkspaceError("the folder does not exist or is not a folder")
    return path.resolve()


def create(log_id: str, spec: Any) -> dict[str, Any]:
    if not isinstance(spec, dict) or set(spec) - {"folder", "pattern", "confirm_folder_reads"}:
        raise EventLogWorkspaceError("a feed needs folder, pattern and confirm_folder_reads")
    if spec.get("confirm_folder_reads") is not True:
        raise EventLogWorkspaceError("explicit confirmation to read files from this folder is required")
    folder = _folder(spec.get("folder"))
    pattern = spec.get("pattern") or "*.csv"
    if not isinstance(pattern, str) or not _PATTERN.fullmatch(pattern):
        raise EventLogWorkspaceError("the file pattern must be a simple name pattern ending in .csv, .tsv or .txt")
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            mapping = _latest_mapping(conn, log_id)
            if conn.execute("SELECT count(*) FROM event_log_feeds WHERE log_id = ?", [log_id]).fetchone()[0] >= MAX_FEEDS_PER_LOG:
                raise EventLogWorkspaceError(f"a log can have at most {MAX_FEEDS_PER_LOG} feeds")
            if conn.execute("SELECT 1 FROM event_log_feeds WHERE log_id = ? AND json_extract_string(definition, '$.folder') = ? "
                            "AND json_extract_string(definition, '$.pattern') = ?", [log_id, str(folder), pattern]).fetchone():
                raise EventLogWorkspaceError("this log already has a feed for that folder and pattern")
            feed = {"format": FORMAT, "feed_id": "feed_" + secrets.token_hex(8), "log_id": log_id,
                    "folder": str(folder), "pattern": pattern, "mapping": mapping, "created_at": _now()}
            conn.execute("INSERT INTO event_log_feeds VALUES (?, ?, now(), ?)",
                         [log_id, feed["feed_id"], json.dumps(feed, sort_keys=True)])
        finally:
            conn.close()
    return feed


def _feed(conn, feed_id: str) -> dict[str, Any]:
    if not isinstance(feed_id, str) or not _FEED_ID.fullmatch(feed_id):
        raise EventLogWorkspaceError("unknown feed")
    row = conn.execute("SELECT definition FROM event_log_feeds WHERE feed_id = ?", [feed_id]).fetchone()
    if not row:
        raise EventLogWorkspaceError("unknown feed")
    return json.loads(row[0])


def get(feed_id: str) -> dict[str, Any]:
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            return _feed(conn, feed_id)
        finally:
            conn.close()


def feeds(log_id: str) -> dict[str, Any]:
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            out = []
            for (definition,) in conn.execute("SELECT definition FROM event_log_feeds WHERE log_id = ? ORDER BY created_at",
                                              [log_id]).fetchall():
                feed = json.loads(definition)
                files = conn.execute("""SELECT name, sha256, bytes, processed_at, outcome, detail FROM event_log_feed_files
                    WHERE feed_id = ? ORDER BY processed_at DESC LIMIT 20""", [feed["feed_id"]]).fetchall()
                totals = conn.execute("""SELECT count(*) FILTER (WHERE outcome = 'appended'),
                    count(*) FILTER (WHERE outcome = 'failed') FROM event_log_feed_files WHERE feed_id = ?""",
                                      [feed["feed_id"]]).fetchone()
                feed["files_appended"], feed["files_failed"] = totals
                feed["recent_files"] = [{"name": n, "sha256": h, "bytes": b, "processed_at": p.isoformat() + "Z",
                                         "outcome": o, **json.loads(d)} for n, h, b, p, o, d in files]
                out.append(feed)
        finally:
            conn.close()
    return {"format": FORMAT, "feeds": out}


def delete(feed_id: str) -> dict[str, Any]:
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            feed = _feed(conn, feed_id)
            conn.execute("DELETE FROM event_log_feed_files WHERE feed_id = ?", [feed_id])
            conn.execute("DELETE FROM event_log_feeds WHERE feed_id = ?", [feed_id])
        finally:
            conn.close()
    from .local_scheduler import forget_target
    forget_target("event_log_folder_feed", feed_id)
    return {"format": FORMAT, "deleted": feed_id, "log_id": feed["log_id"]}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def run(feed_id: str, *, now: float | None = None) -> dict[str, Any]:
    """Append the feed folder's new, settled files; returns what happened to each."""
    feed = get(feed_id)
    folder = Path(feed["folder"])
    if not folder.is_dir():
        raise EventLogWorkspaceError("the feed folder is no longer available")
    current = time.time() if now is None else now
    candidates = []
    for path in folder.glob(feed["pattern"]):
        try:
            stat = path.stat()
        except OSError:
            continue
        if path.is_file() and not path.is_symlink():
            candidates.append((stat.st_mtime, path.name, path, stat.st_size))
    candidates.sort()
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            seen = {h for (h,) in conn.execute("SELECT sha256 FROM event_log_feed_files WHERE feed_id = ?",
                                                [feed_id]).fetchall()}
        finally:
            conn.close()
    waiting, already, results = 0, 0, []
    for mtime, name, path, size in candidates:
        if current - mtime < SETTLE_SECONDS:
            waiting += 1
            continue
        if len(results) >= MAX_FILES_PER_RUN:
            break
        try:
            sha = _sha256(path)
        except OSError:
            continue
        if sha in seen:
            already += 1
            continue
        seen.add(sha)
        try:
            with path.open("rb") as handle:
                appended = workspace.append_log(feed["log_id"], handle, source_format="csv", mapping=feed["mapping"],
                                                kind="folder_feed")
            outcome, detail = "appended", {"added_events": appended["added_events"],
                                           "duplicate_events_skipped": appended["duplicate_events_skipped"],
                                           "rejected_events": appended["rejected_events"], "batch": appended["batch"]}
        except EventLogWorkspaceError as exc:
            outcome, detail = "failed", {"error": str(exc)[:300]}
        with workspace._LOCK:
            conn = workspace._connect()
            try:
                conn.execute("INSERT INTO event_log_feed_files VALUES (?, ?, ?, ?, ?, now(), ?, ?)",
                             [feed["log_id"], feed_id, name[:200], sha, size, outcome, json.dumps(detail)])
            finally:
                conn.close()
        results.append({"name": name, "outcome": outcome, **detail})
    appended = [r for r in results if r["outcome"] == "appended"]
    return {"format": FORMAT, "feed_id": feed_id, "log_id": feed["log_id"], "ran_at": _now(),
            "files_appended": len(appended), "files_failed": len(results) - len(appended),
            "events_added": sum(r["added_events"] for r in appended),
            "files_already_appended": already, "files_still_being_written": waiting,
            "more_files_waiting": len(candidates) - waiting - already - len(results) > 0 and len(results) >= MAX_FILES_PER_RUN,
            "files": results}


def run_scheduled(feed_id: str) -> tuple[str, float]:
    """Scheduler entry point: an outcome code like the other local jobs."""
    try:
        result = run(feed_id)
    except EventLogWorkspaceError:
        return "failed:feed_unavailable", 0.0
    if result["files_failed"]:
        return f"failed:{result['files_failed']}_files_not_appended", 0.0
    return f"succeeded:{result['files_appended']}_files_appended", 0.0


def erase_log(conn, log_id: str) -> list[str]:
    """Inside the log deletion: remove the log's feeds; returns their ids so schedules can be forgotten."""
    ids = [r[0] for r in conn.execute("SELECT feed_id FROM event_log_feeds WHERE log_id = ?", [log_id]).fetchall()]
    conn.execute("DELETE FROM event_log_feed_files WHERE log_id = ?", [log_id])
    conn.execute("DELETE FROM event_log_feeds WHERE log_id = ?", [log_id])
    return ids
