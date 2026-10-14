#!/usr/bin/env python3
"""Opt-in realistic local OWG ingestion/query benchmark (no personal data).

Run from repository root:
    python scripts/bench_local_owg_scale.py --sizes 10000 50000

Uses temporary isolated SQLite data, the REAL insert_events title/privacy
pipeline, Context Pulse, agent-run selection and repeated-workflow discovery.
It never reads an installed user's work or connects to a hosted gateway.
Reports actual query timings, table counts and database bytes; intentionally
does not impose a hardware-independent pass/fail latency threshold.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import time
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(size: int, folder: Path) -> dict[str, object]:
    os.environ["WORKFLOW_OBSERVER_DATA"] = str(folder)
    os.environ["WORKFLOW_OBSERVER_AUTH_DIR"] = str(folder / "auth")
    os.environ["WORKFLOW_OBSERVER_CONFIG"] = str(folder / "config.json")
    # Import AFTER selecting an isolated directory. Never import installed
    # user's DB or rewrite their privacy/retention settings.
    from server import db, context_pulse, workflow_evidence, procedural_memory
    from server.agent_execution_traces import agent_execution_traces
    from mcp_server.continuity import build_continuity_context

    folder.mkdir(parents=True, exist_ok=True)
    db.DATA_DIR = folder
    db.DB_PATH = folder / "workflow_observer.db"
    start = time.perf_counter()
    db.init_db()
    init_s = time.perf_counter() - start
    base = datetime(2026, 10, 8, tzinfo=timezone.utc)
    apps = ("Gmail", "Microsoft Excel", "Fortnox", "Gmail",
            "Google Docs", "GitHub", "Slack", "Google Chrome")

    start = time.perf_counter()
    inserted = 0
    batch: list[dict[str, object]] = []
    for i in range(size):
        app = apps[i % len(apps)]
        is_agent = (i % 100 == 0)
        at = (base + timedelta(seconds=i * 8)).isoformat()
        if is_agent:
            run_no = i // 100
            metadata = {
                "source": "agent",
                "operation": "run_started" if run_no % 2 == 0 else "run_finished",
                "status": "running" if run_no % 2 == 0 else "unknown",
                "agent": {"name": "Claude Code", "provider": "anthropic",
                          "framework": "claude_code"},
            }
        else:
            metadata = {
                "source": "desktop",
                "page": {"surface": app, "title": f"Invoice {i // 8} | {app}"},
                "action": "copy" if i % 8 == 0 else "focus_control",
                "privacy": {"clipboard_contents": False},
            }
        batch.append({
            "event_id": f"bench-{i:07d}",
            "observed_at": at,
            "device_id": "benchmark-only",
            "session_id": f"session-{i // 800:04d}",
            "source": "agent" if is_agent else "desktop",
            "app": "" if is_agent else app,
            "window_title": "" if is_agent else f"Invoice {i // 8} - {app}",
            "event_type": ("agent_run_started" if metadata.get("operation") == "run_started"
                           else "agent_run_finished") if is_agent else "focus_span",
            "duration_seconds": 8.0,
            "metadata": metadata,
        })
        if len(batch) >= 500:
            inserted += db.insert_events(batch)
            batch.clear()
    if batch:
        inserted += db.insert_events(batch)
    insert_seconds = time.perf_counter() - start

    timings: dict[str, float] = {}
    start = time.perf_counter()
    pulse = context_pulse.context_pulse(recent_limit=120, finding_limit=0,
                                        recent_detail="rich")
    timings["context_pulse_no_findings"] = round(time.perf_counter() - start, 3)
    start = time.perf_counter()
    continuity = build_continuity_context(pulse.get("recent_evidence") or [], [],
                                          max_resources=24)
    timings["continuity_projection"] = round(time.perf_counter() - start, 3)
    start = time.perf_counter()
    agent_events = procedural_memory.load_recent_evidence(
        limit=25000, agent_only=True,
    )
    traces = agent_execution_traces(agent_events, limit=20, max_events_per_execution=1)
    timings["agent_run_discovery"] = round(time.perf_counter() - start, 3)
    start = time.perf_counter()
    patterns = workflow_evidence.list_workflow_evidence_candidates(
        source_event_limit=25000, min_runs=2, limit=8,
    )
    timings["repeated_workflows"] = round(time.perf_counter() - start, 3)

    with db.connect() as conn:
        table_counts = {
            name: conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            for name in ("events", "normalized_events", "context_events")
        }
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        size_bytes = int(conn.execute("PRAGMA page_count").fetchone()[0]) * int(
            conn.execute("PRAGMA page_size").fetchone()[0]
        )
    return {
        "events_requested": size,
        "events_inserted": inserted,
        "database_bytes": size_bytes,
        "bytes_per_canonical_event_including_derived_tables": round(size_bytes / max(1, inserted), 1),
        "rows_by_table": table_counts,
        "initialization_seconds": round(init_s, 3),
        "ingestion_seconds": round(insert_seconds, 3),
        "query_seconds": timings,
        "agent_events_seen": len(agent_events),
        "agent_runs_returned": int(traces.get("returned") or 0),
        "workflow_clusters_returned": int(patterns.get("returned") or 0),
        "continuity_resources_returned": len(continuity.get("resources") or []),
        "synthetic_only": True,
        "mac_capture_and_transport_not_tested": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", nargs="+", type=int, default=[10000, 50000])
    args = parser.parse_args()
    if not args.sizes or any(value < 1 or value > 50000 for value in args.sizes):
        parser.error("--sizes must contain integers in range 1..50000")
    # Each sample uses a fresh temporary directory; the app's real data is
    # never mounted or inspected.
    results = []
    for size in args.sizes:
        with tempfile.TemporaryDirectory(prefix="owg-bench-") as dirname:
            results.append(run(size, Path(dirname)))
    print(json.dumps({"benchmark": "local-owg-endpoint-components",
                      "results": results}, indent=2))


if __name__ == "__main__":
    main()
