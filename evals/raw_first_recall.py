"""Deterministic raw-only vs inferred-only vs combined-retrieval recall audit.

Run with: python -m evals.raw_first_recall
These fixtures contain only synthetic data. They test discoverability, not
semantic correctness, production latency, or real-world model recall.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from tempfile import TemporaryDirectory
from pathlib import Path

from gateway.auth import Principal
from gateway.db import GatewayDB
from gateway.goal_context import build_goal_context, gateway_goal_context
from gateway.query import workflow_trace
from shared.evidence import rich_evidence_row


def _raw_scan(db: GatewayDB, *, org: str, actor: str) -> set[str]:
    """Paginate all permitted raw events; never condition on candidate quality."""
    cursor = None
    found: set[str] = set()
    while True:
        page = workflow_trace(db, organization_id=org, actor_id=actor,
                              cursor=cursor, limit=200)
        found.update(str(row.get("event_id")) for row in page["rows"])
        cursor = page.get("next_cursor")
        if not cursor:
            break
    return found


def _inference_only(db: GatewayDB, *, org: str, actor: str, goal: str) -> set[str]:
    rows = db.recent_rows(organization_id=org, actor_id=actor, limit=500)
    evidence = [rich_evidence_row(row, include_identity=False) for row in rows]
    context = build_goal_context(goal, evidence)
    return {str(example["anchor_event_id"]) for example in context["examples"]}


def _combined(db: GatewayDB, *, org: str, actor: str, goal: str) -> set[str]:
    """Model a client that never lets candidate recall gate canonical evidence."""
    context = gateway_goal_context(db, organization_id=org, actor_id=actor,
                                   goal=goal)
    ids = {str(row["event_id"]) for row in context["recent_raw_evidence"]}
    ids.update(str(example["anchor_event_id"]) for example in context["examples"])
    # A robust client *explicitly* pages the raw trace. MCP alone cannot force
    # a model to follow this, so the measured score belongs to this policy,
    # not automatically to every real-world ChatGPT/Claude invocation.
    ids.update(_raw_scan(db, org=org, actor=actor))
    return ids


def run_benchmark() -> dict:
    with TemporaryDirectory() as tmp:
        db = GatewayDB("sqlite:///" + str(Path(tmp) / "recall.sqlite"))
        db.init()
        org = "oauth-sub:synthetic-owner"
        actor = org
        principal = Principal("device", "device", org, actor, "synthetic-device",
                              frozenset())
        now = datetime.now(timezone.utc)
        earlier = now - timedelta(days=4)

        # Goal A appears only outside the last 500 events. Goal B is recent.
        # Goal C has many old matches and is beyond the former 180-row cutoff.
        rows = [
            {"event_id": f"old-quote-{i:03d}",
             "observed_at": (earlier + timedelta(seconds=i)).isoformat(),
             "session_id": "synthetic-project", "source": "browser",
             "app": "Chrome", "event_type": "browser_click",
             "window_title": "Quotation milestone" if i == 250 else "Quotation draft"}
            for i in range(251)
        ]
        rows.extend([
            {"event_id": f"filler-{i:04d}",
             "observed_at": (now - timedelta(seconds=1000-i)).isoformat(),
             "session_id": "filler-session", "source": "desktop",
             "app": "Chrome", "event_type": "screen_scroll",
             "window_title": "Generic browser"} for i in range(700)
        ])
        rows.extend([
            {"event_id": "recent-gh", "observed_at": (now-timedelta(seconds=3)).isoformat(),
             "session_id": "synthetic-cross-app", "source": "browser",
             "app": "Chrome", "event_type": "browser_click",
             "window_title": "Gmail quotation"},
            {"event_id": "recent-sheets", "observed_at": (now-timedelta(seconds=2)).isoformat(),
             "session_id": "synthetic-cross-app", "source": "browser",
             "app": "Chrome", "event_type": "browser_click",
             "window_title": "Sheets quotation"},
        ])
        db.insert_events(principal, rows)
        cases = [
            ("older_work", "Find quotation milestone", "old-quote-250"),
            ("new_work", "Continue Gmail quotation", "recent-gh"),
            ("cross_app", "Find Sheets quotation", "recent-sheets"),
        ]
        raw = _raw_scan(db, org=org, actor=actor)
        output = []
        for name, goal, target in cases:
            inferred = _inference_only(db, org=org, actor=actor, goal=goal)
            combined = _combined(db, org=org, actor=actor, goal=goal)
            output.append({
                "case": name, "target": target,
                "raw_only_hit": target in raw,
                "inference_only_hit": target in inferred,
                "combined_with_explicit_raw_fallback_hit": target in combined,
            })
        return {
            "cases": output,
            "raw_only_recall": sum(x["raw_only_hit"] for x in output) / len(output),
            "inference_only_recall": sum(x["inference_only_hit"] for x in output) / len(output),
            "combined_recall": sum(x["combined_with_explicit_raw_fallback_hit"] for x in output) / len(output),
            "raw_fallback_executed_by_test_policy": True,
            "evaluation_is_synthetic": True,
            "real_world_model_recall_claimed": False,
        }


if __name__ == "__main__":
    print(json.dumps(run_benchmark(), indent=2))
