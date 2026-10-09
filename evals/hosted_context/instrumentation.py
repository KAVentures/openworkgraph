"""Minimal portable recorder for REAL client/MCP tool calls during evaluation.

Wrap a client's actual MCP invocation, call record(...) on tool responses, and
save the trace. No raw tool payload, personal metadata, URL, token or model
answer is written to the trace. Use ONLY synthetic evidence fixtures.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .score import TOOL_NAMES

_SAFE_ARGUMENTS = frozenset({"limit", "max_runs", "min_runs", "detail", "since", "until", "query", "cursor"})


def _ids_from(value: Any) -> set[str]:
    found: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if key == "event_id" and isinstance(child, str):
                    found.add(child)
                elif key in ("rows", "canonical_evidence", "events", "result", "structuredContent", "content"):
                    visit(child)
                elif isinstance(child, dict) or isinstance(child, list):
                    visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, str):
            if item.lstrip().startswith(("{", "[")):
                try:
                    visit(json.loads(item))
                except (ValueError, TypeError):
                    pass

    visit(value)
    return found


def _argument_summary(arguments: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for name, value in arguments.items():
        if name not in _SAFE_ARGUMENTS:
            # Never persist execution IDs, search values, or arbitrary arguments
            # from customer tool calls in benchmark files.
            continue
        if name in {"query", "cursor", "since", "until"}:
            result[name] = "<present>" if str(value or "").strip() else ""
        elif name == "detail" and value in ("compact", "rich"):
            result[name] = value
        elif name in {"limit", "max_runs", "min_runs"} and type(value) is int:
            result[name] = value
    return result


class MCPTraceRecorder:
    def __init__(self, *, case_id: str, client: str, model: str, trial: str):
        self.case_id = case_id
        self.client = client
        self.model = model
        self.trial = trial
        self.calls: list[dict[str, Any]] = []

    def record(self, *, name: str, arguments: dict[str, Any], response: Any) -> Any:
        """Record an actual returned tool payload while preserving it for the caller."""
        if name not in TOOL_NAMES:
            raise ValueError("unexpected tool")
        if not isinstance(arguments, dict):
            raise TypeError("arguments must be a dict")
        compact = json.dumps(response, ensure_ascii=False, default=str)
        self.calls.append({
            "name": name,
            "arguments": _argument_summary(arguments),
            "event_ids": sorted(_ids_from(response)),
            "response_chars": len(compact),
        })
        return response

    def append_jsonl(self, path: Path) -> None:
        item = {
            "case_id": self.case_id,
            "client": self.client,
            "model": self.model,
            "trial": self.trial,
            "harness_recorded": True,
            "tool_calls": self.calls,
        }
        # Output contains only opaque event IDs and low-risk structural args.
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
