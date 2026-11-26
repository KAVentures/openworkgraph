"""Stateless historical review adapter, exact external pins and bounded payload."""
from copy import deepcopy
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from server import agent_procurement_workforce_audit_review as workbench
from shared.agent_procurement import digest
from test_agent_procurement_workforce_timeline import snapshot,manifest
from test_agent_procurement_workforce_exchange import row


def requests():
    key=Ed25519PrivateKey.generate()
    x=snapshot({"a":(key,[row("a",incident=True)])},100)
    y=snapshot({"a":(key,[row("a")])},120)
    m=manifest(x,y)
    return ({"confirmed":True,"expected_dossier_sha256":x[0]["dossier_sha256"],
             "dossier":x[0],"trust":x[1]["trust"]},
            {"confirmed":True,"expected_manifest_sha256":digest(m),
             "manifest":m,"dossiers":[x[0],y[0]]})


def test_exact_recomputation_without_raw_packets_or_key_retention():
    single,multiple=requests()
    copies=deepcopy((single,multiple))
    result=workbench.verify(single)
    assert result["verification"]["historical_signatures_recomputed"]
    assert result["report"]==single["dossier"]["report"]
    timeline=workbench.review_timeline(multiple)
    assert timeline["timeline"]["unique_journals_with_observed_rollback_referrals"]==1
    assert (single,multiple)==copies
    for report in (result,timeline):
        assert not report["execution_authorized"]
        assert not report["result_persisted"]
        assert not report["local_trust_registry_used"]
        assert report["review_sha256"]==digest({k:v for k,v in report.items() if k!="review_sha256"})
        rendered=json.dumps(report)
        assert "case_tags" not in rendered and "source_tags" not in rendered
        assert "signature_hex" not in rendered and "public_key_hex" not in rendered


@pytest.mark.parametrize("operation",["verify","timeline"])
@pytest.mark.parametrize("kind",["no_confirmation","coerced_confirmation","wrong_digest","extra_field","oversize","rehashed_report"])
def test_fail_closed(operation,kind,monkeypatch):
    s,m=requests()
    doc=s if operation=="verify" else m
    run=workbench.verify if operation=="verify" else workbench.review_timeline
    if kind=="no_confirmation":doc["confirmed"]=False
    elif kind=="coerced_confirmation":doc["confirmed"]="true"
    elif kind=="wrong_digest":doc["expected_dossier_sha256" if operation=="verify" else "expected_manifest_sha256"]="0"*64
    elif kind=="extra_field":doc["store_report"]=True
    elif kind=="oversize":monkeypatch.setattr(workbench,"MAX_BYTES",32)
    else:
        target=doc["dossier"] if operation=="verify" else doc["dossiers"][0]
        target["report"]["included_journals"]=99
        target["dossier_sha256"]=digest({k:v for k,v in target.items() if k!="dossier_sha256"})
        if operation=="verify":doc["expected_dossier_sha256"]=target["dossier_sha256"]
        else:
            doc["manifest"]["entries"][0]["dossier_sha256"]=target["dossier_sha256"]
            doc["expected_manifest_sha256"]=digest(doc["manifest"])
    with pytest.raises(ValueError):run(doc)


def test_manifest_order_cannot_be_substituted():
    _,doc=requests()
    doc["dossiers"].reverse()
    with pytest.raises(ValueError):workbench.review_timeline(doc)
