"""Operational packs: independently planted timing, order and duplication deviations."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime,timedelta,timezone
from io import BytesIO
import csv
from io import StringIO
import pytest

from server import event_log_domain_packs as packs
from server import event_log_workspace as workspace
from server import event_log_query as query
from server.event_log_domain_packs_operations import OPERATIONS_PACKS

PACK_IDS=tuple(OPERATIONS_PACKS)
BASE=datetime(2026,1,1,tzinfo=timezone.utc)
CONFIG={
    "contract_lifecycle":{
        "case":"contract_case","roles":["request","draft","legal_review","commercial_approval","signature","repository","close"],
        "hours":[0,1,2,3,4,5,6],"late_role":"draft","late_at":24*6,
        "late_rule":("request","draft"),"late_param":"draft_days","tight":1,
        "duplicate":"signature","precedence":("legal_review","signature"),
        "optional":"legal_review","four_eyes":("draft","commercial_approval"),
        "metric":"contract_request_to_draft"},
    "security_incident_response":{
        "case":"incident_case","roles":["detected","triaged","contained","investigated","recovered","reviewed","closed"],
        "hours":[0,1,2,3,4,5,6],"late_role":"triaged","late_at":6,
        "late_rule":("detected","triaged"),"late_param":"triage_hours","tight":.5,
        "duplicate":"closed","precedence":("contained","recovered"),
        "optional":"reviewed","four_eyes":("recovered","reviewed"),
        "metric":"sec_detection_to_triage"},
    "maintenance_work_orders":{
        "case":"maintenance_case","roles":["requested","assigned","authorized","started","completed","inspected","closed"],
        "hours":[0,1,2,3,4,5,6],"late_role":"assigned","late_at":10,
        "late_rule":("requested","assigned"),"late_param":"assignment_hours","tight":.5,
        "duplicate":"completed","precedence":("authorized","started"),
        "optional":"inspected","four_eyes":("completed","inspected"),
        "metric":"maint_request_to_assignment"},
}


@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(workspace,"DATA_DIR",tmp_path)


def fixture(pack_id):
    cfg=CONFIG[pack_id]
    names={role:f"source_{role}" for role in cfg["roles"]}
    rows=[]
    def add(key,values,resources=True):
        for role,offset in values:
            resource=("actor_"+role if resources else "")
            if key=="same_actor" and role in cfg["four_eyes"]:resource="same"
            rows.append([key,names[role],(BASE+timedelta(hours=offset)).isoformat(),resource])
    normal=list(zip(cfg["roles"],cfg["hours"]))
    add("normal",normal)
    add("late",[(role,cfg["late_at"] if role==cfg["late_role"] else offset) for role,offset in normal])
    add("duplicate",normal+[(cfg["duplicate"],7)])
    a,b=cfg["precedence"]
    add("wrong_order",[(role,8 if role==a else offset) for role,offset in normal])
    add("same_actor",normal)
    add("unknown_actor",normal,False)
    stream=StringIO()
    writer=csv.writer(stream);writer.writerow(["case","activity","time","resource"]);writer.writerows(rows)
    log=workspace.import_log(BytesIO(stream.getvalue().encode()),name=pack_id,source_format="csv",
        mapping={"case_column":"case","activity_column":"activity","timestamp_column":"time","resource_column":"resource"})["log_id"]
    return log,{"version":"1.0.0","mapping":{"object_types":{cfg["case"]:"case"},"activities":names}}


def rule(report,template,a,b=None):
    return next(r for g in report["compliance"] for r in g["results"] if
        r["rule"]["template"]==template and r["rule"]["a"]=="source_"+a and
        r["rule"].get("b")==(None if b is None else "source_"+b))


@pytest.mark.parametrize("pack_id",PACK_IDS)
def test_catalog_install_and_real_report_find_planted_deviations(pack_id):
    cfg=CONFIG[pack_id];log,spec=fixture(pack_id)
    declared=packs.definitions(pack_id)
    catalog=next(p for p in packs.catalog()["packs"] if p["pack_id"]==pack_id)
    assert catalog["latest_version"]=="1.0.0"
    install=packs.install(log,pack_id,spec)
    assert install["change"]=="installed" and len(install["metric_ids"])==len(declared["metrics"])
    assert install["not_applicable"]==[]
    report=packs.report(log,pack_id)
    assert report["pack"]["definitions_sha256"]==install["definitions_sha256"]
    assert not report["source_system_validated"] and not report["policy_compliance_verified"]
    assert not report["execution_authorized"]
    late=rule(report,"response_within",*cfg["late_rule"])
    assert "late" in {x["case_id"] for x in late["violating_case_examples"]}
    duplicate=rule(report,"existence",cfg["duplicate"])
    assert "duplicate" in {x["case_id"] for x in duplicate["violating_case_examples"]}
    wrong=rule(report,"precedence",*cfg["precedence"])
    assert "wrong_order" in {x["case_id"] for x in wrong["violating_case_examples"]}
    four=rule(report,"segregation_of_duties",*cfg["four_eyes"])
    assert "same_actor" in {x["case_id"] for x in four["violating_case_examples"]}
    assert four["unknown_cases"]>=1
    assert report["object_model"]["summary"]["objects"]==6
    assert report["object_model"]["summary"]["fitting_objects"]<6
    metric=next(m for m in report["metrics"] if m["name"]==cfg["metric"])
    assert metric["coverage"]["cases_with_value"]==6
    assert metric["median"]==3600


@pytest.mark.parametrize("pack_id",PACK_IDS)
def test_optional_mapping_and_parameter_revision_are_explicit(pack_id):
    cfg=CONFIG[pack_id];log,spec=fixture(pack_id)
    first=packs.install(log,pack_id,spec)
    reduced=deepcopy(spec)
    del reduced["mapping"]["activities"][cfg["optional"]]
    reduced["parameters"]={cfg["late_param"]:cfg["tight"]}
    second=packs.install(log,pack_id,reduced)
    assert second["definitions_sha256"]!=first["definitions_sha256"]
    report=packs.report(log,pack_id)
    assert cfg["optional"] in {r["unmapped_role"] for r in report["not_applicable"]}
    assert report["parameters"][cfg["late_param"]]==cfg["tight"]
    deadline=rule(report,"response_within",*cfg["late_rule"])
    assert deadline["violating_cases"]>=1
    assert len(query.list_metrics(log)["metrics"])==len(second["metric_ids"])
    packs.uninstall(log,pack_id)
    assert query.list_metrics(log)["metrics"]==[]


@pytest.mark.parametrize("pack_id",PACK_IDS)
def test_invalid_mapping_and_parameter_do_not_install(pack_id):
    cfg=CONFIG[pack_id];log,spec=fixture(pack_id)
    for mutate in (
        lambda s:s["mapping"]["activities"].pop(cfg["roles"][0]),
        lambda s:s["mapping"]["activities"].update({cfg["roles"][0]:"absent"}),
        lambda s:s.update(parameters={cfg["late_param"]:-1}),
    ):
        invalid=deepcopy(spec);mutate(invalid)
        with pytest.raises(workspace.EventLogWorkspaceError):packs.install(log,pack_id,invalid)
    assert query.list_metrics(log)["metrics"]==[]


def test_new_definitions_are_deterministic_and_names_do_not_collide():
    all_names=[]
    for versions in packs.PACKS.values():
        for pack in versions.values():
            all_names.extend((pack["pack_id"],m["metric"]["name"]) for m in pack["metrics"])
    owned={}
    for pack,name in all_names:
        assert name not in owned or owned[name]==pack
        owned[name]=pack
    for pack_id in PACK_IDS:
        first=packs.definitions(pack_id)
        second=packs.definitions(pack_id)
        assert first==second and first is not second
        first["activities"].clear()
        assert packs.definitions(pack_id)["activities"]


@pytest.mark.parametrize("pack_id",PACK_IDS)
def test_every_optional_role_is_explicit_when_unmapped(pack_id):
    log,spec=fixture(pack_id)
    definition=packs.definitions(pack_id)
    missing={a["role"] for a in definition["activities"] if not a["required"]}
    for role in missing:
        del spec["mapping"]["activities"][role]
    installed=packs.install(log,pack_id,spec)
    assert missing <= {r["unmapped_role"] for r in installed["not_applicable"]}
    report=packs.report(log,pack_id)
    assert missing <= {r["unmapped_role"] for r in report["not_applicable"]}
    assert report["object_model"]["summary"]["objects"]==6


def test_security_investigation_has_an_actual_metric_and_sequence_checks():
    log,spec=fixture("security_incident_response")
    packs.install(log,"security_incident_response",spec)
    report=packs.report(log,"security_incident_response")
    metric=next(m for m in report["metrics"] if m["name"]=="sec_investigation_records")
    assert metric["median"]==1
    assert metric["coverage"]["cases_with_value"]==6
    assert rule(report,"precedence","contained","investigated")
    assert rule(report,"precedence","investigated","recovered")
