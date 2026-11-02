"""IT service management domain pack on a single-case CSV log (#370 R23)."""
from __future__ import annotations

import copy
from io import BytesIO

import pytest

from server import event_log_domain_packs as packs
from server import event_log_workspace as workspace
from service_management_fixture import (CLOSED_BEFORE_RESOLVED, CSV_MAPPING, LATE_ASSIGN, LATE_RESOLVE, MAPPING,
                                        NAMES, PING_PONG, REOPENED, RESOLVED_BEFORE_ASSIGN, incidents)

PACK = "service_management"


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _log() -> str:
    return workspace.import_log(BytesIO(incidents().encode()), name="itsm", source_format="csv",
                                mapping=CSV_MAPPING)["log_id"]


def _cases(ids):
    return sorted(f"INC{k:04d}" for k in ids)


def _rule(report, template, a, b=None):
    return next(r for g in report["compliance"] for r in g["results"]
                if r["rule"]["template"] == template and r["rule"]["a"] == a and r["rule"].get("b") == b)


def test_a_csv_export_gets_service_levels_reassignments_and_reopenings():
    log = _log()
    installed = packs.install(log, PACK, {"version": "1.0.0", "mapping": copy.deepcopy(MAPPING)})
    # Without problems mapped, only the problem rule does not apply.
    assert installed["not_applicable"] == [{"section": "object_rules", "index": 1, "unmapped_role": "problem"}]
    report = packs.report(log, PACK)
    assert "typical it service management process" in report["interpretation"]
    examples = lambda r: sorted(e["case_id"] for e in r["violating_case_examples"])
    assign = _rule(report, "response_within", NAMES["log_incident"], NAMES["assign"])
    assert assign["rule"]["seconds"] == 4 * 3600 and examples(assign) == _cases(LATE_ASSIGN)
    resolve = _rule(report, "response_within", NAMES["log_incident"], NAMES["resolve"])
    assert resolve["rule"]["seconds"] == 72 * 3600 and examples(resolve) == _cases(LATE_RESOLVE)
    assert examples(_rule(report, "precedence", NAMES["assign"], NAMES["resolve"])) == _cases(RESOLVED_BEFORE_ASSIGN)
    assert examples(_rule(report, "existence", NAMES["reassign"])) == _cases(PING_PONG)
    assert examples(_rule(report, "existence", NAMES["reopen"])) == _cases(REOPENED)
    assert examples(_rule(report, "precedence", NAMES["resolve"], NAMES["close"])) == _cases(CLOSED_BEFORE_RESOLVED)
    metrics = {m["name"]: m for m in report["metrics"]}
    # Ten incidents are reassigned once and four three times: 22 reassignments over 60 incidents.
    assert metrics["itsm_reassignments"]["mean"] == pytest.approx(22 / 60, abs=1e-6)
    assert metrics["itsm_reopens"]["mean"] == pytest.approx(7 / 60, abs=1e-6)
    assert metrics["itsm_time_to_assign"]["median"] == 3600
    # The happy-path lifecycle fits the 40 incidents with no reassignment, reopening or order problem.
    lifecycle = report["object_model"]["lifecycles"][0]
    assert (lifecycle["objects"], lifecycle["fitting_objects"]) == (60, 40)
    assert lifecycle["unmodelled_activities"] == {NAMES["reassign"]: 22, NAMES["reopen"]: 7}


def test_hour_targets_are_parameters_in_hours():
    log = _log()
    packs.install(log, PACK, {"version": "1.0.0", "mapping": MAPPING,
                              "parameters": {"response_hours": 8, "resolution_hours": 120}})
    report = packs.report(log, PACK)
    assert _rule(report, "response_within", NAMES["log_incident"], NAMES["assign"])["violating_cases"] == 0
    assert _rule(report, "response_within", NAMES["log_incident"], NAMES["resolve"])["violating_cases"] == 0
    with pytest.raises(workspace.EventLogWorkspaceError, match="from 0.25 to 720"):
        packs.install(log, PACK, {"version": "1.0.0", "mapping": MAPPING, "parameters": {"response_hours": 0.1}})
    # Day parameters of the other packs keep their unit.
    assert packs.UNIT_SECONDS == {"days": 86400, "hours": 3600}
    assert all(p.get("unit", "days") == "days" for k in ("order_to_cash", "procure_to_pay")
               for p in packs.definitions(k)["parameters"])
