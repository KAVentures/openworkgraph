"""Synthetic event data for cross-client OWG MCP evaluation.

These examples contain no actual customer files, people, email, credentials,
or tool messages. They must be loaded ONLY into a disposable test Gateway.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .score import load_cases


def event(event_id: str, when: datetime, *, title: str, app: str = "Browser",
          session: str = "session-main", metadata: dict[str, Any] | None = None,
          source: str = "desktop", event_type: str = "window_focus") -> dict[str, Any]:
    return {
        "event_id": event_id,
        "observed_at": when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "session_id": session, "app": app, "source": source,
        "event_type": event_type, "window_title": title,
        "duration_seconds": 0,
        "metadata": metadata or {},
    }


def fixture_events() -> list[dict[str, Any]]:
    now = datetime(2026, 10, 9, 10, 0, tzinfo=timezone.utc)
    old = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)
    rows = [
        event("evt-old-001", old, title="Customer onboarding checklist", session="old",
              metadata={"resource_reference": {"provider": "drive", "resource_ref": "owg:r:fixture-onboard", "resource_kind": "document"}}),
        event("evt-recent-001", now + timedelta(minutes=1), title="Jira open migration issue", session="recent"),
        event("evt-recent-002", now + timedelta(minutes=2), title="Editor review branch", app="Editor", session="recent"),
        event("evt-renewal-001", now - timedelta(days=1, minutes=10), title="Acme renewal review", session="renewal",
              metadata={"resource_reference": {"provider": "salesforce", "resource_ref": "owg:r:fixture-acme", "resource_kind": "record"}}),
        event("evt-untagged-004", now + timedelta(minutes=3), title="Procurement review: navigate", session="procure"),
        event("evt-unmatched-003", now + timedelta(minutes=4), title="Uncatalogued exception control: review", session="special"),
        event("evt-unrelated-001", now + timedelta(minutes=5), title="Jira issue alpha", session="adjacent",
              metadata={"tab_context_id": "tab-fixture", "resource_reference": {"provider": "jira", "resource_ref": "owg:r:fixture-issue", "resource_kind": "issue"}}),
        event("evt-unrelated-002", now + timedelta(minutes=5, seconds=10), title="Salesforce opportunity beta", session="adjacent",
              metadata={"tab_context_id": "tab-fixture", "resource_reference": {"provider": "salesforce", "resource_ref": "owg:r:fixture-opportunity", "resource_kind": "record"}}),
        event("evt-tuesday-001", datetime(2026, 10, 6, 9, 0, tzinfo=timezone.utc), title="Tuesday task opened", session="tuesday"),
        event("evt-tuesday-044", datetime(2026, 10, 6, 17, 0, tzinfo=timezone.utc), title="Tuesday task completed", session="tuesday"),
        event("evt-agent-001", now + timedelta(minutes=7), title="Agent attempted issue refactor", app="Agent",
              source="agent", event_type="agent_tool_call", session="agent-fixture",
              metadata={"action": "edit", "operation": "tool_call", "status": "unknown",
                        "tool": {"category": "write", "name": "synthetic_edit"},
                        "observation_level": "structural", "trace": {"run_id": "synthetic-run"}}),
        event("evt-jira-001", now + timedelta(minutes=8), title="Jira task for CRM handoff", session="handoff"),
        event("evt-price-001", now + timedelta(minutes=9), title="Acme pricing sheet", app="Sheets", session="pricing",
              metadata={"resource_reference": {"provider": "google_drive", "resource_ref": "owg:r:fixture-pricing", "resource_kind": "spreadsheet"}}),
        event("evt-approval-001", now + timedelta(minutes=10), title="Historical approval requested; state unknown", session="approval"),
    ]
    # Exercise date-bounded pagination rather than only labeling two endpoints
    # as a 44-step day. Preserve the original target IDs and timestamps.
    tuesday = datetime(2026, 10, 6, 9, 0, tzinfo=timezone.utc)
    for i in range(2, 44):
        rows.append(event(f"evt-tuesday-{i:03d}",
                          tuesday + timedelta(seconds=(i - 1) * 8 * 3600 / 43),
                          title="Tuesday task reviewed", session="tuesday"))
    for i in range(10):
        rows.append(event(f"evt-invoice-{i:03d}", now + timedelta(minutes=12, seconds=i),
                          title="Invoice review step", session=f"invoice-{i // 2}",
                          event_type="click"))
    for i, marker in enumerate(("evt-quote-a", "evt-quote-b")):
        rows.append(event(marker, now + timedelta(minutes=13 + i),
                          title="Quotation: open pricing then save draft", session=f"quote-{i}",
                          event_type="click", metadata={"action": "save_draft"}))
    # Older-target case should be genuinely outside the last 200. Filler is
    # harmless repetitive context; the model must not infer it is the only work.
    for i in range(220):
        rows.append(event(f"evt-filler-{i:03d}", now + timedelta(minutes=20, seconds=i),
                          title="Routine tab switch", session="filler"))
    return sorted(rows, key=lambda e: (e["observed_at"], e["event_id"]))


def main() -> None:
    parser = argparse.ArgumentParser(description="Export synthetic OWG evidence JSONL for an isolated test Gateway")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    rows = fixture_events()
    args.out.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    known = {row["event_id"] for row in rows}
    target = {id for case in load_cases() for id in case["target_event_ids"]}
    missing = target - known
    if missing:
        raise ValueError(f"missing fixture targets: {sorted(missing)}")


if __name__ == "__main__":
    main()
