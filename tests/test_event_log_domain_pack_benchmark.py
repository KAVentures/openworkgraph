"""Cross-source benchmarks compare canonical pack roles, not raw labels."""
from copy import deepcopy
from io import BytesIO
import json
import pytest

from server import event_log_domain_packs as packs
from server import event_log_domain_pack_benchmark as benchmark
from server import event_log_workspace as workspace
from service_management_fixture import incidents,MAPPING,CSV_MAPPING

PACK="service_management"


@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(workspace,"DATA_DIR",tmp_path)


def installed(rename=False,parameters=None):
    mapping=deepcopy(MAPPING)
    text=incidents()
    if rename:
        # Replace complete status fields, not substrings of other labels.
        import csv
        from io import StringIO
        rows=list(csv.reader(StringIO(text)))
        names={old:"Other system "+old for old in mapping["activities"].values()}
        for row in rows[1:]:row[1]=names[row[1]]
        buf=StringIO();csv.writer(buf).writerows(rows);text=buf.getvalue()
        mapping["activities"]={role:names[old] for role,old in mapping["activities"].items()}
    log=workspace.import_log(BytesIO(text.encode()),name="comparison" if rename else "baseline",
                              source_format="csv",mapping=CSV_MAPPING)["log_id"]
    spec={"version":"1.0.0","mapping":mapping,**({"parameters":parameters} if parameters else {})}
    packs.install(log,PACK,spec)
    return log,spec


def preview(a,b):
    return benchmark.preflight(a,PACK,{"other_log_id":b})


def compare(a,b,pin):
    return benchmark.compare(a,PACK,{"other_log_id":b,"expected_preflight_sha256":pin["preflight_sha256"]})


def test_role_aligned_different_labels_same_data_produce_zero_changes():
    a,_=installed();b,_=installed(rename=True)
    pin=preview(a,b)
    assert pin["ready_to_compare"]
    assert pin["baseline"]["definitions_sha256"]!=pin["comparison"]["definitions_sha256"]
    assert all(x["mapped_on_both"] for x in pin["role_alignment"])
    result=compare(a,b,pin)
    assert result["report_sha256"]==compare(a,b,pin)["report_sha256"]
    for row in result["metrics"]:
        assert row["comparable"]
        assert all(x in (None,0) for x in row["changes"].values())
    for row in result["case_rules"]:
        assert row["changes"]["violation_rate_change"] in (None,0)
    assert "INC0003" not in json.dumps(result)
    assert not result["raw_case_identifiers_returned"]
    assert not result["causal_effect_estimated"]
    assert not result["execution_authorized"]


def test_unmapped_optional_definition_has_no_delta():
    a,_=installed();b,spec=installed(rename=True)
    del spec["mapping"]["activities"]["reopen"]
    packs.install(b,PACK,spec)
    result=compare(a,b,preview(a,b))
    omitted=[r for r in result["case_rules"]+result["metrics"] if not r["comparable"]]
    assert omitted
    assert all(r["changes"] is None and r["not_comparable_reason"]=="unmapped_definition" for r in omitted)
    assert not result["object_models"]["same_mapped_roles"]


def test_different_parameters_refuse_comparison():
    a,_=installed();b,_=installed(rename=True)
    # Use actual first parameter rather than assuming the spelling.
    definition=packs.definitions(PACK)
    param=definition["parameters"][0]
    record=deepcopy(MAPPING)
    renamed={k:"Other system "+v for k,v in record["activities"].items()}
    record["activities"]=renamed
    packs.install(b,PACK,{"version":"1.0.0","mapping":record,"parameters":{param["name"]:param["default"]+1}})
    pin=preview(a,b)
    assert "different_parameter_values" in pin["blockers"]
    assert not pin["ready_to_compare"]
    with pytest.raises(workspace.EventLogWorkspaceError,match="parameters"):compare(a,b,pin)


def test_stale_source_and_configuration_are_refused():
    a,_=installed();b,spec=installed(rename=True)
    pin=preview(a,b)
    with workspace._LOCK:
        conn=workspace._connect()
        try:conn.execute("UPDATE events SET resource='changed' WHERE log_id=?",[b])
        finally:conn.close()
    with pytest.raises(workspace.EventLogWorkspaceError,match="changed"):compare(a,b,pin)
    pin=preview(a,b)
    packs.install(b,PACK,spec)
    with pytest.raises(workspace.EventLogWorkspaceError,match="changed"):compare(a,b,pin)


def test_missing_install_same_log_and_budget():
    a,_=installed()
    b=workspace.import_log(BytesIO(incidents().encode()),name="not installed",source_format="csv",mapping=CSV_MAPPING)["log_id"]
    with pytest.raises(workspace.EventLogWorkspaceError,match="different"):preview(a,a)
    with pytest.raises(workspace.EventLogWorkspaceError,match="Install"):preview(a,b)
    with pytest.raises(workspace.EventLogWorkspaceError,match="digest"):
        benchmark.compare(a,PACK,{"other_log_id":b,"expected_preflight_sha256":"bad"})


def test_work_budget_refuses_partial_review(monkeypatch):
    a,_=installed();b,_=installed(rename=True)
    monkeypatch.setattr(benchmark,"MAX_SOURCE_ROWS",1)
    with pytest.raises(workspace.EventLogWorkspaceError,match="budget"):preview(a,b)


def test_case_unknown_bounds_use_activated_not_total():
    definition={"compliance":[{"case_type":"@case","rule":{"template":"precedence","a":"@a","b":"@b"}}]}
    resolved={"not_applicable":[],"compliance":[{"case_type":"case","rule":{"template":"precedence","a":"A","b":"B"}}]}
    row={"rule":{"template":"precedence","a":"A","b":"B"},"cases":20,"activated_cases":10,
         "vacuously_satisfied_cases":10,"violating_cases":2,"satisfied_cases":5,"unknown_cases":3,"violation_rate":2/7}
    slots=benchmark._compliance_slots(definition,resolved,{"compliance":[{"case_type":"case","results":[row]}]})
    assert slots[0]["activated_violation_share_bounds"]=={"lower":.2,"upper":.5}
    result=benchmark._compare_slots(definition,"compliance",slots,slots)[0]
    assert result["changes"]["violation_rate_change"]==0
    assert result["changes"]["activated_violation_share_change_bounds"]=={"lower":.2-.5,"upper":.5-.2}
