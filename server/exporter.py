from __future__ import annotations

import csv
import io
import json
import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import xlsxwriter

from .analytics import candidate_tasks, semantic_activity, summary
from .db import normalized_rows, rows

ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip() if (ROOT / "VERSION").exists() else "unknown"
AI_GUIDE_PATH = ROOT / "AI_GUIDE.md"
PROMPT_PATH = ROOT / "PROMPT.md"
EXCEL_DATA_ROWS_PER_SHEET = 1_048_575


def _read_bundled_text(path: Path, fallback: str) -> str:
    try:
        return path.read_text(encoding="utf-8").strip() + "\n"
    except Exception:
        return fallback.strip() + "\n"


AI_DATA_DICTIONARY_MD = _read_bundled_text(
    AI_GUIDE_PATH,
    """# OpenWorkGraph AI guide
Treat observed workflow rows as evidence. Derived tasks and summaries are heuristic indexes, not ground truth. Typed text, individual key identities and clipboard contents are not captured. Browser-extension events are optional; desktop-derived browser evidence can still exist when browser semantic events are zero.
""",
)
AI_STARTER_PROMPT_MD = _read_bundled_text(
    PROMPT_PATH,
    """# OpenWorkGraph starter prompt
Use the OpenWorkGraph evidence to reconstruct how I worked. Treat raw observations as evidence and derived tasks/summaries as non-authoritative heuristics. Combine this workflow evidence with all other context and tools available to you, and clearly distinguish observations from your inferences.
""",
)


def _since_for_scope(scope: str) -> str | None:
    return os.getenv("WORKFLOW_OBSERVER_RUN_STARTED_AT") if scope == "current" else None


def _loader_for(table: str):
    if table == "events":
        return rows
    if table == "normalized_events":
        return normalized_rows
    raise ValueError("unsupported table")


def _query_table(table: str, *, since: str | None) -> list[dict[str, Any]]:
    """Return the complete evidence table for the requested scope.

    Exports must never silently truncate evidence. Derived summaries may use bounded
    analytical windows for responsiveness, but the exported evidence tables are
    complete and the manifest states that explicitly.
    """
    loader = _loader_for(table)
    if since:
        return loader(
            f"SELECT * FROM {table} WHERE observed_at >= ? ORDER BY observed_at ASC",
            (since,),
        )
    return loader(f"SELECT * FROM {table} ORDER BY observed_at ASC")


def _count_table(table: str, *, since: str | None) -> int:
    loader = _loader_for(table)
    if since:
        result = loader(f"SELECT COUNT(*) AS count FROM {table} WHERE observed_at >= ?", (since,))
    else:
        result = loader(f"SELECT COUNT(*) AS count FROM {table}")
    return int((result[0] if result else {}).get("count", 0) or 0)


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, tuple):
        return [_jsonable(v) for v in value]
    return value


def _capture_manifest(
    *,
    scope: str,
    include_raw: bool,
    raw_summary: dict[str, Any],
    operational_rows: int,
    raw_rows: int,
) -> dict[str, Any]:
    browser_events = int(raw_summary.get("browser_semantic_events", 0) or 0)
    event_count = raw_rows
    return {
        "schema_version": "1.0",
        "product": "OpenWorkGraph / Workflow Observer",
        "product_version": VERSION,
        "scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evidence": {
            "raw_local_evidence_included": bool(include_raw),
            "desktop_observer": "observed_in_export" if event_count else "no_events_in_export",
            "browser_extension": "observed_in_export" if browser_events else "not_observed_in_export",
            # Keep the v0.50-v0.53 public key while also making the bounded-summary
            # nature explicit in the additive key below.
            "browser_extension_semantic_event_count": browser_events,
            "browser_extension_semantic_event_count_in_bounded_summary": browser_events,
            "desktop_browser_context_may_still_be_present": True,
            "typed_text_captured": False,
            "individual_key_identities_captured": False,
            "clipboard_contents_captured": False,
            "screenshots_in_normal_capture": False,
            "operational_evidence_rows_exported": operational_rows,
            "raw_evidence_rows_exported": raw_rows if include_raw else 0,
            "evidence_tables_truncated": False,
        },
        "interpretation": {
            "raw_local_evidence_role": "primary captured evidence when included",
            "operational_layers_role": "privacy-normalized derived/index views",
            "inferred_tasks_role": "heuristic convenience index; not ground truth",
            "derived_analytics_note": "Some summaries/heuristic indexes intentionally use bounded analytical windows; evidence tables themselves are complete.",
            "missing_browser_extension_events_mean": "No browser-extension semantic events were observed in the bounded summary; desktop-derived browser evidence may still exist.",
        },
    }


def build_export_payload(*, scope: str = "current", include_raw: bool = False) -> dict[str, Any]:
    since = _since_for_scope(scope)
    operational_summary = summary(100000, since=since, operational=True)
    raw_summary = summary(100000, since=since, operational=False)
    task_data = candidate_tasks(limit=100000, since=since)
    operational_events = _query_table("normalized_events", since=since)
    raw_events = _query_table("events", since=since) if include_raw else []
    operational_count = len(operational_events)
    raw_count = len(raw_events) if include_raw else _count_table("events", since=since)
    manifest = _capture_manifest(
        scope=scope,
        include_raw=include_raw,
        raw_summary=raw_summary,
        operational_rows=operational_count,
        raw_rows=raw_count,
    )

    return {
        "export": {
            "product": "OpenWorkGraph / Workflow Observer",
            "version": VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "scope": scope,
            "run_started_at": os.getenv("WORKFLOW_OBSERVER_RUN_STARTED_AT"),
            "include_raw_local_evidence": bool(include_raw),
            "evidence_tables_truncated": False,
            "privacy_note": (
                "Raw local evidence may contain names, subjects, document titles, URLs, and other sensitive content."
                if include_raw
                else "This export contains the privacy-normalized operational layer; raw local evidence is excluded."
            ),
            "interpretation_note": "Observed evidence is authoritative for what was captured. Inferred tasks/summaries are non-authoritative heuristics that AI should verify against evidence.",
        },
        "capture_manifest": manifest,
        "ai_guide_markdown": AI_DATA_DICTIONARY_MD,
        "starter_prompt_markdown": AI_STARTER_PROMPT_MD,
        "overview": {
            "operational": operational_summary,
            "raw_capture_counts": {
                "events": raw_count,
                # Preserve established keys for callers; these three are bounded
                # analytic counts, while `events` and evidence tables are complete.
                "focus_events": raw_summary.get("focus_events", 0),
                "screen_interactions": raw_summary.get("screen_interactions", 0),
                "browser_semantic_events": raw_summary.get("browser_semantic_events", 0),
                "focus_events_in_bounded_summary": raw_summary.get("focus_events", 0),
                "screen_interactions_in_bounded_summary": raw_summary.get("screen_interactions", 0),
                "browser_semantic_events_in_bounded_summary": raw_summary.get("browser_semantic_events", 0),
            },
        },
        "effort_by_surface": operational_summary.get("surfaces", []),
        "transitions": operational_summary.get("transitions", []),
        "repeated_workflow_fragments": operational_summary.get("frequent_sequences", []),
        "semantic_action_counts": operational_summary.get("semantic_action_counts", []),
        "inferred_tasks": task_data.get("tasks", []),
        "repeated_task_families": task_data.get("patterns", []),
        "task_inference": task_data.get("inference", {}),
        "operational_events": operational_events,
        "operational_semantic_activity": semantic_activity(limit=100000, since=since, operational=True),
        **({"raw_local_evidence": raw_events} if include_raw else {}),
    }


def json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(_jsonable(payload), ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _metadata_parts(e: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    meta = e.get("metadata") or {}
    if not isinstance(meta, dict):
        meta = {}
    page = meta.get("page") if isinstance(meta.get("page"), dict) else {}
    target = meta.get("target") if isinstance(meta.get("target"), dict) else {}
    return meta, page, target


def _flatten_event(e: dict[str, Any]) -> dict[str, Any]:
    meta, page, target = _metadata_parts(e)
    target_label = target.get("label") or target.get("title") or target.get("description") or target.get("help") or ""
    target_role = target.get("role") or target.get("localized_role") or target.get("tag") or ""
    return {
        "observed_at": e.get("observed_at"),
        "schema_version": e.get("schema_version", "1.0"),
        "organization_id": e.get("organization_id", ""),
        "actor_id": e.get("actor_id", ""),
        "device_id": e.get("device_id"),
        "sensor_id": e.get("sensor_id", ""),
        "source": e.get("source", ""),
        "session_id": e.get("session_id"),
        "app_or_surface": e.get("app"),
        "window_title": e.get("window_title"),
        "event_type": e.get("event_type"),
        "action": meta.get("action", ""),
        "page_host": page.get("hostname", ""),
        "page_path": page.get("pathname", ""),
        "target_label": target_label,
        "target_role": target_role,
        "duration_seconds": e.get("duration_seconds", 0),
        "metadata_json": json.dumps(meta, ensure_ascii=False, separators=(",", ":")),
    }


def _task_row(t: dict[str, Any]) -> dict[str, Any]:
    b = t.get("boundary") or {}
    return {
        "interpretation_status": "heuristic_not_ground_truth",
        "started_at": t.get("started_at"),
        "ended_at": t.get("ended_at"),
        "task_label": t.get("suggested_label"),
        "task_family": t.get("task_family"),
        "confidence": t.get("confidence"),
        "primary_surface": t.get("primary_surface"),
        "surfaces": " → ".join(str(x) for x in (t.get("surfaces") or [])),
        "elapsed_seconds": t.get("elapsed_seconds", 0),
        "foreground_seconds": t.get("foreground_seconds", 0),
        "engaged_seconds": t.get("engaged_seconds", 0),
        "idle_seconds": t.get("idle_seconds", 0),
        "active_input_seconds": t.get("active_input_seconds", 0),
        "keypress_count": t.get("keypress_count", 0),
        "click_count": t.get("click_count", 0),
        "scroll_count": t.get("scroll_count", 0),
        "boundary_reason": b.get("end_reason"),
        "boundary_confidence": b.get("confidence"),
        "effort_estimated": t.get("effort_estimated", False),
        "semantic_actions": " | ".join(str(x) for x in (t.get("semantic_actions") or [])),
    }


def _family_row(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "interpretation_status": "heuristic_not_ground_truth",
        "task_label": p.get("suggested_label"),
        "task_family": p.get("task_family") or p.get("signature"),
        "observed_count": p.get("observed_count", 0),
        "surfaces": " → ".join(str(x) for x in (p.get("surfaces") or [])),
        "surface_variant_count": p.get("surface_variant_count", 0),
        "total_engaged_seconds": p.get("total_engaged_seconds", 0),
        "median_engaged_seconds": p.get("median_engaged_seconds", 0),
        "p90_engaged_seconds": p.get("p90_engaged_seconds", 0),
        "median_elapsed_seconds": p.get("median_elapsed_seconds", 0),
        "keypress_count": p.get("keypress_count", 0),
        "click_count": p.get("click_count", 0),
        "completion_boundary_count": p.get("completion_boundary_count", 0),
        "confidence": p.get("confidence"),
    }


def _spreadsheet_safe_value(value: Any) -> Any:
    """Neutralize spreadsheet formulas without changing canonical JSON evidence."""
    if not isinstance(value, str):
        return value
    if value and value[0] in {"=", "+", "-", "@"}:
        return "'" + value
    return value


def csv_zip_bytes(payload: dict[str, Any]) -> bytes:
    files: dict[str, list[dict[str, Any]]] = {
        "effort_by_surface.csv": list(payload.get("effort_by_surface") or []),
        "transitions.csv": list(payload.get("transitions") or []),
        "inferred_tasks.csv": [_task_row(x) for x in (payload.get("inferred_tasks") or [])],
        "repeated_task_families.csv": [_family_row(x) for x in (payload.get("repeated_task_families") or [])],
        "operational_events.csv": [_flatten_event(x) for x in (payload.get("operational_events") or [])],
        "semantic_activity.csv": list(payload.get("operational_semantic_activity") or []),
    }
    if "raw_local_evidence" in payload:
        files["raw_local_evidence.csv"] = [_flatten_event(x) for x in payload.get("raw_local_evidence") or []]

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "README.txt",
            "OpenWorkGraph AI context package\n\n"
            + str(payload["export"].get("privacy_note") or "")
            + "\n\nStart with START_HERE.md, AI_GUIDE.md, and CAPTURE_MANIFEST.json.\n"
            + "Observed evidence is the primary record; inferred tasks and repeated families are heuristic convenience indexes.\n"
            + "Spreadsheet-leading formula characters are escaped in CSV/XLSX only; JSON evidence remains literal.\n"
            + "Each CSV represents one table from the captured session.\n",
        )
        zf.writestr("START_HERE.md", str(payload.get("starter_prompt_markdown") or AI_STARTER_PROMPT_MD))
        zf.writestr("AI_GUIDE.md", str(payload.get("ai_guide_markdown") or AI_DATA_DICTIONARY_MD))
        zf.writestr("README_FOR_AI.md", str(payload.get("ai_guide_markdown") or AI_DATA_DICTIONARY_MD))
        zf.writestr(
            "CAPTURE_MANIFEST.json",
            json.dumps(_jsonable(payload.get("capture_manifest") or {}), ensure_ascii=False, indent=2),
        )
        for filename, records in files.items():
            buf = io.StringIO(newline="")
            if records:
                keys: list[str] = []
                for record in records:
                    for key in record.keys():
                        if key not in keys:
                            keys.append(key)
                writer = csv.DictWriter(buf, fieldnames=keys)
                writer.writeheader()
                for record in records:
                    cooked = {}
                    for key in keys:
                        value = record.get(key)
                        value = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
                        cooked[key] = _spreadsheet_safe_value(value)
                    writer.writerow(cooked)
            zf.writestr(filename, buf.getvalue().encode("utf-8-sig"))
    return out.getvalue()


def _xlsx_value(v: Any) -> Any:
    if isinstance(v, (dict, list, tuple)):
        v = json.dumps(v, ensure_ascii=False, separators=(",", ":"))
    if v is None:
        return ""
    return _spreadsheet_safe_value(v)


def _flatten_mapping(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
    rows_out: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            rows_out.extend(_flatten_mapping(child, child_prefix))
    elif isinstance(value, list):
        rows_out.append((prefix, json.dumps(value, ensure_ascii=False)))
    else:
        rows_out.append((prefix, value))
    return rows_out


def xlsx_bytes(payload: dict[str, Any]) -> bytes:
    out = io.BytesIO()
    wb = xlsxwriter.Workbook(out, {
        "in_memory": True,
        "strings_to_formulas": False,
        "strings_to_urls": False,
    })
    wb.set_properties({
        "title": "OpenWorkGraph session export",
        "subject": "Captured workflow data",
        "comments": payload["export"]["privacy_note"],
    })

    title_fmt = wb.add_format({"bold": True, "font_size": 18})
    section_fmt = wb.add_format({"bold": True, "font_size": 12, "bg_color": "#E2E8F0"})
    header_fmt = wb.add_format({"bold": True, "font_color": "#FFFFFF", "bg_color": "#334155", "border": 1})
    text_fmt = wb.add_format({"valign": "top"})
    wrap_fmt = wb.add_format({"valign": "top", "text_wrap": True})
    num_fmt = wb.add_format({"num_format": "0.0", "valign": "top"})
    int_fmt = wb.add_format({"num_format": "0", "valign": "top"})

    overview = wb.add_worksheet("Overview")
    overview.hide_gridlines(2)
    overview.set_column("A:A", 30)
    overview.set_column("B:B", 72)
    overview.write("A1", "OpenWorkGraph session export", title_fmt)
    overview.write("A3", "Export", section_fmt)
    meta_rows = [
        ("Version", payload["export"].get("version")),
        ("Generated at", payload["export"].get("generated_at")),
        ("Scope", payload["export"].get("scope")),
        ("Run started at", payload["export"].get("run_started_at")),
        ("Raw local evidence included", str(payload["export"].get("include_raw_local_evidence"))),
        ("Evidence tables truncated", str(payload["export"].get("evidence_tables_truncated"))),
        ("Privacy note", payload["export"].get("privacy_note")),
        ("Interpretation note", payload["export"].get("interpretation_note")),
    ]
    for i, (key, value) in enumerate(meta_rows, start=4):
        overview.write(i - 1, 0, key, header_fmt if i == 4 else text_fmt)
        overview.write(i - 1, 1, _xlsx_value(value), wrap_fmt)

    op = payload.get("overview", {}).get("operational", {})
    overview.write("A13", "Operational summary", section_fmt)
    kpis = [
        ("Focus periods", op.get("focus_events", 0)),
        ("Engaged seconds", op.get("total_engaged_seconds", 0)),
        ("Idle seconds", op.get("total_idle_seconds", 0)),
        ("Key presses", op.get("keypress_count", 0)),
        ("Clicks", op.get("click_count", 0)),
        ("Browser semantic events", op.get("browser_semantic_events", 0)),
        ("Inferred tasks", len(payload.get("inferred_tasks") or [])),
        ("Repeated task families", len(payload.get("repeated_task_families") or [])),
    ]
    for i, (key, value) in enumerate(kpis, start=14):
        overview.write(i - 1, 0, key, text_fmt)
        overview.write(i - 1, 1, value, num_fmt if isinstance(value, float) else int_fmt)

    overview.write("A24", "How AI should read this", section_fmt)
    overview.write("A25", "Observed evidence", text_fmt)
    overview.write("B25", "Primary record of what OpenWorkGraph captured.", wrap_fmt)
    overview.write("A26", "Derived tasks/summaries", text_fmt)
    overview.write("B26", "Heuristic convenience indexes; verify against evidence and reconstruct workflow when needed.", wrap_fmt)
    overview.write("A27", "Browser semantic events = 0", text_fmt)
    overview.write("B27", "Means no browser-extension semantic events were observed; desktop-derived browser evidence may still be present.", wrap_fmt)
    overview.write("A28", "Full instructions", text_fmt)
    overview.write("B28", "Read the 'AI guide', 'Starter prompt', and 'Capture manifest' sheets.", wrap_fmt)

    def write_table(sheet_name: str, records: list[dict[str, Any]], *, widths: dict[str, int] | None = None) -> None:
        chunks = [records[i:i + EXCEL_DATA_ROWS_PER_SHEET] for i in range(0, len(records), EXCEL_DATA_ROWS_PER_SHEET)] or [[]]
        for part_number, chunk in enumerate(chunks, start=1):
            suffix = "" if len(chunks) == 1 else f" {part_number}"
            base = sheet_name[:31 - len(suffix)]
            ws = wb.add_worksheet((base + suffix)[:31])
            ws.freeze_panes(1, 0)
            ws.hide_gridlines(2)
            if not chunk:
                ws.write(0, 0, "No data captured for this table.")
                continue
            keys: list[str] = []
            for record in chunk:
                for key in record.keys():
                    if key not in keys:
                        keys.append(key)
            for col, key in enumerate(keys):
                ws.write(0, col, key, header_fmt)
                width = (widths or {}).get(key, min(40, max(12, len(key) + 2)))
                ws.set_column(col, col, width)
            for row_idx, record in enumerate(chunk, start=1):
                for col, key in enumerate(keys):
                    value = _xlsx_value(record.get(key))
                    if isinstance(value, bool):
                        ws.write_boolean(row_idx, col, value, text_fmt)
                    elif isinstance(value, (int, float)) and not isinstance(value, bool):
                        ws.write_number(row_idx, col, float(value), num_fmt if isinstance(value, float) else int_fmt)
                    else:
                        ws.write(row_idx, col, value, wrap_fmt if isinstance(value, str) and len(value) > 60 else text_fmt)
            ws.autofilter(0, 0, len(chunk), len(keys) - 1)

    guide_ws = wb.add_worksheet("AI guide")
    guide_ws.hide_gridlines(2)
    guide_ws.set_column("A:A", 110)
    for row_idx, line in enumerate(str(payload.get("ai_guide_markdown") or AI_DATA_DICTIONARY_MD).splitlines()):
        guide_ws.write(row_idx, 0, _xlsx_value(line), title_fmt if row_idx == 0 else wrap_fmt)

    prompt_ws = wb.add_worksheet("Starter prompt")
    prompt_ws.hide_gridlines(2)
    prompt_ws.set_column("A:A", 110)
    for row_idx, line in enumerate(str(payload.get("starter_prompt_markdown") or AI_STARTER_PROMPT_MD).splitlines()):
        prompt_ws.write(row_idx, 0, _xlsx_value(line), title_fmt if row_idx == 0 else wrap_fmt)

    manifest_ws = wb.add_worksheet("Capture manifest")
    manifest_ws.hide_gridlines(2)
    manifest_ws.set_column("A:A", 48)
    manifest_ws.set_column("B:B", 80)
    manifest_ws.write(0, 0, "Field", header_fmt)
    manifest_ws.write(0, 1, "Value", header_fmt)
    for row_idx, (key, value) in enumerate(_flatten_mapping(payload.get("capture_manifest") or {}), start=1):
        manifest_ws.write(row_idx, 0, _xlsx_value(key), text_fmt)
        manifest_ws.write(row_idx, 1, _xlsx_value(value), wrap_fmt)

    effort = list(payload.get("effort_by_surface") or [])
    write_table("Effort by surface", effort, widths={"surface": 24, "container_app": 22})
    write_table("Inferred tasks", [_task_row(x) for x in payload.get("inferred_tasks") or []], widths={"interpretation_status": 28, "task_label": 28, "task_family": 28, "surfaces": 38, "semantic_actions": 45})
    write_table("Repeated families", [_family_row(x) for x in payload.get("repeated_task_families") or []], widths={"interpretation_status": 28, "task_label": 28, "task_family": 30, "surfaces": 38})
    write_table("Transitions", list(payload.get("transitions") or []), widths={"from": 30, "to": 30})
    write_table("Operational events", [_flatten_event(x) for x in payload.get("operational_events") or []], widths={"app_or_surface": 24, "window_title": 28, "target_label": 36, "page_host": 26, "event_type": 22, "metadata_json": 55})
    write_table("Semantic activity", list(payload.get("operational_semantic_activity") or []), widths={"app": 24, "label": 40, "event_type": 24})
    if "raw_local_evidence" in payload:
        write_table("RAW local evidence", [_flatten_event(x) for x in payload.get("raw_local_evidence") or []], widths={"app_or_surface": 24, "window_title": 50, "target_label": 40, "page_host": 26, "event_type": 22, "metadata_json": 65})

    if effort:
        top = effort[:10]
        start_row = 31
        overview.write(start_row, 0, "Top work surfaces by engaged time", section_fmt)
        overview.write(start_row + 1, 0, "Surface", header_fmt)
        overview.write(start_row + 1, 1, "Engaged seconds", header_fmt)
        for i, item in enumerate(top, start=start_row + 2):
            overview.write(i, 0, _xlsx_value(item.get("surface", "")), text_fmt)
            overview.write_number(i, 1, float(item.get("engaged_seconds") or 0), num_fmt)
        chart = wb.add_chart({"type": "bar"})
        chart.add_series({
            "name": "Engaged seconds",
            "categories": ["Overview", start_row + 2, 0, start_row + 1 + len(top), 0],
            "values": ["Overview", start_row + 2, 1, start_row + 1 + len(top), 1],
        })
        chart.set_title({"name": "Engaged time by work surface"})
        chart.set_legend({"none": True})
        chart.set_x_axis({"name": "Seconds"})
        chart.set_size({"width": 720, "height": 360})
        overview.insert_chart(start_row, 3, chart)

    wb.close()
    return out.getvalue()


def export_filename(fmt: str, *, include_raw: bool) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = "-rich" if include_raw else "-normalized-only"
    ext = {"json": "json", "xlsx": "xlsx", "csvzip": "zip"}[fmt]
    return f"openworkgraph-session-{stamp}{suffix}.{ext}"
