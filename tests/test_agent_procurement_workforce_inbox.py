"""Real SQLite inbox integration with actual signed packets and trust registry."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from server import agent_procurement_workforce_inbox as inbox
from server import agent_procurement_workforce_trust as trust
from server import agent_procurement_monitor_journal as journal
from test_agent_procurement_monitor_journal import local_db
from test_agent_procurement_workforce_trust import NS, configure, packet, review


def status():
    return inbox.status({"correlation_namespace_sha256":NS,"maximum_packet_age_ms":100},now=150)


def scope(view):
    return {"correlation_namespace_sha256":NS,"maximum_packet_age_ms":100,
        "expected_revision":view["registry_revision"],"expected_inbox_revision":view["inbox_revision"],"confirmed":True}


def admit(view,p):
    return inbox.admit({**scope(view),"signed_packet":p},now=150)


def test_separate_arrivals_complete_review_and_replays(local_db):
    a,b=Ed25519PrivateKey.generate(),Ed25519PrivateKey.generate()
    configure(0,"a",a);configure(1,"b",b)
    initial=status()
    assert not initial["ready_for_complete_review"]
    pa,pb=packet("a",a),packet("b",b)
    first=admit(initial,pa)
    assert first["inbox_revision"]==1 and first["registry_revision"]==2
    assert {r["status"] for r in first["entries"]}=={"available","missing"}
    with pytest.raises(journal.JournalError):inbox.review(scope(first),now=150)
    assert admit(first,pa)["idempotent_replay"]
    with pytest.raises(journal.JournalError):admit(initial,pb)
    second=admit(first,pb)
    assert second["ready_for_complete_review"]
    result=inbox.review(scope(second),now=150)
    assert result["accepted_new_packets"]==2 and result["registry_revision"]==3
    assert result["inbox_revision"]==2
    assert inbox.review(scope(status()),now=150)["exact_repeated_packets"]==2


def test_revoke_rotate_and_forget(local_db):
    a,b=Ed25519PrivateKey.generate(),Ed25519PrivateKey.generate()
    configure(0,"a",a)
    staged=admit(status(),packet("a",a))
    inbox.review(scope(staged),now=150)
    configure(2,"a",None,"revoke")
    assert status()["entries"][0]["status"]=="not_active"
    configure(3,"a",b,"rotate")
    assert status()["entries"][0]["status"]=="key_changed"
    with pytest.raises(ValueError):admit(status(),packet("a",a,101))
    with pytest.raises(ValueError):admit(status(),packet("a",b,99))
    current=admit(status(),packet("a",b,101))
    assert current["ready_for_complete_review"]
    s=scope(current);s.pop("maximum_packet_age_ms")
    removed=inbox.forget(s)
    assert removed["removed_packets"]==1
    assert status()["entries"][0]["status"]=="missing"
    with pytest.raises(ValueError):admit(status(),packet("a",b,99))


def test_superseded_tampered_and_stale_packets(local_db):
    a=Ed25519PrivateKey.generate();configure(0,"a",a)
    v=admit(status(),packet("a",a))
    review(1,packet("a",a,101))
    assert status()["entries"][0]["status"]=="superseded"
    current=admit(status(),packet("a",a,102))
    with pytest.raises(ValueError):admit(current,packet("a",a,151))
    with pytest.raises(ValueError):admit(current,packet("a",a,49))
    bad=deepcopy(packet("a",a,103));bad["signature_hex"]="0"*128
    with pytest.raises(ValueError):admit(current,bad)
    assert status()["inbox_revision"]==current["inbox_revision"]
    with local_db() as conn:conn.execute("UPDATE procurement_workforce_inbox SET packet_json='{}'")
    assert status()["entries"][0]["status"]=="corrupt"
    assert not status()["ready_for_complete_review"]


def test_two_writers_same_revision_only_one_advances(local_db):
    a,b=Ed25519PrivateKey.generate(),Ed25519PrivateKey.generate()
    configure(0,"a",a);configure(1,"b",b)
    v=status()
    def attempt(p):
        try:admit(v,p);return True
        except journal.JournalError:return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        result=list(pool.map(attempt,[packet("a",a),packet("b",b)]))
    assert sum(result)==1 and status()["inbox_revision"]==1


def incident_packet(alias,key,when,flag):
    from server import agent_procurement_workforce_signing as signing
    from test_agent_procurement_workforce_exchange import row,pinned
    item=row(alias);item["rollback_review"]=flag
    return signing.sign_packet(key,pinned(alias,[item],when=when)["packet"])


def test_sticky_rollback_signal_survives_clean_packet_and_removal(local_db):
    key=Ed25519PrivateKey.generate();configure(0,"a",key)
    first=admit(status(),incident_packet("a",key,100,True))
    assert len(first["retained_rollback_signals"])==1
    original=first["retained_rollback_signals"][0]
    second=admit(first,incident_packet("a",key,101,False))
    assert second["retained_rollback_signals"]==[original]
    third=admit(second,incident_packet("a",key,102,True))
    item=third["retained_rollback_signals"][0]
    assert item["first_packet_sha256"]==original["first_packet_sha256"]
    assert item["last_exported_ms"]==102
    result=inbox.review(scope(third),now=150)
    assert result["retained_rollback_signals"]==[item]
    req=scope(status());req.pop("maximum_packet_age_ms")
    assert inbox.forget(req)["retained_rollback_signals_preserved"]
    assert status()["retained_rollback_signals"]==[item]


def test_incident_capacity_rolls_back_packet_admission(local_db,monkeypatch):
    key=Ed25519PrivateKey.generate();configure(0,"a",key)
    old=status()
    monkeypatch.setattr(inbox,"MAX_INCIDENTS",0)
    with pytest.raises(journal.JournalError,match="capacity"):
        admit(old,incident_packet("a",key,100,True))
    assert status()==old


def test_retirement_requires_inactive_namespace_exact_history_and_consent(local_db):
    key=Ed25519PrivateKey.generate();configure(0,"a",key)
    v=admit(status(),incident_packet("a",key,100,True))
    def request(view):
        req=scope(view);req.pop("maximum_packet_age_ms")
        return {**req,"expected_incident_history_sha256":view["incident_history_sha256"],
                "confirm_exported_history":True}
    with pytest.raises(journal.JournalError):inbox.retire(request(v))
    configure(1,"a",None,"revoke")
    v=status()
    with pytest.raises(journal.JournalError):inbox.retire(request(v))
    req=scope(v);req.pop("maximum_packet_age_ms")
    inbox.forget(req);v=status()
    assert v["incident_history_retirement_available"]
    with pytest.raises(ValueError):inbox.retire({**request(v),"confirm_exported_history":False})
    with pytest.raises(journal.JournalError):inbox.retire({**request(v),"expected_incident_history_sha256":"f"*64})
    assert status()==v
    result=inbox.retire(request(v))
    assert result["removed_rollback_signals"]==1 and result["accepted_registry_watermarks_preserved"]
    assert not result["archive_independently_verified"]
    assert not status()["retained_rollback_signals"]
    assert trust.status({})["entries"][0]["active"] is False


def test_portable_dossier_requires_complete_current_inbox_without_advancing_pins(local_db):
    from server import agent_procurement_workforce_dossier as dossier
    from test_agent_procurement_workforce_signing import trust as pin
    a,b=Ed25519PrivateKey.generate(),Ed25519PrivateKey.generate()
    configure(0,"a",a);configure(1,"b",b)
    first=admit(status(),packet("a",a))
    with pytest.raises(journal.JournalError):
        dossier.freeze_inbox(scope(first),now=150)
    complete=admit(first,packet("b",b))
    assert complete["idempotent_replay"] is False
    before_freeze=status()
    assert before_freeze=={key:value for key,value in complete.items() if key!="idempotent_replay"}
    with pytest.raises(journal.JournalError):
        dossier.freeze_inbox(scope(first),now=150)
    with pytest.raises(ValueError):
        dossier.freeze_inbox({**scope(complete),"confirmed":False},now=150)
    with pytest.raises(journal.JournalError):
        dossier.freeze_inbox(scope(complete),now=201)
    doc=dossier.freeze_inbox(scope(complete),now=150)
    receipt=dossier.verify(doc,{"expected_correlation_namespace_sha256":NS,
        "trust":[pin("a",a),pin("b",b)]},expected_dossier_sha256=doc["dossier_sha256"])
    assert receipt["historical_report_recomputed"]
    assert status()==before_freeze
    assert all(r["accepted_at_ms"] is None for r in trust.status({})["entries"])
    assert inbox.review(scope(complete),now=150)["accepted_new_packets"]==2
