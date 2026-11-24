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
  the same content; files too large to snapshot are reported on each run.

Feeds and their file records are deleted with the log. Files in the folder are
never modified or deleted.
"""
from __future__ import annotations

import hashlib
import heapq
import json
import os
import re
import secrets
import stat
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Iterator

from . import event_log_workspace as workspace
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.event-log-folder-feed.v1"
MAX_FEEDS_PER_LOG = 5
MAX_FILES_PER_RUN = 20
MAX_PREVIEW_FILES = 40
MAX_PREVIEW_BYTES = 32 * 1024 * 1024
SETTLE_SECONDS = 60
_PATTERN = re.compile(r"^.{1,80}\.(csv|tsv|txt)$", re.IGNORECASE)
_UNSAFE_PATTERN = re.compile(r"[/\\\x00-\x1f\x7f-\x9f\ud800-\udfff]")
_FEED_ID = re.compile(r"^feed_[0-9a-f]{16}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
FAILURE_PAGE_SIZE = 50


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
    if (not isinstance(value, str) or not value.strip() or len(value) > 1000 or "\x00" in value
            or any(0xD800 <= ord(char) <= 0xDFFF for char in value)):
        raise EventLogWorkspaceError("give the folder as an absolute path")
    # Whitespace can be part of a real path. Trimming it can redirect a feed
    # to a different, unselected sibling directory.
    try:
        path = Path(value).expanduser()
    except (OSError, RuntimeError):
        raise EventLogWorkspaceError("give the folder as an absolute path") from None
    if not path.is_absolute():
        raise EventLogWorkspaceError("give the folder as an absolute path")
    try:
        if not path.is_dir():
            raise EventLogWorkspaceError("the folder does not exist or is not a folder")
        return path.resolve()
    except (OSError, RuntimeError):
        raise EventLogWorkspaceError("the folder does not exist or is not a folder") from None


def create(log_id: str, spec: Any) -> dict[str, Any]:
    if not isinstance(spec, dict) or set(spec) - {"folder", "pattern", "confirm_folder_reads"}:
        raise EventLogWorkspaceError("a feed needs folder, pattern and confirm_folder_reads")
    if spec.get("confirm_folder_reads") is not True:
        raise EventLogWorkspaceError("explicit confirmation to read files from this folder is required")
    folder = _folder(spec.get("folder"))
    pattern = spec.get("pattern", "*.csv")
    if pattern is None or pattern == "":
        pattern = "*.csv"
    if not isinstance(pattern, str) or not _PATTERN.fullmatch(pattern) or _UNSAFE_PATTERN.search(pattern):
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


class _FileChanged(Exception):
    """The exporter changed or replaced a file while it was being read."""


def _identity(info: os.stat_result) -> tuple[int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _digest(path: Path, expected: os.stat_result, limit: int, destination: BinaryIO | None = None) -> tuple[str, int]:
    """Hash a stable regular file without following a replaced symlink."""
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as source:
        opened = os.fstat(source.fileno())
        if not stat.S_ISREG(opened.st_mode) or _identity(opened) != _identity(expected):
            raise _FileChanged()
        if opened.st_size > limit:
            raise EventLogWorkspaceError("event log is larger than the supported limit")
        digest, size = hashlib.sha256(), 0
        while chunk := source.read(min(1 << 20, limit - size + 1)):
            size += len(chunk)
            if size > limit:
                raise EventLogWorkspaceError("event log is larger than the supported limit")
            digest.update(chunk)
            if destination is not None:
                destination.write(chunk)
        if _identity(os.fstat(source.fileno())) != _identity(opened):
            raise _FileChanged()
        current = path.lstat()
        if not stat.S_ISREG(current.st_mode) or _identity(current) != _identity(opened):
            raise _FileChanged()
        return digest.hexdigest(), size


@contextmanager
def _snapshot(path: Path, expected: os.stat_result, *, limit: int | None = None):
    """Hash exactly the bytes later appended."""
    with tempfile.TemporaryFile() as snapshot:
        sha, size = _digest(path, expected, workspace.MAX_IMPORT_BYTES if limit is None else limit, snapshot)
        snapshot.seek(0)
        yield sha, size, snapshot


def _candidates(feed: dict[str, Any]) -> Iterator[tuple[float, str, Path, os.stat_result]]:
    folder = Path(feed["folder"])
    if not folder.is_dir():
        raise EventLogWorkspaceError("the feed folder is no longer available")
    for path in folder.glob(feed["pattern"]):
        try:
            info = path.lstat()
        except OSError:
            continue
        if stat.S_ISREG(info.st_mode):
            yield info.st_mtime, path.name, path, info


def preview(feed_id: str, *, now: float | None = None) -> dict[str, Any]:
    """Inspect bounded file metadata and hashes without staging, validating, or appending rows."""
    feed = get(feed_id)
    current = time.time() if now is None else now
    candidates = heapq.nsmallest(MAX_PREVIEW_FILES + 1, _candidates(feed))
    files: list[dict[str, Any]] = []
    pending: dict[int, str] = {}
    remaining = MAX_PREVIEW_BYTES
    for mtime, name, path, info in candidates[:MAX_PREVIEW_FILES]:
        row = {"name": name, "bytes": info.st_size}
        if current - mtime < SETTLE_SECONDS:
            state = "settling"
        elif info.st_size > workspace.MAX_IMPORT_BYTES:
            state = "too_large"
        elif info.st_size + 1 > remaining:
            state = "preview_limit"
        else:
            # Count one extra byte per read for EOF or for a file that grew.
            remaining -= info.st_size + 1
            try:
                sha, _ = _digest(path, info, info.st_size)
                pending[len(files)] = sha
                state = "new_content"
            except (_FileChanged, EventLogWorkspaceError):
                state = "settling"
            except OSError:
                state = "unavailable"
        files.append({**row, "state": state})

    with workspace._LOCK:
        conn = workspace._connect()
        try:
            _feed(conn, feed_id)
            previous = dict(conn.execute("""SELECT sha256,
                    CASE WHEN bool_or(outcome = 'appended') THEN 'already_appended' ELSE 'previously_failed' END
                FROM event_log_feed_files WHERE feed_id = ? AND sha256 IN (SELECT unnest(?::VARCHAR[]))
                GROUP BY sha256""", [feed_id, list(set(pending.values()))]).fetchall())
        finally:
            conn.close()
    seen: set[str] = set()
    for index, sha in pending.items():
        state = previous.get(sha, "repeated_content" if sha in seen else "new_content")
        files[index]["state"] = state
        if state == "new_content":
            files[index]["sha256"] = sha
        seen.add(sha)
    counts: dict[str, int] = {}
    for row in files:
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    return {"format": FORMAT, "feed_id": feed_id, "files": files, "counts": counts,
            "more_files": len(candidates) > MAX_PREVIEW_FILES, "max_files": MAX_PREVIEW_FILES,
            "max_bytes": MAX_PREVIEW_BYTES}


def failures(feed_id: str, *, after: str = "") -> dict[str, Any]:
    """Page distinct outstanding failures without returning file contents."""
    if not isinstance(after, str) or (after and not _SHA256.fullmatch(after)):
        raise EventLogWorkspaceError("invalid failure page cursor")
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            _feed(conn, feed_id)
            rows = conn.execute("""WITH latest AS (
                SELECT name, sha256, bytes, processed_at, outcome, detail,
                    row_number() OVER (PARTITION BY sha256 ORDER BY (outcome = 'appended') DESC, processed_at DESC, name) AS rank
                FROM event_log_feed_files WHERE feed_id = ?)
                SELECT name, sha256, bytes, processed_at, detail FROM latest
                WHERE rank = 1 AND outcome = 'failed' AND sha256 > ? ORDER BY sha256 LIMIT ?""",
                [feed_id, after, FAILURE_PAGE_SIZE + 1]).fetchall()
        finally:
            conn.close()
    more = len(rows) > FAILURE_PAGE_SIZE
    shown = rows[:FAILURE_PAGE_SIZE]
    return {"format": FORMAT, "feed_id": feed_id,
            "failures": [{"name": n, "sha256": sha, "bytes": size, "processed_at": stamp.isoformat() + "Z",
                          **json.loads(detail)} for n, sha, size, stamp, detail in shown],
            "next_after": shown[-1][1] if more else None}


def append_one(feed_id: str, spec: Any) -> dict[str, Any]:
    if not isinstance(spec, dict) or set(spec) != {"name", "sha256"}:
        raise EventLogWorkspaceError("choose a previewed file by its exact name and SHA-256")
    name, sha = spec["name"], spec["sha256"]
    if (not isinstance(name, str) or not name or len(name) > 1024 or "/" in name or "\x00" in name
            or any(0xD800 <= ord(char) <= 0xDFFF for char in name)
            or not isinstance(sha, str) or not _SHA256.fullmatch(sha)):
        raise EventLogWorkspaceError("choose a previewed file by its exact name and SHA-256")
    return run(feed_id, selected=(name, sha))


def run(feed_id: str, *, now: float | None = None, retry_failed: bool = False,
        retry_hash: str | None = None, selected: tuple[str, str] | None = None) -> dict[str, Any]:
    """Append all new files, one selected file, or an explicit retry set."""
    if type(retry_failed) is not bool:
        raise EventLogWorkspaceError("retry_failed must be a boolean")
    if retry_hash is not None and (not retry_failed or not isinstance(retry_hash, str) or not _SHA256.fullmatch(retry_hash)):
        raise EventLogWorkspaceError("retry hash must be a SHA-256 for a failed file")
    feed = get(feed_id)
    current = time.time() if now is None else now
    candidates = sorted(item for item in _candidates(feed) if selected is None or item[1] == selected[0])
    selected_changed = False
    waiting, already, previously_failed, skipped_new, duplicate_failed, results, attempted = 0, 0, 0, 0, 0, [], set()
    for mtime, name, path, info in candidates:
        if current - mtime < SETTLE_SECONDS:
            waiting += 1
            continue
        if len(results) >= MAX_FILES_PER_RUN:
            break
        if selected is not None and info.st_size + 1 > MAX_PREVIEW_BYTES:
            selected_changed = True
            break
        try:
            snap = (_snapshot(path, info) if selected is None
                    else _snapshot(path, info, limit=min(info.st_size, workspace.MAX_IMPORT_BYTES)))
            with snap as (sha, size, snapshot), workspace._LOCK:
                if selected is not None and sha != selected[1]:
                    selected_changed = True
                    break
                if retry_hash is not None and sha != retry_hash:
                    skipped_new += 1
                    continue
                conn = workspace._connect()
                try:
                    # Recheck under the append lock: another run may have won,
                    # or the feed may have been deleted during the snapshot.
                    _feed(conn, feed_id)
                    prior = conn.execute("""SELECT outcome, detail FROM event_log_feed_files WHERE feed_id = ? AND sha256 = ?
                        ORDER BY (outcome = 'appended') DESC, processed_at DESC LIMIT 1""", [feed_id, sha]).fetchone()
                finally:
                    conn.close()
                if retry_failed and not prior:
                    skipped_new += 1
                    if retry_hash is not None:
                        break
                    continue
                if prior and (not retry_failed or prior[0] == 'appended'):
                    if prior[0] == 'appended':
                        already += 1
                    else:
                        previously_failed += 1
                    if retry_hash is not None:
                        break
                    continue
                if retry_failed and sha in attempted:
                    duplicate_failed += 1
                    continue
                if retry_failed:
                    attempted.add(sha)
                def record_append(conn, log_id):
                    batch, added, duplicate, rejected = conn.execute("""SELECT batch, added_events, duplicate_events, rejected_events
                        FROM log_appends WHERE log_id = ? ORDER BY batch DESC LIMIT 1""", [log_id]).fetchone()
                    detail = {"added_events": added, "duplicate_events_skipped": duplicate, "rejected_events": rejected, "batch": batch}
                    conn.execute("DELETE FROM event_log_feed_files WHERE feed_id = ? AND sha256 = ? AND outcome = 'failed'", [feed_id, sha])
                    conn.execute("INSERT INTO event_log_feed_files VALUES (?, ?, ?, ?, ?, now(), ?, ?)",
                                 [log_id, feed_id, name[:200], sha, size, "appended", json.dumps(detail)])
                try:
                    appended = workspace.append_log(feed["log_id"], snapshot, source_format="csv", mapping=feed["mapping"],
                                                    kind="folder_feed", _transaction_hook=record_append)
                    outcome, detail = "appended", {"added_events": appended["added_events"],
                        "duplicate_events_skipped": appended["duplicate_events_skipped"],
                        "rejected_events": appended["rejected_events"], "batch": appended["batch"]}
                except EventLogWorkspaceError as exc:
                    conn = workspace._connect()
                    try:
                        # A post-commit reporting error must not turn a committed
                        # append into a failed record or a retry.
                        prior = conn.execute("SELECT outcome, detail FROM event_log_feed_files WHERE feed_id = ? AND sha256 = ? LIMIT 1", [feed_id, sha]).fetchone()
                        if prior and prior[0] == 'appended':
                            outcome, detail = prior[0], json.loads(prior[1])
                            detail["warning"] = "The append committed, but its quality report could not be recorded."
                            conn.execute("UPDATE event_log_feed_files SET detail = ? WHERE feed_id = ? AND sha256 = ?",
                                         [json.dumps(detail), feed_id, sha])
                        else:
                            outcome, detail = "failed", {"error": str(exc)[:300]}
                            conn.execute("DELETE FROM event_log_feed_files WHERE feed_id = ? AND sha256 = ? AND outcome = 'failed'", [feed_id, sha])
                            conn.execute("INSERT INTO event_log_feed_files VALUES (?, ?, ?, ?, ?, now(), ?, ?)",
                                         [feed["log_id"], feed_id, name[:200], sha, size, outcome, json.dumps(detail)])
                    finally:
                        conn.close()
        except _FileChanged:
            waiting += 1
            continue
        except OSError:
            if selected is not None:
                selected_changed = True
                break
            continue
        except EventLogWorkspaceError as exc:
            if selected is not None and str(exc) == "event log is larger than the supported limit":
                selected_changed = True
                break
            if retry_failed and str(exc) == "event log is larger than the supported limit":
                skipped_new += 1
                continue
            results.append({"name": name, "outcome": "failed", "error": str(exc)[:300]})
            continue
        results.append({"name": name, "outcome": outcome, **detail})
        if retry_hash is not None:
            break
    appended = [r for r in results if r["outcome"] == "appended"]
    return {"format": FORMAT, "feed_id": feed_id, "log_id": feed["log_id"], "ran_at": _now(),
            "files_appended": len(appended), "files_failed": len(results) - len(appended),
            "events_added": sum(r["added_events"] for r in appended),
            "files_already_appended": already, "files_previously_failed": previously_failed, "files_still_being_written": waiting,
            **({"retry_failed_only": True, "new_files_left_for_normal_run": skipped_new,
                "duplicate_failed_files_skipped": duplicate_failed} if retry_failed else {}),
            "more_files_waiting": len(candidates) - waiting - already - previously_failed - skipped_new - duplicate_failed - len(results) > 0 and len(results) >= MAX_FILES_PER_RUN,
            "files": results,
            **({"selected_changed": selected_changed or not candidates} if selected is not None else {})}


def run_scheduled(feed_id: str) -> tuple[str, float]:
    """Scheduler entry point: an outcome code like the other local jobs."""
    try:
        result = run(feed_id)
    except EventLogWorkspaceError:
        return "failed:feed_unavailable", 0.0
    if result["files_failed"]:
        return f"failed:{result['files_failed']}_files_not_appended", 0.0
    if any(item.get("warning") for item in result["files"]):
        return "failed:quality_report_unavailable", 0.0
    if result['files_previously_failed']:
        return 'failed:previously_failed_files', 0.0
    if result['more_files_waiting'] and result['files_still_being_written']:
        return 'partial:more_files_pending_and_not_ready', 0.0
    if result['more_files_waiting']:
        return 'partial:more_files_pending', 0.0
    if result['files_still_being_written']:
        return 'partial:files_not_ready', 0.0
    return f"succeeded:{result['files_appended']}_files_appended", 0.0


def erase_log(conn, log_id: str) -> list[str]:
    """Inside the log deletion: remove the log's feeds; returns their ids so schedules can be forgotten."""
    ids = [r[0] for r in conn.execute("SELECT feed_id FROM event_log_feeds WHERE log_id = ?", [log_id]).fetchall()]
    conn.execute("DELETE FROM event_log_feed_files WHERE log_id = ?", [log_id])
    conn.execute("DELETE FROM event_log_feeds WHERE log_id = ?", [log_id])
    return ids
