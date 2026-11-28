"""Real retained incident-store, concurrency and HTTP contract tests."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import random

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from server import automation_incident_batch as batch
from server import automation_incident_batch_routes as routes
from server import automation_incidents as incidents


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(incidents, "DATA_DIR", tmp_path)


def pin(item):
    return {"incident_id": item["incident_id"], "expected_last_seen": item["last_seen"],
            "expected_review_revision": item.get("review_revision", 0),
            "expected_occurrences": item["occurrences"],
            "expected_membership_sha256": item.get("run_membership_sha256")}


def selected(n=3):
    return {"selection": [pin(incidents.record("dep_" + "1"*16, "lost_reports", f"gap:{k}"))
                          for k in range(n)], "note": " Review started "}


def ready(p):
    return {**p, "confirm": True, "preview_sha256": batch.preview(p)["preview_sha256"]}


def test_atomic_file_write_and_unselected_preserved(monkeypatch):
    p = selected()
    other = incidents.record("dep_" + "2"*16, "signature_rejected", "other")
    original = incidents._write
    writes = []
    def write(data):
        writes.append(1)
        original(data)
    monkeypatch.setattr(incidents, "_write", write)
    q = ready(p)
    assert not writes
    result = batch.acknowledge(q)
    assert result["acknowledged_count"] == 3 and not result["resolution_claimed"]
    assert not result["execution_authorized"]
    current = incidents._load()["incidents"]
    assert current[other["incident_id"]] == other
    for row in p["selection"]:
        item = current[row["incident_id"]]
        assert item["status"] == "acknowledged" and item["review_revision"] == 1
        assert item["notes"][-1]["note"] == "Review started"
    assert len(writes) == 1
    with pytest.raises(incidents.IncidentError):
        batch.acknowledge(q)
    assert len(writes) == 1


@pytest.mark.parametrize("mutate", [
    lambda x: x.update(status="resolved"), lambda x: x.update(last_seen="later"),
    lambda x: x.update(occurrences=99), lambda x: x.update(review_revision=1),
    lambda x: x.update(run_membership_sha256="a"*64),
    lambda x: x.update(notes=[{"note": "different"}]),
    lambda x: x.update(fingerprint="different"),
])
def test_one_changed_record_refuses_entire_batch(mutate):
    p = selected()
    q = ready(p)
    data = incidents._load()
    mutate(data["incidents"][p["selection"][-1]["incident_id"]])
    incidents._write(data)
    before = incidents._path().read_bytes()
    with pytest.raises(incidents.IncidentError):
        batch.acknowledge(q)
    assert incidents._path().read_bytes() == before


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(confirm=1), lambda p: p.update(note="different"),
    lambda p: p.update(note="bad\nline"), lambda p: p.update(preview_sha256="0"*64),
    lambda p: p["selection"].append(p["selection"][0]),
    lambda p: p["selection"][0].update(expected_review_revision=True),
    lambda p: p.update(selection=[]), lambda p: p.update(status="resolved"),
])
def test_malformed_or_changed_review_refused(mutate):
    p = selected()
    q = ready(p)
    before = incidents._path().read_bytes()
    mutate(q)
    with pytest.raises(incidents.IncidentError):
        batch.acknowledge(q)
    assert incidents._path().read_bytes() == before


def test_concurrent_replay_has_one_winner():
    q = ready(selected(20))
    def apply(_):
        try:
            return batch.acknowledge(q)["acknowledged_count"]
        except incidents.IncidentError:
            return 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(apply, range(16))) == 20
    assert incidents.listing()["counts"]["acknowledged"] == 20


def test_random_subsets_do_not_change_unselected_rows():
    rng = random.Random(920)
    for _ in range(60):
        incidents._path().unlink(missing_ok=True)
        n = rng.randint(1, 50)
        p = selected(n)
        p["selection"] = rng.sample(p["selection"], rng.randint(1, n))
        ids = {x["incident_id"] for x in p["selection"]}
        before = deepcopy(incidents._load()["incidents"])
        result = batch.acknowledge(ready(p))
        assert set(result["acknowledged_incident_ids"]) == ids
        for key, row in incidents._load()["incidents"].items():
            if key in ids:
                assert row["status"] == "acknowledged" and row["review_revision"] == 1
            else:
                assert row == before[key]


def test_note_retention_and_individual_triage_compatibility():
    p = selected(1)
    iid = p["selection"][0]["incident_id"]
    data = incidents._load()
    item = data["incidents"][iid]
    item["notes"] = [{"at": item["last_seen"], "status": "open", "note": str(k)}
                     for k in range(incidents.MAX_NOTES)]
    incidents._write(data)
    batch.acknowledge(ready(p))
    row = incidents._load()["incidents"][iid]
    assert len(row["notes"]) == incidents.MAX_NOTES
    assert row["notes"][0]["note"] == "1"
    decision = incidents.triage(iid, {"status": "resolved", "note": "Individual review",
                                   "expected_last_seen": row["last_seen"],
                                   "expected_review_revision": row["review_revision"]})
    assert decision["status"] == "resolved" and decision["review_revision"] == 2


def test_http_routes_require_dashboard_and_exact_review(monkeypatch):
    # Exercise real ASGI parsing, route admission and file persistence.
    # The session validator is isolated here; browser acceptance uses a real session.
    monkeypatch.setattr(routes, "dashboard_session_valid", lambda token: token == "owned-test")
    app = FastAPI()
    app.include_router(routes.router)
    base = "/v1/automation-monitoring/incidents/batch/"
    p = selected(2)
    with TestClient(app) as c:
        assert c.get("/automation-incident-batch.js").status_code == 200
        assert c.post(base+"preview", json=p).status_code == 403
        assert c.post(base+"preview", json=p, headers={"Authorization":"Bearer token"}).status_code == 403
        auth = {"Authorization": "OWG-Session owned-test"}
        r = c.post(base+"preview", json=p, headers=auth)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
        q = {**p, "preview_sha256": r.json()["preview_sha256"], "confirm": True}
        assert c.post(base+"acknowledge", json={**q, "confirm": False}, headers=auth).status_code == 422
        assert c.post(base+"acknowledge", json=q, headers=auth).json()["acknowledged_count"] == 2
        assert c.post(base+"acknowledge", json=q, headers=auth).status_code == 422
