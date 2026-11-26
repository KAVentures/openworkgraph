"""Signed historical audit, separate trust anchors, and conservative deltas."""
from copy import deepcopy
import json
from itertools import product

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from server import agent_procurement_workforce_dossier as dossier
from server import agent_procurement_workforce_signing as signing
from shared.agent_procurement import digest
from test_agent_procurement_workforce_exchange import row, pinned, request
from test_agent_procurement_workforce_signing import trust


def pair():
    keys = [Ed25519PrivateKey.generate() for _ in range(2)]
    pins = {"expected_correlation_namespace_sha256": digest("namespace"),
            "trust": [trust(a,k) for a,k in zip(("a","b"),keys)]}
    return keys,pins


def snapshot(keys,pins,rows,*,when=100,now=120):
    req = request(*(signing.sign_packet(k,pinned(a,r,when=when)["packet"])
                    for a,k,r in zip(("a","b"),keys,rows)))
    req["trust"] = deepcopy(pins["trust"])
    return dossier.freeze(req,now=now)


def repin(doc):
    doc["dossier_sha256"] = digest({k:v for k,v in doc.items() if k != "dossier_sha256"})
    return doc["dossier_sha256"]


def verify(doc,pins):
    return dossier.verify(doc,pins,expected_dossier_sha256=doc["dossier_sha256"])


def test_archive_recomputed_and_private_key_excluded():
    keys,pins=pair()
    doc=snapshot(keys,pins,[[row("a")],[row("b")]])
    assert doc["report"]["included_journals"]==2
    assert "trust" not in doc
    assert "public_key_hex" not in json.dumps(doc)
    r=verify(doc,pins)
    assert r["historical_signatures_recomputed"] and r["historical_report_recomputed"]
    assert not r["current_freshness_verified"] and not r["trusted_timestamp_verified"]
    assert not r["execution_authorized"]
    with pytest.raises(ValueError):
        dossier.verify(doc,pins,expected_dossier_sha256="0"*64)


@pytest.mark.parametrize("kind",["report","report_pin","bool","packet","signature",
    "future","stale","trust","extra_trust","namespace","ordering","extra"])
def test_tampering_even_with_new_digest_is_refused(kind):
    keys,pins=pair()
    doc=snapshot(keys,pins,[[row("a")],[row("b")]])
    if kind=="report":doc["report"]["included_journals"]=5
    elif kind=="report_pin":doc["report"]["report_sha256"]="0"*64
    elif kind=="bool":doc["report"]["included_journals"]=True
    elif kind=="packet":
        p=doc["packets"][0];p["packet"]["rows"][0]["attention"]=True
        p["expected_packet_sha256"]=digest(p["packet"])
    elif kind=="signature":doc["packets"][0]["signature_hex"]="0"*128
    elif kind=="future":doc["reviewed_as_of_ms"]=99
    elif kind=="stale":doc["reviewed_as_of_ms"]=201
    elif kind=="trust":pins["trust"][0]["public_key_hex"]=signing._public(Ed25519PrivateKey.generate())
    elif kind=="extra_trust":pins["trust"].append(trust("c",Ed25519PrivateKey.generate()))
    elif kind=="namespace":pins["expected_correlation_namespace_sha256"]="0"*64
    elif kind=="ordering":doc["packets"].reverse()
    else:doc["raw_input"]="must reject"
    repin(doc)
    with pytest.raises(ValueError):verify(doc,pins)


def compare(a,b,pins):
    return dossier.compare(a,b,pins,previous_sha256=a["dossier_sha256"],
                           current_sha256=b["dossier_sha256"])


def test_delta_preserves_prior_incidents_and_cohort_membership():
    keys,pins=pair()
    a=snapshot(keys,pins,[[row("a",incident=True),row("gone",incident=True)],[row("b")]])
    b=snapshot(keys,pins,[[row("a"),row("new")],[row("b",case="different")]],when=130,now=140)
    result=compare(a,b,pins)
    by_tag={r["journal_tag"]:r for r in result["journals"]}
    tag=lambda n: row(n)["journal_tag"]
    assert by_tag[tag("a")]["membership"]=="same_declared_case_cohort"
    assert by_tag[tag("a")]["rollback_referral_observed_in_either_snapshot"]
    assert by_tag[tag("gone")]["membership"]=="no_longer_present"
    assert by_tag[tag("new")]["membership"]=="newly_present"
    assert by_tag[tag("b")]["membership"]=="changed_or_unavailable_cohort"
    assert result["prior_or_current_rollback_referrals"]==2
    assert not result["incident_clearance_established"] and not result["financial_delta_calculated"]


@pytest.mark.parametrize("when,now,incident",[(99,140,False),(100,140,True),(101,120,False)])
def test_delta_rejects_nonmonotone_evidence(when,now,incident):
    keys,pins=pair()
    a=snapshot(keys,pins,[[row("a")],[row("b")]])
    b=snapshot(keys,pins,[[row("a",incident=incident)],[row("b")]],when=when,now=now)
    with pytest.raises(ValueError):compare(a,b,pins)


def test_exact_repeated_signed_packets_can_be_reviewed_at_later_clock():
    keys,pins=pair()
    a=snapshot(keys,pins,[[row("a")],[row("b")]])
    b=snapshot(keys,pins,[[row("a")],[row("b")]],when=100,now=140)
    assert all(not r["packet_advanced"] for r in compare(a,b,pins)["installations"])


def test_delta_incident_truth_table():
    keys,pins=pair()
    for before,after in product((False,True),repeat=2):
        a=snapshot(keys,pins,[[row("a",incident=before)],[row("b")]])
        b=snapshot(keys,pins,[[row("a",incident=after)],[row("b")]],when=130,now=140)
        r=compare(a,b,pins)
        assert r["prior_or_current_rollback_referrals"]==int(before or after)


def test_cli_exclusive_files_and_strict_json(tmp_path,monkeypatch):
    keys,pins=pair()
    req=request(*(signing.sign_packet(k,pinned(a,[row(a)])["packet"])
                  for a,k in zip(("a","b"),keys)))
    req["trust"]=pins["trust"]
    inp,trust_file,out,receipt=[tmp_path/p for p in ("input.json","trust.json","dossier.json","receipt.json")]
    dossier.write_json(inp,req);dossier.write_json(trust_file,pins)
    monkeypatch.setattr(dossier.time,"time",lambda:0.120)
    assert dossier.main(["freeze",str(inp),str(out),"--confirm-linkable-export"])==0
    doc=dossier.read_json(out)
    assert dossier.main(["verify",str(out),str(trust_file),"--sha256",doc["dossier_sha256"],str(receipt)])==0
    assert dossier.read_json(receipt)["historical_report_recomputed"]
    with pytest.raises(SystemExit):
        dossier.main(["freeze",str(inp),str(out),"--confirm-linkable-export"])
    assert dossier.read_json(out)==doc
    inp.write_text('{"a":1,"a":2}')
    with pytest.raises(ValueError):dossier.read_json(inp)
    inp.write_text('{"a":NaN}')
    with pytest.raises(ValueError):dossier.read_json(inp)
    monkeypatch.setattr(dossier,"MAX_BYTES",4)
    with pytest.raises(ValueError):dossier.read_json(inp)
