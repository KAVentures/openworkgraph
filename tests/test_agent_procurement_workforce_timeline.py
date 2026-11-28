"""Independent historical trust manifests and multi-snapshot incident retention."""
from copy import deepcopy
from itertools import product

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from server import agent_procurement_workforce_dossier as audit
from server import agent_procurement_workforce_timeline as timeline
from server import agent_procurement_workforce_signing as signing
from shared.agent_procurement import digest
from test_agent_procurement_workforce_exchange import row,pinned,request
from test_agent_procurement_workforce_signing import trust


def snapshot(sites, when, *, age=100, clock=None):
    """sites: alias -> (key, rows)."""
    req=request(*(signing.sign_packet(k,pinned(a,rows,when=when)["packet"])
                  for a,(k,rows) in sites.items()))
    req["trust"]=[trust(a,k) for a,(k,_) in sites.items()]
    req["maximum_packet_age_ms"]=age
    doc=audit.freeze(req,now=when+10 if clock is None else clock)
    return doc,{"dossier_sha256":doc["dossier_sha256"],
                "trust":{"expected_correlation_namespace_sha256":digest("namespace"),
                         "trust":req["trust"]}}


def manifest(*snapshots):
    return {"format":"openworkgraph.workforce-audit-timeline-manifest.v1",
            "entries":[e for _,e in snapshots]}


def review(*snapshots):
    m=manifest(*snapshots)
    return timeline.review([d for d,_ in snapshots],m,expected_manifest_sha256=digest(m))


def test_history_survives_absence_rotation_and_changed_policy():
    a,b,c=[Ed25519PrivateKey.generate() for _ in range(3)]
    first=snapshot({"a":(a,[row("a",incident=True)]),"b":(b,[row("b")])},100)
    second=snapshot({"b":(b,[row("b")])},120)
    last=snapshot({"a":(c,[row("a")]),"b":(b,[row("b")])},140,age=200)
    r=review(first,second,last)
    assert r["snapshots"][1]["aliases_absent_since_previous"]==["a"]
    assert r["snapshots"][2]["aliases_added_since_previous"]==["a"]
    assert r["snapshots"][2]["declared_key_changes"][0]["installation_alias"]=="a"
    assert r["snapshots"][2]["packet_age_policy_changed"]
    item=next(x for x in r["journals"] if x["installation_alias"]=="a")
    assert item["rollback_referral_snapshots"]==[1]
    assert item["observed_snapshots"]==2 and item["present_in_latest_snapshot"]
    assert r["unique_journals_with_observed_rollback_referrals"]==1
    assert not r["incident_clearance_established"] and not r["key_rotation_authority_verified"]
    assert not r["financial_delta_calculated"]


@pytest.mark.parametrize("kind",["backdated","conflict","recycled_key","reassigned_key","alias_case","clock","duplicate"])
def test_historical_reappearance_rejects_rewrites(kind):
    a,b,c=[Ed25519PrivateKey.generate() for _ in range(3)]
    first=snapshot({"a":(a,[row("a")]),"b":(b,[row("b")])},100)
    second=snapshot({"a":(c,[row("a")]),"b":(b,[row("b")])},120)
    absent=snapshot({"b":(b,[row("b")])},140)
    if kind=="backdated": last=snapshot({"a":(c,[row("a")])},119,clock=160)
    elif kind=="conflict":last=snapshot({"a":(c,[row("a",incident=True)])},120,clock=160)
    elif kind=="recycled_key":last=snapshot({"a":(a,[row("a")])},150)
    elif kind=="reassigned_key":last=snapshot({"z":(a,[row("a")])},150)
    elif kind=="alias_case":last=snapshot({"A":(c,[row("a")])},150)
    elif kind=="clock":last=snapshot({"a":(c,[row("a")])},150,clock=150)
    else:last=first
    with pytest.raises(ValueError):review(first,second,absent,last)


def test_cutoff_highwater_and_changed_cohort_across_absence():
    a,b=[Ed25519PrivateKey.generate() for _ in range(2)]
    r1=row("a");r1["cutoff_ms"]=100
    r2=row("a",case="different");r2["cutoff_ms"]=70
    r3=row("a",case="different");r3["cutoff_ms"]=90
    snapshots=[snapshot({"a":(a,[r1]),"b":(b,[row("b")])},110),
               snapshot({"b":(b,[row("b")])},120),
               snapshot({"a":(a,[r2])},130),snapshot({"a":(a,[r3])},140)]
    item=next(r for r in review(*snapshots)["journals"] if r["installation_alias"]=="a")
    assert item["changed_cohort_snapshots"]==[3]
    assert item["cutoff_regression_snapshots"]==[3,4]
    assert item["maximum_cutoff_ms"]==100


def test_manifest_pin_and_signed_report_cannot_be_substituted():
    a=Ed25519PrivateKey.generate()
    x=snapshot({"a":(a,[row("a")])},100)
    y=snapshot({"a":(a,[row("a")])},120)
    m=manifest(x,y)
    with pytest.raises(ValueError):timeline.review([y[0],x[0]],m,expected_manifest_sha256=digest(m))
    with pytest.raises(ValueError):timeline.review([x[0],y[0]],m,expected_manifest_sha256="0"*64)
    altered=deepcopy(y[0]);altered["report"]["included_journals"]=2
    altered["dossier_sha256"]=digest({k:v for k,v in altered.items() if k!="dossier_sha256"})
    m["entries"][1]["dossier_sha256"]=altered["dossier_sha256"]
    with pytest.raises(ValueError):timeline.review([x[0],altered],m,expected_manifest_sha256=digest(m))


def test_historical_incident_union_truth_table():
    a,b=[Ed25519PrivateKey.generate() for _ in range(2)]
    for states in product(("absent","clean","flag"),repeat=3):
        snaps=[]
        for i,state in enumerate(states):
            sites={"b":(b,[row("b")])}
            if state!="absent":sites["a"]=(a,[row("a",incident=state=="flag")])
            snaps.append(snapshot(sites,100+20*i))
        result=review(*snaps)
        assert result["unique_journals_with_observed_rollback_referrals"]==int("flag" in states)
        if any(s!="absent" for s in states):
            item=next(r for r in result["journals"] if r["installation_alias"]=="a")
            assert item["rollback_referral_snapshots"]==[i+1 for i,s in enumerate(states) if s=="flag"]
            assert item["present_in_latest_snapshot"]==(states[-1]!="absent")


def test_timeline_cli_and_exclusive_output(tmp_path):
    a=Ed25519PrivateKey.generate()
    snaps=[snapshot({"a":(a,[row("a")])},when) for when in (100,120)]
    m=manifest(*snaps);mp=tmp_path/"trust.json";out=tmp_path/"report.json"
    audit.write_json(mp,m)
    paths=[]
    for i,(doc,_) in enumerate(snaps):
        p=tmp_path/f"{i}.json";audit.write_json(p,doc);paths.append(str(p))
    args=[str(mp),"--manifest-sha256",digest(m),"--output",str(out),*paths]
    assert timeline.main(args)==0
    assert audit.read_json(out)["historical_reports_recomputed"]
    with pytest.raises(SystemExit):timeline.main(args)
