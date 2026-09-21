from __future__ import annotations

"""Additive context packaging layered on top of the v0.50 exporter.

The base exporter remains authoritative for all existing JSON/XLSX/CSV package
behavior. This module adds regeneratable context views without rewriting raw
evidence or replacing the canonical AI guide/capture manifest.
"""

import csv
import io
import json
import zipfile
from typing import Any

from . import exporter as base
from .context_layers import candidate_tasks

AI_DATA_DICTIONARY_MD = base.AI_DATA_DICTIONARY_MD
AI_STARTER_PROMPT_MD = base.AI_STARTER_PROMPT_MD


def build_export_payload(*, scope: str = "current", include_raw: bool = False) -> dict[str, Any]:
    payload = base.build_export_payload(scope=scope, include_raw=include_raw)
    since = base._since_for_scope(scope)
    task_data = candidate_tasks(limit=100000, since=since)

    payload["inferred_tasks"] = task_data.get("tasks", [])
    payload["repeated_task_families"] = task_data.get("patterns", [])
    payload["task_inference"] = task_data.get("inference", {})
    payload["agent_turns"] = task_data.get("agent_turns", [])
    payload["factual_context_timeline"] = task_data.get("factual_context", [])
    payload["export"]["derived_context_version"] = "v1"
    payload["export"]["raw_evidence_mutated_by_context_layer"] = False
    payload["export"]["xlsx_context_note"] = (
        "The XLSX layout remains the v0.50-compatible workbook. "
        "Agent-turn and factual-context tables are available in JSON, CSV ZIP, and MCP."
    )
    return payload


def json_bytes(payload: dict[str, Any]) -> bytes:
    return base.json_bytes(payload)


def export_filename(fmt: str, *, include_raw: bool) -> str:
    return base.export_filename(fmt, include_raw=include_raw)


def xlsx_bytes(payload: dict[str, Any]) -> bytes:
    # Preserve the exact v0.50 workbook contract, including AI guide/prompt/manifest
    # sheets and every existing evidence sheet.
    return base.xlsx_bytes(payload)


def _csv_bytes(records: list[dict[str, Any]]) -> bytes:
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
            cooked: dict[str, Any] = {}
            for key in keys:
                value = record.get(key)
                cooked[key] = (
                    json.dumps(value, ensure_ascii=False)
                    if isinstance(value, (dict, list))
                    else value
                )
            writer.writerow(cooked)
    return buf.getvalue().encode("utf-8-sig")


def _task_evidence_rows(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for task in tasks:
        rows.append(
            {
                "interpretation_status": "derived_regeneratable_not_ground_truth",
                "task_id": task.get("task_id", ""),
                "started_at": task.get("started_at"),
                "ended_at": task.get("ended_at"),
                "task_label": task.get("suggested_label"),
                "task_family": task.get("task_family"),
                "agent_turn_count": task.get("agent_turn_count", 0),
                "agent_turn_ids": task.get("agent_turn_ids") or [],
                "outcomes": task.get("outcomes") or [],
                "evidence_window": task.get("evidence_window") or {},
                "anchor_event_ids": task.get("anchor_event_ids") or [],
                "context_layer": task.get("context_layer", ""),
            }
        )
    return rows


def csv_zip_bytes(payload: dict[str, Any]) -> bytes:
    """Preserve the v0.50 package byte-for-byte for existing entries, then append context."""
    out = io.BytesIO()
    out.write(base.csv_zip_bytes(payload))
    with zipfile.ZipFile(out, "a", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "agent_turns.csv",
            _csv_bytes(list(payload.get("agent_turns") or [])),
        )
        zf.writestr(
            "factual_context_timeline.csv",
            _csv_bytes(list(payload.get("factual_context_timeline") or [])),
        )
        zf.writestr(
            "task_evidence.csv",
            _csv_bytes(_task_evidence_rows(list(payload.get("inferred_tasks") or []))),
        )
    return out.getvalue()
