"""Strong repeated-work candidates outrank frequent, weak single-app noise."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from server.workflow_candidates import cluster_runs


def _run(i: int, steps: list[str]) -> dict:
    return {
        "execution_id": f"execution:{i:016x}",
        "actor_kind": "human",
        "structural_steps": steps,
        "semantic_steps": [s.removeprefix("surface:") for s in steps],
        "started_at": f"2026-10-13T08:00:00+00:00",
        "duration_seconds": 720,
    }


def test_quality_filtered_before_cluster_budget():
    weak = [
        _run(i, [f"surface:low{i:04d}", "action:focus"]) for i in range(150)
        for _ in (0, 1)
    ]
    # Distinct IDs for repeated runs, otherwise tests simulate overlapping
    # observations for the same event rather than independent executions.
    for i, row in enumerate(weak):
        row["execution_id"] = f"execution:{2000 + i:016x}"
    strong = [
        _run(10000 + i, ["surface:gmail", "action:copy",
                         "surface:excel", "action:paste",
                         "surface:fortnox", "action:save"])
        for i in range(3)
    ]
    filtered = cluster_runs(weak + strong, min_runs=2, min_cluster_quality=0.45)
    assert any(row["execution_count"] == 3 and "surface:fortnox" in row["core_structural_steps"]
               for row in filtered)


def test_local_mcp_has_safe_quality_default_and_explicit_unfiltered_option():
    code = r'''
from mcp_server import compact
params_seen=[]
compact.core._begin=lambda *_:None
compact.core._finish=lambda _name,x:x
def fake_get(path,params):
    assert path == "/v1/workflow-evidence/families"
    params_seen.append(params)
    return {"families":[],"candidate_clusters":[],
            "source_event_rows_considered":12000,"source_limit_reached":True}
compact.secure_runtime.secure_get=fake_get
ranked=compact.find_repeated_workflows()
assert params_seen[-1]["min_cluster_quality"] == 0.0
assert params_seen[-1]["rank_by_navigation"] is True
assert params_seen[-1]["navigation_summary_only"] is True
assert "require_multi_surface" not in params_seen[-1]
assert "require_nonzero_duration" not in params_seen[-1]
assert ranked["quality_filter_applied"] is False
assert ranked["navigation_ranking_applied"] is True
assert ranked["source_limit_reached"] is True
unfiltered=compact.find_repeated_workflows(include_low_specificity=True)
assert "min_cluster_quality" not in params_seen[-1]
assert "rank_by_navigation" not in params_seen[-1]
assert not unfiltered["quality_filter_applied"]
assert not unfiltered["navigation_ranking_applied"]
'''
    root=Path(__file__).resolve().parents[1]
    run=subprocess.run([sys.executable,"-c",code],cwd=root,
                       capture_output=True,text=True,timeout=30)
    assert run.returncode==0,run.stderr
