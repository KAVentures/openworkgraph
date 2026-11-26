from copy import deepcopy
from itertools import product
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from server import agent_procurement_workforce_trust as trust
from server import agent_procurement_monitor_journal as journal
from server import agent_procurement_workforce_signing as signing
from shared.agent_procurement import digest
from test_agent_procurement_monitor_journal import local_db
from test_agent_procurement_workforce_exchange import row,pinned


NS=digest("namespace")


def configure(revision,alias,key,action="enroll"):
    return trust.configure({"expected_revision":revision,"installation_alias":alias,
        "correlation_namespace_sha256":NS,"action":action,
        "public_key_hex":signing._public(key) if key else None,"confirmed":True})


def packet(alias,key,when=100):
    return signing.sign_packet(key,pinned(alias,[row(alias)],when=when)["packet"])


def review(revision,*packets):
    return trust.review({"expected_revision":revision,"expected_correlation_namespace_sha256":NS,
        "maximum_packet_age_ms":100,"packets":list(packets),"confirmed":True},now=150)


def test_atomic_registered_review_and_rotation(local_db):
    a,b,c=(Ed25519PrivateKey.generate() for _ in range(3))
    assert trust.status({})["revision"]==0
    assert configure(0,"a",a)["revision"]==1
    assert configure(1,"b",b)["revision"]==2
    pa,pb=packet("a",a),packet("b",b)
    with pytest.raises(ValueError):review(2,pa)
    first=review(2,pa,pb)
    assert first["registry_revision"]==3 and first["accepted_new_packets"]==2
    repeat=review(3,pa,pb)
    assert repeat["registry_revision"]==3 and repeat["exact_repeated_packets"]==2
    with pytest.raises(ValueError):review(3,packet("a",a,99),packet("b",b,101))
    assert trust.status({})["revision"]==3
    assert configure(3,"a",None,"revoke")["revision"]==4
    with pytest.raises(ValueError):review(4,pa,pb)
    assert configure(4,"a",c,"rotate")["revision"]==5
    with pytest.raises(ValueError):review(5,pa,pb)
    with pytest.raises(ValueError):review(5,packet("a",c,99),pb)
    assert review(5,packet("a",c,101),pb)["registry_revision"]==6
    with pytest.raises(ValueError):configure(6,"a",a,"rotate")
    with pytest.raises(journal.JournalError):configure(5,"b",None,"revoke")


def test_acceptance_oracle_and_gate_generator():
    from shared.agent_procurement_portfolio import _combine
    from shared.agent_procurement_trials import _latency
    for old,new,same in product(range(5),range(5),(True,False)):
        if new>old or new==old and same:
            assert trust._acceptance(old,"a",new,"a" if same else "b")==("advance" if new>old else "repeat")
        else:
            with pytest.raises(ValueError):trust._acceptance(old,"a",new,"a" if same else "b")
    for size in range(6):
        for states in product(("pass","unknown","fail"),repeat=size):
            expected="fail" if "fail" in states else "unknown" if "unknown" in states else "pass"
            assert _combine(iter(states))==expected
    assert _latency([])["p95_upper_ms"] is None
