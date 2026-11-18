"""Case filter: endpoints (first / last activity) and case duration, applied by every filtered view."""
from __future__ import annotations

from io import BytesIO

import pytest

from server import event_log_cohorts as cohorts
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _log():
    # c0..c9 start with Receive; c10..c11 start with Escalate. Case i lasts i hours.
    # Even cases end with Close, odd ones with Cancel.
    rows = ["case,activity,time"]
    for i in range(12):
        first = "Escalate" if i >= 10 else "Receive"
        last = "Close" if i % 2 == 0 else "Cancel"
        rows += [f"c{i},{first},2024-03-01T00:00:00Z", f"c{i},Work,2024-03-01T00:00:00Z",
                 f"c{i},{last},2024-03-01T{i:02d}:00:00Z"]
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="t", source_format="csv",
                                mapping=MAPPING)["log_id"]


def _cases(log, cohort):
    return workspace.summary(log, cohort=cohort)["cases"]


def test_endpoints_and_duration():
    log = _log()
    assert _cases(log, {"starts_with": "Escalate"}) == 2
    assert _cases(log, {"starts_with": "Work"}) == 0
    assert _cases(log, {"ends_with": "Cancel"}) == 6
    # Case 0 has three events at one instant: ordered by time, then event id, so it ends with Close.
    assert _cases(log, {"ends_with": "Close"}) == 6
    assert _cases(log, {"duration": {"min": 3 * 3600}}) == 9
    assert _cases(log, {"duration": {"max": 3 * 3600}}) == 3
    assert _cases(log, {"duration": {"min": 2 * 3600, "max": 6 * 3600}}) == 4
    combined = workspace.summary(log, cohort={"starts_with": "Receive", "ends_with": "Close", "duration": {"min": 3600}})
    assert combined["cases"] == 4 and combined["cases_before_filter"] == 12
    assert combined["cohort"]["duration"] == {"min": 3600.0} and combined["cohort"]["starts_with"] == "Receive"


@pytest.mark.parametrize("bad", [{"duration": {"min": -1}}, {"duration": {"max": "1"}},
                                 {"duration": {"min": 5, "max": 5}}, {"duration": {"min": True}},
                                 {"duration": {"low": 1}}, {"duration": [1, 2]}, {"starts_with": "x" * 201},
                                 {"ends_with": 3}])
def test_validation(bad):
    with pytest.raises(EventLogWorkspaceError):
        cohorts.parse_cohort(bad)


def test_describe_round_trips():
    spec = {"starts_with": "A", "ends_with": "B", "duration": {"min": 60, "max": 3600}}
    parsed = cohorts.parse_cohort(spec)
    assert cohorts.parse_cohort(cohorts.describe(parsed)) == parsed


def test_route_parameters(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
rows = ["case,activity,time"]
for i in range(6):
    rows += [f"c{i},{'X' if i < 2 else 'A'},2024-03-01T00:00:00Z", f"c{i},B,2024-03-01T0{i}:30:00Z"]
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    log_id = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time",
                         content="\n".join(rows).encode(), headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"}).json()["log_id"]
    base = f"/v1/event-logs/{log_id}/summary"
    get = lambda q: client.get(base + q, headers=owner)
    assert get("?starts_with=X").json()["cases"] == 2
    assert get("?ends_with=B&duration_min=7200").json()["cases"] == 4
    assert get("?duration_max=3600").json()["cases"] == 1
    assert get("?duration_min=7200&duration_max=7200").status_code == 422
    assert get("?duration_min=-1").status_code == 422
    r = client.get(f"/v1/event-logs/{log_id}/kpis?starts_with=A&duration_min=7200", headers=owner)
    assert r.status_code == 200 and r.json()["cases"] == 4, r.text
print("ok")
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "srv"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "srvauth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    root = Path(__file__).resolve().parents[1]
    done = subprocess.run([sys.executable, "-c", code], cwd=root, env=env, capture_output=True, text=True, timeout=180)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), f"stdout={done.stdout}\nstderr={done.stderr}"


def test_saved_filter_set_keeps_endpoints_and_duration():
    from server import event_log_filter_sets as filter_sets
    log = _log()
    filter_sets.save(log, {"name": "Slow escalations", "cohort": {"starts_with": "Escalate", "ends_with": "Gone",
                                                                  "duration": {"min": 3600}}})
    saved = filter_sets.list_sets(log)["filter_sets"][0]
    assert saved["cohort"] == {"starts_with": "Escalate", "ends_with": "Gone", "duration": {"min": 3600.0}}
    assert saved["activities_not_in_log"] == ["Gone"]
