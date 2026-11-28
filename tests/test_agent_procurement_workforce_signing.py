from copy import deepcopy

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from server import agent_procurement_workforce_signing as signing
from shared.agent_procurement import digest
from test_agent_procurement_workforce_exchange import row,pinned,request


def signed(alias,key,rows):
    return signing.sign_packet(key,pinned(alias,rows)["packet"])


def trust(alias,key):
    return {"installation_alias":alias,"public_key_hex":signing._public(key),
            "correlation_namespace_sha256":digest("namespace")}


def bundle():
    a,b=Ed25519PrivateKey.generate(),Ed25519PrivateKey.generate()
    req=request(signed("a",a,[row("a")]),signed("b",b,[row("b")]))
    req["trust"]=[trust("a",a),trust("b",b)]
    return req


def test_signed_review_and_declared_identity_boundary():
    req=bundle()
    r=signing.signed_review(req,now=120)
    assert r["all_packet_signatures_verified_against_operator_pins"]
    assert r["included_journals"]==2
    assert not r["source_identity_verified"] and not r["organizational_identity_verified"]
    assert not r["persistent_replay_history_verified"]
    assert len(r["trusted_key_fingerprints"])==2


@pytest.mark.parametrize("kind",["content","repin","wrong_key","signature","alias","namespace","same_key","extra_trust","missing_trust","stale","future","confirmation"])
def test_signed_review_fails_closed(kind):
    req=bundle()
    p=req["packets"][0]
    if kind=="content":p["packet"]["rows"][0]["attention"]=True
    elif kind=="repin":
        p["packet"]["rows"][0]["attention"]=True
        p["expected_packet_sha256"]=digest(p["packet"])
    elif kind=="wrong_key":req["trust"][0]["public_key_hex"]=signing._public(Ed25519PrivateKey.generate())
    elif kind=="signature":p["signature_hex"]="0"*128
    elif kind=="alias":req["trust"][0]["installation_alias"]="A"
    elif kind=="namespace":req["trust"][0]["correlation_namespace_sha256"]="f"*64
    elif kind=="same_key":req["trust"][0]["public_key_hex"]=req["trust"][1]["public_key_hex"]
    elif kind=="extra_trust":req["trust"].append(trust("c",Ed25519PrivateKey.generate()))
    elif kind=="missing_trust":req["trust"].pop()
    elif kind=="stale":req["maximum_packet_age_ms"]=10
    elif kind=="future":
        with pytest.raises(ValueError):signing.signed_review(req,now=99)
        return
    else:req["confirmed"]=False
    with pytest.raises(ValueError):signing.signed_review(req,now=120)


def test_local_identity_stable_and_secret_not_returned(tmp_path,monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR",str(tmp_path))
    assert not list(tmp_path.iterdir())
    with pytest.raises(ValueError):signing.identity({"confirmed":False})
    assert not list(tmp_path.iterdir())
    a=signing.identity({"confirmed":True})
    b=signing.identity({"confirmed":True})
    assert a==b
    secret=(tmp_path/signing.KEY_FILE).read_text().strip()
    assert secret not in str(a)
    packet=pinned("a",[row("a")])["packet"]
    monkeypatch.setattr(signing.exchange,"export",lambda doc:{"packet":packet})
    result=signing.signed_export({})
    assert result["public_key_hex"]==a["public_key_hex"]
    assert secret not in str(result)
    s=result["signed_packet"]
    signing.verify_packet(a["public_key_hex"],s["packet"],s["expected_packet_sha256"],s["signature_hex"])
