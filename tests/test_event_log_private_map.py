"""Differentially private process map: bounded contribution, Laplace noise, nothing case-level released."""
from __future__ import annotations

import random
import statistics
from io import BytesIO

import pytest

from server import event_log_private_map as private
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time", "resource_column": "who"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _log(n=400):
    rows = ["case,activity,time,who"]
    for i in range(n):
        rows += [f"secret-case-{i},Receive,2024-03-01T08:00:00Z,alice", f"secret-case-{i},Check,2024-03-01T09:00:00Z,bob",
                 f"secret-case-{i},{'Approve' if i % 4 else 'Reject'},2024-03-01T10:00:00Z,carol"]
    rows += ["zz-one-off,Receive,2024-03-02T08:00:00Z,dave", "zz-one-off,Audit by Dave,2024-03-02T09:00:00Z,dave"]
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="claims", source_format="csv",
                                mapping=MAPPING)["log_id"]


class _Seeded(random.Random):
    """A seeded stand-in for SystemRandom so tests are repeatable."""


def test_counts_are_close_and_rare_paths_hidden():
    log = _log()
    result = private.private_map(log, epsilon=2.0, max_transitions_per_case=5, _rng=_Seeded(7))
    edges = {(e["from"], e["to"]): e["count"] for e in result["edges"]}
    scale = result["privacy"]["noise"]["edge_scale"]
    assert scale == pytest.approx(5.0)
    assert abs(edges[("Receive", "Check")] - 400) < 10 * scale
    assert abs(edges[("Check", "Approve")] - 300) < 10 * scale
    assert ("(start)", "Receive") in edges and ("Approve", "(end)") in edges
    # No edge that nobody took is released (expected 0.05 such edges per export).
    assert set(edges) <= {("(start)", "Receive"), ("Receive", "Check"), ("Check", "Approve"), ("Check", "Reject"),
                          ("Approve", "(end)"), ("Reject", "(end)")}
    # The single rare case's path stays below the release threshold.
    assert ("Receive", "Audit by Dave") not in edges
    assert "Audit by Dave" not in {a["activity"] for a in result["activities"]}


def test_nothing_case_level_is_released():
    log = _log(60)
    shared = private.shareable(private.private_map(log, _rng=_Seeded(1)))
    text = str(shared)
    for secret in ("secret-case", "alice", "bob", "2024-03", "zz-one-off"):
        assert secret not in text
    assert "owner_only_not_private" not in shared
    assert shared["privacy"]["activity_names_protected"] is False


def test_noise_is_unbiased_laplace():
    rng = _Seeded(3)
    draws = [private._laplace(rng, 2.0) for _ in range(20000)]
    assert abs(statistics.fmean(draws)) < 0.1
    # Laplace(b) has mean absolute deviation b.
    assert statistics.fmean(abs(x) for x in draws) == pytest.approx(2.0, rel=0.05)


def test_contribution_bound_clips_long_cases():
    rows = ["case,activity,time"] + [f"long,A{i % 3},2024-03-01T{i:02d}:00:00Z" for i in range(20)]
    log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="t", source_format="csv",
                               mapping={k: v for k, v in MAPPING.items() if k != "resource_column"})["log_id"]

    class NoNoise(_Seeded):
        pass
    result = private.private_map(log, epsilon=1.0, max_transitions_per_case=4, threshold=0, _rng=NoNoise(0))
    assert result["owner_only_not_private"] == {"cases": 1, "cases_clipped": 1}


def test_fresh_noise_each_export_and_validation():
    log = _log(400)
    a = private.private_map(log, epsilon=2.0, max_transitions_per_case=5)["edges"]
    b = private.private_map(log, epsilon=2.0, max_transitions_per_case=5)["edges"]
    assert a and b and a != b
    for kwargs in ({"epsilon": 0}, {"epsilon": 11}, {"epsilon": True}, {"max_transitions_per_case": 0},
                   {"max_transitions_per_case": 101}, {"max_transitions_per_case": 2.5}, {"threshold": -1}):
        with pytest.raises(EventLogWorkspaceError):
            private.private_map(log, **kwargs)


def test_spurious_edges_are_rare_over_many_exports():
    log = _log(100)
    observed = {("(start)", "Receive"), ("Receive", "Check"), ("Check", "Approve"), ("Check", "Reject"),
                ("Approve", "(end)"), ("Reject", "(end)"), ("Receive", "Audit by Dave"), ("Audit by Dave", "(end)")}
    rng = _Seeded(11)
    spurious = sum(sum((e["from"], e["to"]) not in observed for e in private.private_map(log, _rng=rng)["edges"])
                   for _ in range(200))
    assert spurious <= 30   # expected about 0.05 per export, so about 10 in 200


def test_route_is_dashboard_only_post(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
rows = ["case,activity,time"]
for i in range(300):
    rows += [f"c{i},A,2024-03-01T08:00:00Z", f"c{i},B,2024-03-01T09:00:00Z"]
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    log_id = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time",
                         content="\n".join(rows).encode(), headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"}).json()["log_id"]
    path = f"/v1/event-logs/{log_id}/private-map"
    assert client.post(path, json={}, headers=bearer).status_code == 403
    assert client.get(path, headers=owner).status_code == 405
    r = client.post(path, json={"epsilon": 2, "max_transitions_per_case": 3}, headers=owner)
    body = r.json()
    assert r.status_code == 200 and ("A", "B") in {(e["from"], e["to"]) for e in body["edges"]}, r.text
    assert "c1" not in str(body["edges"]) and body["privacy"]["max_transitions_per_case"] == 3
    assert client.post(path, json={"epsilon": 50}, headers=owner).status_code == 422
    assert client.post(path, json={"names": "x"}, headers=owner).status_code == 422
print("ok")
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "srv"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "srvauth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    root = Path(__file__).resolve().parents[1]
    done = subprocess.run([sys.executable, "-c", code], cwd=root, env=env, capture_output=True, text=True, timeout=180)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), f"stdout={done.stdout}\nstderr={done.stderr}"
