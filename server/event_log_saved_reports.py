"""Saved copies of an event log's HTML report, made by hand or on a local schedule.

Each copy is the same self-contained report as ``GET /v1/event-logs/{id}/report.html``
(overview, KPIs, variants, activities, weekly completions) for the whole log,
written to this computer's data folder so a weekly snapshot can be compared with
the last one. The newest ``KEEP`` copies per log are kept; older ones are
removed after a new copy is written. Copies are deleted with their log. Nothing
is sent anywhere.
"""
from __future__ import annotations

import json
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
    return {"name": path.name, "bytes": stat.st_size, "has_figures": _sidecar(path).is_file(),
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
    data = report.build(log_id)
    html = report.render(data)
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
    # The figures behind the copy, so two copies can be compared without parsing HTML.
    _write_atomic(folder, _sidecar(folder / name), json.dumps(figures(data), sort_keys=True))
    copies = _copies(folder)
    for old in copies[:-KEEP]:
        old.unlink(missing_ok=True)
        _sidecar(old).unlink(missing_ok=True)
    return {"format": FORMAT, "log_id": log_id, "saved": _describe(folder / name), "kept": min(len(copies), KEEP)}


def _sidecar(copy: Path) -> Path:
    return copy.with_suffix(".json")


def _write_atomic(folder: Path, target: Path, text: str) -> None:
    handle, temp = tempfile.mkstemp(dir=folder, prefix=".writing-", suffix=".json")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(text)
        os.replace(temp, target)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


MAX_ACTIVITIES = 500


def figures(data: dict[str, Any]) -> dict[str, Any]:
    """The headline figures of one report, as stored next to its saved copy."""
    summary, kpis, variants = data["summary"], data["kpis"], data["variants"]
    top = variants["variants"][0] if variants["variants"] else None
    costs = data.get("costs")
    return {
        "generated_at": data["generated_at"],
        "cases": summary["cases"], "events": summary["events"], "activities": summary["activities"],
        "median_case_seconds": summary["case_duration_seconds"]["median"],
        "p90_case_seconds": summary["case_duration_seconds"]["p90"],
        "events_per_case": kpis.get("events_per_case"), "repeat_rate": kpis.get("rework_rate"),
        "distinct_variants": variants["distinct_variants"],
        "top_variant": None if top is None else {"path": top["path"], "share": top["share"]},
        "activity_cases": {a["activity"]: a["cases"] for a in data["activities"]["activities"][:MAX_ACTIVITIES]},
        "total_cost": None if not costs else costs["total_cost"],
        "currency": None if not costs else costs["currency"],
    }


def _delta(a: Any, b: Any, digits: int = 6) -> Any:
    return None if a is None or b is None else round(b - a, digits)


def changes(log_id: str, older: str | None = None, newer: str | None = None) -> dict[str, Any]:
    """Compare the figures of two saved copies; by default the two newest that have figures."""
    _known(log_id)
    folder = _folder(log_id)
    with_figures = [p for p in _copies(folder) if _sidecar(p).is_file()]
    if (older is None) != (newer is None):
        raise workspace.EventLogWorkspaceError("give both copies to compare, or neither")
    if older is None:
        if len(with_figures) < 2:
            return {"format": FORMAT, "log_id": log_id, "available": False,
                    "reason": "two saved copies with figures are needed"}
        first, second = with_figures[-2], with_figures[-1]
    else:
        first, second = path(log_id, older), path(log_id, newer)
        if not _sidecar(first).is_file() or not _sidecar(second).is_file():
            raise workspace.EventLogWorkspaceError("that copy was saved without figures")
    a = json.loads(_sidecar(first).read_text(encoding="utf-8"))
    b = json.loads(_sidecar(second).read_text(encoding="utf-8"))
    acts_a, acts_b = a["activity_cases"], b["activity_cases"]
    moved = sorted(({"activity": k, "older_cases": acts_a[k], "newer_cases": acts_b[k], "change": acts_b[k] - acts_a[k]}
                    for k in set(acts_a) & set(acts_b) if acts_a[k] != acts_b[k]),
                   key=lambda r: (-abs(r["change"]), r["activity"]))[:10]
    same_currency = a.get("currency") and a.get("currency") == b.get("currency")
    return {
        "format": FORMAT, "log_id": log_id, "available": True,
        "older": {"name": first.name, "generated_at": a["generated_at"]},
        "newer": {"name": second.name, "generated_at": b["generated_at"]},
        "figures": {key: {"older": a.get(key), "newer": b.get(key), "change": _delta(a.get(key), b.get(key))}
                    for key in ("cases", "events", "activities", "distinct_variants", "median_case_seconds",
                                "p90_case_seconds", "events_per_case", "repeat_rate")},
        "top_variant": {"older": a.get("top_variant"), "newer": b.get("top_variant"),
                        "same_path": (a.get("top_variant") or {}).get("path") == (b.get("top_variant") or {}).get("path")},
        "activities_added": sorted(set(acts_b) - set(acts_a)),
        "activities_gone": sorted(set(acts_a) - set(acts_b)),
        "largest_activity_changes": moved,
        "cost": ({"currency": b["currency"], "older": a["total_cost"], "newer": b["total_cost"],
                  "change": _delta(a["total_cost"], b["total_cost"], 2)} if same_currency else None),
        "interpretation": ("Differences between two saved snapshots of the same log. A change can come from new or "
                           "appended events, an applied activity mapping or edited prices as much as from the process "
                           "itself."),
    }


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
