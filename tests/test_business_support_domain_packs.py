"""Real CSV import/installation checks for business support packs."""
import pytest
from test_service_governance_domain_packs import _isolated,_fixture,_spec,_rule
from server import event_log_domain_packs as packs
from server import event_log_workspace as workspace
from server import event_log_query as query
from server.event_log_domain_packs_business_support import BUSINESS_SUPPORT_PACKS


CASES=[
    ("receivables_collections","collection_case","opened","assigned","assignment_hours","ar_open_to_assignment","promise_recorded"),
    ("employee_access_requests","access_case","requested","assessed","assessment_hours","access_request_to_assessment","reworked"),
    ("recruitment_offer_operations","recruitment_case","received","screened","screening_hours","rec_intake_to_screen","rescheduled"),
]


@pytest.mark.parametrize("pack,case,start,first,target,metric,optional",CASES)
def test_import_report_revision_and_uninstall(pack,case,start,first,target,metric,optional):
    log=_fixture(pack);spec=_spec(pack,case)
    install=packs.install(log,pack,spec)
    result=packs.report(log,pack)
    assert result["pack"]["definitions_sha256"]==install["definitions_sha256"]
    assert not result["source_system_validated"] and not result["policy_compliance_verified"]
    assert not result["not_applicable"]
    assert result["object_model"]["summary"]["fitting_objects"]>=1
    metrics={x["name"]:x for x in result["metrics"]}
    assert metrics[metric]["coverage"]["cases_without_to_activity"]>=1
    rule=_rule(result,"response_within",start,first)
    assert {"late","open","reverse"}<={x["case_id"] for x in rule["violating_case_examples"]}
    assert {x["case_id"] for x in _rule(result,"precedence",start,first)["violating_case_examples"]}=={"reverse"}
    updated=packs.install(log,pack,{**spec,"parameters":{target:.25}})
    assert updated["definitions_sha256"]!=install["definitions_sha256"]
    assert _rule(packs.report(log,pack),"response_within",start,first)["rule"]["seconds"]==900
    query.save_metric(log,{"metric":{"name":"manual_count","kind":"activity_count","activity":"Source::"+start}})
    packs.uninstall(log,pack)
    assert [x["metric"]["name"] for x in query.list_metrics(log)["metrics"]]==["manual_count"]


@pytest.mark.parametrize("pack,case,start,first,target,metric,optional",CASES)
def test_missing_optional_and_required_role(pack,case,start,first,target,metric,optional):
    log=_fixture(pack);spec=_spec(pack,case,optional=False)
    invalid=_spec(pack,case,optional=False)
    invalid["mapping"]["activities"].pop(first)
    with pytest.raises(workspace.EventLogWorkspaceError,match="required role"):
        packs.install(log,pack,invalid)
    assert not packs.installed(log)["installs"]
    installed=packs.install(log,pack,spec)
    assert optional in {x["unmapped_role"] for x in installed["not_applicable"]}
    assert packs.report(log,pack)["not_applicable"]==installed["not_applicable"]


def test_unique_metric_names_across_registered_packs():
    names=[]
    for p in packs.catalog()["packs"]:
        definition=packs.definitions(p["pack_id"])
        names.extend(x["metric"]["name"] for x in definition["metrics"])
    for pack in BUSINESS_SUPPORT_PACKS:
        definition=packs.definitions(pack)
        for item in definition["metrics"]:
            assert names.count(item["metric"]["name"])==1
            assert len(item["metric"]["name"])<=40


def test_access_resource_separation_and_recorded_scope():
    log=_fixture("employee_access_requests")
    packs.install(log,"employee_access_requests",_spec("employee_access_requests","access_case"))
    r=packs.report(log,"employee_access_requests")
    # Fixture records the same beta actor for provisioning and verification.
    rule=_rule(r,"segregation_of_duties","provisioned","verified")
    assert "good" in {x["case_id"] for x in rule["violating_case_examples"]}
