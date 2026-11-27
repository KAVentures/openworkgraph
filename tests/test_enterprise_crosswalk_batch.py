"""New native case domains and private reviewed batch export acceptance."""
import json
from zipfile import ZipFile

import pytest

from server import enterprise_connector_crosswalk as local
from server import enterprise_crosswalk_batch as batch
from server import enterprise_connector_snapshot as snapshot
from server import enterprise_rest_feed as rest
from server import event_log_workspace as ws
from gateway.connector_case_review import ReviewedCases,Plan,Export,linked_csv
from gateway.connector_crosswalk_proposals import Propose,propose
from shared.connector_crosswalk import Crosswalk
from shared.enterprise_connector_contract import capabilities
from test_connector_event_publications import deployed,source,grant,upload,pin
from test_gateway_connector_jobs import db

SECRET=bytes(range(32))
KEY="private-reviewed-order"
BATCH="a"*32


@pytest.mark.parametrize("provider",["databricks","snowflake","netsuite","workday"])
def test_curated_case_domains(provider):
    p={"key":"ab"*32,"provider":provider,"config":{}}
    row=local.InputRow(native_case_id="Case:001",business_key=KEY)
    assert local.native_case(p,row)==rest._key(p,provider+"_case","Case:001")
    assert capabilities(provider)["private_native_crosswalk"]
    with pytest.raises(ValueError):
        local.native_case(p,row.model_copy(update={"native_type":"unapproved"}))
    assert local.native_case(p,row)!=local.native_case(p,row.model_copy(update={"native_case_id":"case:001"}))


def test_oracle_exact_selected_case():
    p={"key":"ab"*32,"provider":"oracle_fusion","config":{"selections":[
        {"object_type":"requisition","business_unit_id":"9007199254740993","header_ids":["9007199254740995"]},
        {"object_type":"purchase_order","business_unit_id":"9007199254740997","header_ids":["9007199254740995"]}]}}
    row=local.InputRow(native_case_id="9007199254740995",native_type="requisition",business_key=KEY)
    assert local.native_case(p,row)==rest._key(p,"oracle_fusion_case","requisition:9007199254740993:9007199254740995")
    assert local.native_case(p,row)!=local.native_case(p,row.model_copy(update={"native_type":"purchase_order"}))
    for changes in ({"native_type":None},{"native_type":"unknown"},{"native_case_id":"9007199254740996"}):
        with pytest.raises(ValueError):
            local.native_case(p,row.model_copy(update=changes))


def private(path, data):
    path.write_text(json.dumps(data),encoding="utf-8")
    path.chmod(0o600)
    return str(path)


@pytest.fixture
def prepared(deployed,tmp_path):
    jobs,pubs=deployed
    entries=[];grants=[]
    for i in range(2):
        j,token,snap,_=source(deployed)
        g=grant(pubs,j,snap);upload(pubs,token,g,snap);grants.append(g)
        sf=tmp_path/f"snapshot-{i}.json"
        snapshot.write_private(sf,snap)
        mf=private(tmp_path/f"mapping-{i}.json",{
            "rows":[{"native_case_id":"raw-case-1","business_key":KEY}],
            "basis":"reviewed_business_record","confirm_reviewed_mapping":True,
            "confirm_match_tag_disclosure":True})
        entries.append({"snapshot_file":str(sf),"mapping_file":mf,
                        "job_id":j["job_id"],"grant_id":g["grant_id"],"publication_revision":g["revision"]})
    spec=batch.Batch.model_validate({"batch_id":BATCH,"domain":"purchase_order","sources":entries,
          "require_all_snapshot_cases_mapped":True,"confirm_batch_review":True,"confirm_match_tag_disclosure":True})
    return spec,ReviewedCases(pubs),grants


def test_batch_preview_export_gateway_proposal_and_coverage(prepared,tmp_path):
    spec,reviewed,grants=prepared
    report,artifacts=batch.prepare_batch(spec,SECRET)
    assert report["source_count"]==2 and report["mapped_cases"]==2 and report["unmapped_cases"]==0
    assert report["automatic_linkage_authorized"] is False
    for private_value in (KEY,SECRET.hex(),"raw-case-1",artifacts[0].rows[0].match_tag):
        assert private_value not in json.dumps(report)
    out=tmp_path/"batch.zip"
    assert batch.write_bundle(out,spec,SECRET,report["batch_sha256"],confirm_export=True)==report
    assert out.stat().st_mode&0o077==0
    with ZipFile(out) as z:
        assert set(z.namelist())=={"crosswalk-01.json","crosswalk-02.json","review-manifest.json"}
        assert json.loads(z.read("review-manifest.json"))==report
        values=[Crosswalk.model_validate_json(z.read(f"crosswalk-{i:02d}.json")) for i in (1,2)]
    request=Propose.model_validate({"snapshots":[pin(g) for g in grants],"crosswalks":[a.model_dump() for a in values],
                                    "confirm_inspection":True,"confirm_tag_processing":True})
    result=propose(reviewed,"customer",request,"owner")
    assert result["summary"]["candidate_groups"]==1
    assert all(s["mapped_cases"]==s["candidate_cases"]==1 and s["unmapped_cases"]==0 for s in result["coverage"]["sources"])
    pair=result["coverage"]["source_pairs"][0]
    assert pair["candidate_groups"]==pair["left_candidate_cases"]==pair["right_candidate_cases"]==1
    assert KEY not in json.dumps(result) and values[0].rows[0].match_tag not in json.dumps(result)
    with pytest.raises(FileExistsError):
        batch.write_bundle(out,spec,SECRET,report["batch_sha256"],confirm_export=True)
    plan=Plan.model_validate(result["plan"])
    exported=reviewed.export("customer",Export.model_validate({**plan.model_dump(),
         "expected_review_sha256":result["review"]["review_sha256"],"confirm_link_assertions":True,"confirm_export":True}),"owner")
    from io import BytesIO
    log=ws.import_log(BytesIO(linked_csv(exported).encode()),name="Reviewed batch",source_format="csv",mapping=exported["mapping"])
    assert log["events"]==4 and log["objects"]==1


def test_stale_preview_and_refusals_leave_no_bundle(prepared,tmp_path):
    spec,_,_=prepared
    report,_=batch.prepare_batch(spec,SECRET)
    path=tmp_path/"out.zip"
    with pytest.raises(ValueError):
        batch.write_bundle(path,spec,SECRET,report["batch_sha256"],confirm_export=False)
    assert not path.exists()
    with pytest.raises(ValueError,match="reviewed batch changed"):
        batch.write_bundle(path,spec,bytes(reversed(range(32))),report["batch_sha256"],confirm_export=True)
    assert not path.exists()
    from pathlib import Path
    mf=Path(spec.sources[0].mapping_file)
    value=json.loads(mf.read_text());value["rows"][0]["business_key"]="new-key"
    private(mf,value)
    with pytest.raises(ValueError,match="reviewed batch changed"):
        batch.write_bundle(path,spec,SECRET,report["batch_sha256"],confirm_export=True)
    assert not path.exists()
    mf.chmod(0o644)
    with pytest.raises(ValueError):
        batch.prepare_batch(spec,SECRET)


def test_missing_snapshot_case_and_duplicate_profile(prepared,tmp_path):
    spec,_,_=prepared
    from pathlib import Path
    first=spec.sources[0]
    data=json.loads(Path(first.mapping_file).read_text());data["rows"][0]["native_case_id"]="missing"
    private(Path(first.mapping_file),data)
    with pytest.raises(ValueError):
        batch.prepare_batch(spec,SECRET)
    data["rows"][0]["native_case_id"]="raw-case-1"
    private(Path(first.mapping_file),data)
    # Duplicate profile using distinct snapshot files and job/grant pins.
    copy=tmp_path/"copy.json";copy.write_bytes(Path(first.snapshot_file).read_bytes());copy.chmod(0o600)
    duplicate=spec.model_copy(update={"sources":[first,spec.sources[1].model_copy(update={"snapshot_file":str(copy)})]})
    with pytest.raises(ValueError,match="one exact snapshot"):
        batch.prepare_batch(duplicate,SECRET)


def test_unmatched_per_source_coverage(prepared):
    spec,reviewed,grants=prepared
    from pathlib import Path
    path=Path(spec.sources[0].mapping_file);data=json.loads(path.read_text())
    data["rows"][0]["business_key"]="unmatched-key";private(path,data)
    _,artifacts=batch.prepare_batch(spec,SECRET)
    result=propose(reviewed,"customer",Propose.model_validate({"snapshots":[pin(g) for g in grants],
          "crosswalks":[a.model_dump() for a in artifacts],"confirm_inspection":True,"confirm_tag_processing":True}),"owner")
    assert result["plan"] is None
    assert all(s["single_source_cases"]==1 and s["candidate_cases"]==0 for s in result["coverage"]["sources"])
    assert result["coverage"]["source_pairs"][0]["candidate_groups"]==0
