"""Unusual cases: transparent signals that rank cases for review."""
from __future__ import annotations

from io import BytesIO

import pytest

from server import event_log_unusual_cases as uc
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _import(cases):
    """``cases``: list of (case_id, [(activity, minutes from start)]); case i starts on day i."""
    rows = ["case,activity,time"]
    for i, (case, steps) in enumerate(cases):
        for activity, minutes in steps:
            total = i * 24 * 60 + minutes
            day, rest = divmod(total, 24 * 60)
            rows.append(f"{case},{activity},2024-{1 + day // 28:02d}-{1 + day % 28:02d}T{rest // 60:02d}:{rest % 60:02d}:00Z")
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="u", source_format="csv",
                                mapping=MAPPING)["log_id"]


def _normal(i):
    gap = 60 + (i % 5) * 10
    return (f"n{i:03d}", [("Open", 0), ("Review", gap), ("Close", 2 * gap)])


def test_each_signal_is_found_and_explained():
    cases = [_normal(i) for i in range(100)]
    cases.append(("slow", [("Open", 0), ("Review", 60), ("Close", 60 * 24 * 9)]))
    cases.append(("loop", [("Open", 0)] + [(a, 10 * k + 10) for k, a in enumerate(["Review", "Fix"] * 6)] + [("Close", 200)]))
    cases.append(("odd", [("Open", 0), ("Escalate", 30), ("Review", 60), ("Close", 120)]))
    log = _import(cases)
    r = uc.unusual_cases(log)
    by_id = {c["case_id"]: c for c in r["top"]}
    signals = lambda cid: {x["signal"] for x in by_id[cid]["reasons"]}  # noqa: E731
    assert {"unusually_slow"} <= signals("slow")
    assert {"heavy_rework", "unusually_long", "rare_path"} <= signals("loop")
    assert {"rare_activity", "rare_path"} <= signals("odd")
    assert not any(cid.startswith("n") for cid in by_id)
    assert r["unusual_cases"] == 3 and r["cases"] == 103 and r["baseline"]["median_events"] == 3
    odd = next(x for x in by_id["odd"]["reasons"] if x["signal"] == "rare_activity")
    assert odd["detail"] == "contains Escalate (1 cases)"
    assert by_id["loop"]["score"] >= by_id["slow"]["score"]
    assert all(0 < x["strength"] <= 1 for c in r["top"] for x in c["reasons"])


def test_reason_filter_pages_ranked_matches_without_changing_baseline():
    cases = [_normal(i) for i in range(50)]
    cases += [(f"odd-{i:02d}", [("Open", 0), (f"Escalate {i}", 60), ("Close", 120)]) for i in range(30)]
    log = _import(cases)
    baseline = uc.unusual_cases(log)
    first = uc.unusual_cases(log, signal="rare_activity", limit=25)
    assert first["baseline"] == baseline["baseline"]
    assert first["by_signal"] == baseline["by_signal"] and first["unusual_cases"] == baseline["unusual_cases"]
    assert first["page"] == {"signal": "rare_activity", "total": 30, "offset": 0, "limit": 25, "next_offset": 25}
    second = uc.unusual_cases(log, signal="rare_activity", offset=first["page"]["next_offset"])
    assert second["page"]["next_offset"] is None
    assert [case["case_id"] for case in first["top"] + second["top"]] == [f"odd-{i:02d}" for i in range(30)]
    assert uc.unusual_cases(log, signal="heavy_rework")["top"] == []
    assert uc.unusual_cases(log, signal="rare_activity", offset=60)["page"]["next_offset"] is None


@pytest.mark.parametrize("options", [{"signal": "none"}, {"signal": []}, {"offset": -1}, {"offset": True}])
def test_rejects_invalid_review_filters(options):
    with pytest.raises(EventLogWorkspaceError):
        uc.unusual_cases("not-read", **options)


def test_case_ids_can_be_withheld_and_limits_hold():
    cases = [_normal(i) for i in range(30)] + [("odd", [("Open", 0), ("Escalate", 5), ("Close", 9)])]
    log = _import(cases)
    r = uc.unusual_cases(log, include_case_ids=False, limit=1)
    assert len(r["top"]) == 1 and "case_id" not in r["top"][0] and r["case_ids_included"] is False
    with pytest.raises(EventLogWorkspaceError, match="limit"):
        uc.unusual_cases(log, limit=0)
    small = _import([_normal(i) for i in range(5)])
    with pytest.raises(EventLogWorkspaceError, match="at least 20 cases"):
        uc.unusual_cases(small)


def test_agents_get_case_ids_only_at_the_cases_level(tmp_path, monkeypatch):
    from server import event_log_agent_sharing as sharing
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path / "auth"))
    (tmp_path / "auth").mkdir()
    cases = [_normal(i) for i in range(30)] + [("odd", [("Open", 0), ("Escalate", 5), ("Close", 9)])]
    log = _import(cases)
    with pytest.raises(sharing.NotShared):
        sharing.unusual_cases(log)
    sharing.set_share(log, "aggregates")
    aggregate = sharing.unusual_cases(log)
    assert aggregate["unusual_cases"] == 1 and aggregate["case_ids_included"] is False
    assert "odd" not in str(aggregate["top"]) and "path" not in aggregate["top"][0]
    sharing.set_share(log, "cases")
    detailed = sharing.unusual_cases(log)
    assert detailed["top"][0]["case_id"] == "odd" and detailed["top"][0]["path"] == ["Open", "Escalate", "Close"]


def test_unique_paths_are_not_unusual_when_most_paths_are_unique():
    letters = "BCDEFGHIJK"
    cases = [(f"v{i:02d}", [("A", 0), (letters[i % 10], 60), (letters[(i // 10) % 10], 120), ("Z", 180)])
             for i in range(60)]
    log = _import(cases)
    r = uc.unusual_cases(log)
    assert r["baseline"]["distinct_paths"] == 60
    assert "rare_path" not in r["by_signal"]


def test_csv_worklist_has_every_unusual_case_beyond_one_page():
    import csv
    from io import StringIO
    cases = [_normal(i) for i in range(300)]
    cases += [(f"=odd{k}", [("Open", 0), (f"Rare{k}", 30), ("Review", 60), ("Close", 120)]) for k in range(3)]
    log = _import(cases)
    assert uc.unusual_cases(log, limit=1)["page"]["next_offset"] == 1
    text, meta = uc.unusual_cases_csv(log)
    table = list(csv.reader(StringIO(text)))
    assert table[0] == uc.CSV_COLUMNS and meta["rows"] == meta["unusual_cases"] == len(table) - 1 == 3
    rows = [dict(zip(table[0], r)) for r in table[1:]]
    assert all(r["case_id"].startswith("'=odd") for r in rows)   # formula-like case keys neutralized
    assert all("rare_activity" in r["signals"].split(";") and "contains Rare" in r["reasons"] for r in rows)
    assert all(r["path"].startswith("Open → Rare") and r["path_truncated"] == "False" for r in rows)
    scores = [float(r["score"]) for r in rows]
    assert scores == sorted(scores, reverse=True)
    only, only_meta = uc.unusual_cases_csv(log, signal="heavy_rework")
    assert only.strip() == ",".join(uc.CSV_COLUMNS) and only_meta["rows"] == 0
    with pytest.raises(EventLogWorkspaceError):
        uc.unusual_cases_csv(log, signal="nonsense")


def test_csv_route_is_dashboard_only(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
rows = ["case,activity,time"] + [f"c{i:02d},{a},2024-03-{1 + i % 28:02d}T0{k}:00:00Z" for i in range(25) for k, a in enumerate(["A", "B"])]
rows += ["odd,A,2024-03-01T01:00:00Z", "odd,Z,2024-03-01T02:00:00Z"]
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    log_id = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time",
                         content="\n".join(rows).encode(), headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"}).json()["log_id"]
    path = f"/v1/event-logs/{log_id}/unusual-cases.csv"
    assert client.get(path, headers=bearer).status_code == 403
    r = client.get(path, headers=owner)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv"), r.text
    assert r.text.startswith("case_id,score,signals,") and "\nodd," in r.text and r.headers["x-owg-unusual-cases"].startswith("rows=")
    assert client.get(path + "?signal=nonsense", headers=owner).status_code == 422
print("ok")
'''
    env = {**os.environ, "WORKFLOW_OBSERVER_DATA": str(tmp_path / "srv"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
           "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")}
    done = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env,
                          capture_output=True, text=True, timeout=180)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), f"stdout={done.stdout}\nstderr={done.stderr}"
