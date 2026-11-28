"""Reuse actual generated runtime manifests for the selected exact candidate."""
from copy import deepcopy
import hashlib
from io import BytesIO
from zipfile import ZipFile

import pytest

from shared.agent_procurement import digest
from shared.agent_procurement_selection import decide
from shared.agent_procurement_substitution import inspect
from shared.process_agent_project import build_runtime_project
from shared.process_agent_release import MANIFEST_NAME
from test_agent_procurement_selection import selection,assessment,request_for
from test_agent_procurement_trials import trial
from test_agent_procurement import study


def artifact(packet,directory):
    directory.mkdir()
    with ZipFile(BytesIO(build_runtime_project(packet))) as archive:
        archive.extractall(directory)
    return hashlib.sha256((directory/MANIFEST_NAME).read_bytes()).hexdigest()


@pytest.fixture
def preflight(selection,tmp_path):
    packet,dp,dc,hp,hc,n,_=selection
    a=assessment(selection)
    request=request_for(a)
    receipt=decide(packet,dp,dc,hp,hc,n,request)
    bundle={"discovery_plan":dp,"discovery_captures":dc,"holdout_plan":hp,
            "holdout_captures":hc,"nomination":n,"request":request}
    old=tmp_path/"baseline";new=tmp_path/"candidate"
    bp=artifact(packet,old);cp=artifact(packet,new)
    spec={"expected_selection_receipt_sha256":receipt["receipt_sha256"],"confirmed":True,
          "baseline":{"configuration_sha256":digest("baseline"),"manifest_sha256":bp},
          "candidate":{"configuration_sha256":digest("cheap"),"manifest_sha256":cp}}
    return packet,bundle,receipt,old,new,spec


def test_real_runtime_preflight_keeps_deployment_separate(preflight):
    result=inspect(*preflight)
    assert result["generated_runtime_specifications_match_evaluated_specification"]
    assert result["generated_artifacts_verified_against_supplied_manifest_pins"]
    assert result["rehearsal_candidate_id"]=="cheap"
    assert result["runtime_comparison"]["revision_direction"]=="same_revision_same_content"
    assert not result["execution_authorized"] and not result["deployment_changed"]
    assert not result["live_tool_schema_compatibility_verified"]
    assert not result["artifact_to_candidate_configuration_link_independently_verified"]
    assert result["preflight_sha256"]==digest({k:v for k,v in result.items() if k!="preflight_sha256"})


def test_changed_artifact_bytes_rejected(preflight):
    packet,bundle,receipt,old,new,spec=preflight
    (new/"agent.py").write_text("# changed",encoding="utf-8")
    with pytest.raises(ValueError):inspect(*preflight)


def test_different_saved_revision_artifact_rejected_even_with_correct_manifest(preflight,tmp_path):
    packet,bundle,receipt,old,new,spec=preflight
    different=tmp_path/"different"
    pin=artifact({**packet,"revision":2},different)
    spec=deepcopy(spec);spec["candidate"]["manifest_sha256"]=pin
    with pytest.raises(ValueError,match="exact evaluated specification"):
        inspect(packet,bundle,receipt,old,different,spec)


def test_changed_configuration_and_receipt_rejected(preflight):
    packet,bundle,receipt,old,new,spec=preflight
    changed=deepcopy(spec);changed["candidate"]["configuration_sha256"]="f"*64
    with pytest.raises(ValueError,match="configuration"):inspect(packet,bundle,receipt,old,new,changed)
    changed=deepcopy(receipt);changed["rehearsal_candidate_id"]="baseline"
    with pytest.raises(ValueError,match="receipt"):inspect(packet,bundle,changed,old,new,spec)


def test_retain_baseline_is_not_substitution_authority(preflight):
    packet,bundle,receipt,old,new,spec=preflight
    bundle=deepcopy(bundle);bundle["request"]["decision"]="retain_baseline"
    retained=decide(packet,**bundle)
    spec=deepcopy(spec);spec["expected_selection_receipt_sha256"]=retained["receipt_sha256"]
    with pytest.raises(ValueError,match="did not nominate"):inspect(packet,bundle,retained,old,new,spec)
