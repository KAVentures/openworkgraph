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
