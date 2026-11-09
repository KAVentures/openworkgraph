"""Saved copies of an event log's HTML report, made by hand or on a local schedule.

Each copy is the same self-contained report as ``GET /v1/event-logs/{id}/report.html``
(overview, KPIs, variants, activities, weekly completions) for the whole log,
written to this computer's data folder so a weekly snapshot can be compared with
the last one. The newest ``KEEP`` copies per log are kept; older ones are
removed after a new copy is written. Copies are deleted with their log. Nothing
is sent anywhere.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import event_log_report as report
from . import event_log_workspace as workspace

FORMAT = "openworkgraph.event-log-saved-reports.v1"
KEEP = 10
_NAME = re.compile(r"^report-(\d{8}T\d{6}Z)(?:-(\d+))?\.html$")


def _folder(log_id: str) -> Path:
    if not workspace._LOG_ID.fullmatch(str(log_id or "")):
        raise workspace.EventLogWorkspaceError("unknown event log")
    return Path(workspace.DATA_DIR) / "event_log_reports" / log_id


def _known(log_id: str) -> None:
    if not any(log["log_id"] == log_id for log in workspace.list_logs()["logs"]):
        raise workspace.EventLogWorkspaceError("unknown event log")


def _describe(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {"name": path.name, "bytes": stat.st_size,
            "saved_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")}


def _copies(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    # Names embed the UTC time and a sequence number for copies saved in the same second.
    def order(p: Path) -> tuple[str, int]:
        match = _NAME.fullmatch(p.name)
        return match.group(1), int(match.group(2) or 0)
    return sorted((p for p in folder.iterdir() if p.is_file() and _NAME.fullmatch(p.name)), key=order)


def save(log_id: str) -> dict[str, Any]:
    """Write a new copy of the report and keep the newest ``KEEP``."""
    _known(log_id)
    html = report.report_html(log_id)
    folder = _folder(log_id)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    # A copy made in the same second as earlier ones gets a higher sequence number than any of them,
    # even if pruning already removed some, so it always sorts as the newest.
    same_second = [int(m.group(2) or 0) for p in _copies(folder) if (m := _NAME.fullmatch(p.name)) and m.group(1) == stamp]
    name = f"report-{stamp}.html" if not same_second else f"report-{stamp}-{max(same_second) + 1}.html"
    handle, temp = tempfile.mkstemp(dir=folder, prefix=".writing-", suffix=".html")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(html)
        os.replace(temp, folder / name)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise
    copies = _copies(folder)
    for old in copies[:-KEEP]:
        old.unlink(missing_ok=True)
    return {"format": FORMAT, "log_id": log_id, "saved": _describe(folder / name), "kept": min(len(copies), KEEP)}


def listing(log_id: str) -> dict[str, Any]:
    _known(log_id)
    copies = _copies(_folder(log_id))
    return {"format": FORMAT, "log_id": log_id, "reports": [_describe(p) for p in reversed(copies)], "keep": KEEP,
            "stored_on_this_computer_only": True}


def path(log_id: str, name: str) -> Path:
    _known(log_id)
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise workspace.EventLogWorkspaceError("unknown saved report")
    target = _folder(log_id) / name
    if not target.is_file():
        raise workspace.EventLogWorkspaceError("unknown saved report")
    return target


def erase(log_id: str) -> None:
    """Remove every saved copy of a log's report (called when the log is deleted)."""
    try:
        folder = _folder(log_id)
    except workspace.EventLogWorkspaceError:
        return
    shutil.rmtree(folder, ignore_errors=True)
