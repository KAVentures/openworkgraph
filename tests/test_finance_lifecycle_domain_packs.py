"""Exercise the existing real CSV import, pack installation and report engines."""
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO
import csv
import pytest
from server import event_log_domain_packs as packs
from server import event_log_workspace as workspace
from server.event_log_domain_packs_finance_lifecycle import FINANCE_LIFECYCLE_PACKS


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


@pytest.mark.parametrize("key", sorted(FINANCE_LIFECYCLE_PACKS))
def test_install_report_reconfigure_and_uninstall(key):
    pack = packs.definitions(key)
    assert key in {p["pack_id"] for p in packs.catalog()["packs"]}
    required = [r["role"] for r in pack["activities"] if r["required"]]
    optional = [r["role"] for r in pack["activities"] if not r["required"]]
    target = pack["parameters"][0]
    start, next_step = required[:2]
    buf = StringIO()
    w = csv.writer(buf)
    w.writerow(["case", "step", "time", "actor"])
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    def emit(case, roles, hours):
        for i, (role, hour) in enumerate(zip(roles, hours)):
            w.writerow([case, "src:" + role, (base + timedelta(hours=hour)).isoformat(),
                        "A" if i == 0 else "B"])
    emit("good", required, range(len(required)))
    emit("late", required, [0] + [target["default"] + i + 1 for i in range(len(required)-1)])
    emit("open", required[:1], [0])
    emit("reverse", [next_step, start, *required[2:]], range(len(required)))
    emit("extra", required + optional, range(len(required) + len(optional)))
    log = workspace.import_log(BytesIO(buf.getvalue().encode()), name="finance fixture",
                               source_format="csv", mapping={"case_column": "case",
                               "activity_column": "step", "timestamp_column": "time",
                               "resource_column": "actor"})["log_id"]
    spec = {"version": "1.0.0", "mapping": {
        "object_types": {pack["object_types"][0]["role"]: "case"},
        "activities": {r: "src:" + r for r in required + optional}}}
    result = packs.install(log, key, spec)
    report = packs.report(log, key)
    assert report["pack"]["definitions_sha256"] == result["definitions_sha256"]
    assert not report["source_system_validated"] and not report["policy_compliance_verified"]
    assert report["object_model"]["summary"]["fitting_objects"] >= 1
    def deadline(report):
        return next(r for g in report["compliance"] for r in g["results"]
                    if r["rule"]["template"] == "response_within"
                    and r["rule"]["a"] == "src:" + start
                    and r["rule"]["b"] == "src:" + next_step)
    assert {"late", "open", "reverse"} <= {e["case_id"] for e in deadline(report)["violating_case_examples"]}
    spec["parameters"] = {target["name"]: .25}
    updated = packs.install(log, key, spec)
    assert updated["definitions_sha256"] != result["definitions_sha256"]
    assert deadline(packs.report(log, key))["rule"]["seconds"] == 900
    for role in optional:
        del spec["mapping"]["activities"][role]
    packs.install(log, key, spec)
    assert set(optional) <= {r["unmapped_role"] for r in packs.report(log, key)["not_applicable"]}
    packs.uninstall(log, key)
    with pytest.raises(workspace.EventLogWorkspaceError):
        packs.report(log, key)
