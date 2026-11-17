"""The owned loopback dashboard, seeded with synthetic AI agent runs through the validated ingest path."""
from datetime import datetime, timedelta, timezone

from process_logs_app import app  # noqa: F401  (sets up isolated data and the dashboard session)
from server.agent_ingest import ingest_agent_payloads
from server.db import init_db

init_db()

start = datetime.now(timezone.utc) - timedelta(days=2)
payloads = []
for i in range(10):
    fail = i % 4 == 0  # runs 0, 4 and 8: only the Reviewer agent fails
    common = {"agent_name": "Coder" if i % 2 else "Reviewer", "provider": "test", "framework": "custom-agent",
              "observation_level": "native_trace", "run_id": f"synthetic-run-{i}", "session_id": f"synthetic-session-{i}"}
    steps = [("run_started", "running", {}),
             ("tool_call", "success", {"tool_name": "Grep", "tool_category": "search"}),
             ("tool_call", "error" if fail else "success", {"tool_name": "Bash", "tool_category": "code"}),
             ("run_finished", "error" if fail else "success", {})]
    for n, (operation, status, extra) in enumerate(steps):
        payloads.append({**common, "event_id": f"synthetic-{i}-{n}", "operation": operation, "status": status,
                         "observed_at": (start + timedelta(hours=i, minutes=n * (10 if fail else 1))).isoformat(), **extra})
print("seeded", ingest_agent_payloads(payloads))
