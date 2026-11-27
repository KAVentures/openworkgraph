"""R4 exact history review: real publication transactions and HTTP boundaries."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from gateway.analysis_history_review import HistoryRequest, Target, _fraction_text, preview, review
from gateway.analysis_publications import Access, PublicationError
from test_shared_analysis_publications import store, process_store, person, app as app_factory, snapshot as grouped
from test_shared_analysis_revision_comparisons import plain, published


def history(store, values, sources=None, specs=None):
    wid = None
    for i, value in enumerate(values):
        body = published(value, expected=i, source=(sources or "abcdefghij")[i],
                         spec=(specs or "bbbbbbbbbb")[i])
        detail = store.publish(person(), body, wid)
        wid = detail["view_id"]
    return wid


def request_from(p, *, cutoff=None, anchor=1, targets=None):
    rows = p["revisions"][:cutoff]
    return HistoryRequest(pins=[{"revision": row["revision"],
                                 "expected_content_sha256": row["content_sha256"]} for row in rows],
                          anchor_revision=anchor, targets=targets or [])


def target(operator="at_least", threshold="6", upper=None):
    return {"heading": "Measure 1", "operator": operator, "threshold": threshold, "upper": upper}


def test_complete_history_adjacent_anchor_targets_and_exact_distances(store):
    wid = history(store, [4, 8, 6, 2, 7])
    p = preview(store, person(), wid)
    assert p["latest_revision"] == 5
    assert [r["revision"] for r in p["revisions"]] == [1, 2, 3, 4, 5]
    assert all("snapshot" not in r and "rows" not in r for r in p["revisions"])
    request = request_from(p, anchor=3, targets=[target()])
    r = review(store, person(), wid, request)
    assert r["anchor_comparable_count"] == 5 and r["adjacent_comparable_count"] == 4
    assert [x["changes"][0]["change_decimal"] for x in r["anchor_comparisons"]] == ["-2", "2", "0", "-4", "1"]
    assert [x["changes"][0]["change_decimal"] for x in r["adjacent_comparisons"]] == ["4", "-2", "-4", "5"]
    t = r["targets"][0]
    assert [x["status"] for x in t["revision_results"]] == ["outside", "met", "met", "outside", "met"]
    assert [x["distance_to_target_decimal"] for x in t["revision_results"]] == ["2", "0", "0", "4", "0"]
    assert [x["status"] for x in t["transitions"]] == [
        "entered_target", "remained_met", "left_target", "entered_target"]
    assert t["counts"] == {"met": 3, "outside": 2, "withheld": 0}
    assert t["longest_consecutive_met_revisions"] == 2
    assert r["report_sha256"] == review(store, person(), wid, request)["report_sha256"]
    assert not any(r[k] for k in ("execution_authorized", "source_independently_verified",
                                  "case_identities_joined", "causal_effect_estimated", "pooled_totals_reported"))
    audit = json.dumps(store.db.audit_rows("acme", 100))
    assert "analysis.history_reviewed" in audit and "analysis.history_previewed" in audit
    assert "Synthetic case counts" not in audit and "distance_to_target_decimal" not in audit
    assert '"threshold"' not in audit


@pytest.mark.parametrize("op,lo,hi,statuses,gaps", [
    ("at_most", "4", None, ["met", "outside", "met"], ["0", "2", "0"]),
    ("between", "4.000000000000000000000000000001", "6", ["outside", "met", "outside"],
     ["0.000000000000000000000000000001", "0", "2.000000000000000000000000000001"]),
    ("between", "4", "4", ["met", "outside", "outside"], ["0", "2", "2"]),
])
def test_inclusive_target_boundaries_without_decimal_context_rounding(store, op, lo, hi, statuses, gaps):
    wid = history(store, [4, 6, 2])
    r = review(store, person(), wid, request_from(preview(store, person(), wid),
               targets=[target(op, lo, hi)]))
    rows = r["targets"][0]["revision_results"]
    assert [x["status"] for x in rows] == statuses
    assert [x["distance_to_target_decimal"] for x in rows] == gaps


def test_incompatible_definition_breaks_streak_without_interpolation(store):
    wid = history(store, [4, 6, 8, 7, 9], specs="bbcbb")
    r = review(store, person(), wid, request_from(preview(store, person(), wid),
               targets=[target("at_least", "4")]))
    t = r["targets"][0]
    assert [x["status"] for x in t["revision_results"]] == ["met", "met", "withheld", "met", "met"]
    assert t["revision_results"][2]["value"] is None
    assert t["revision_results"][2]["distance_to_target_decimal"] is None
    assert t["longest_consecutive_met_revisions"] == 2
    assert [x["status"] for x in t["transitions"]] == [
        "remained_met", "withheld", "withheld", "remained_met"]
    assert r["adjacent_comparable_count"] == 2


def test_nonadjacent_conflicting_claimed_sources_block_all_affected_pairs(store):
    wid = history(store, [4, 6, 8, 9], sources="abcb")
    r = review(store, person(), wid, request_from(preview(store, person(), wid),
               targets=[target()]))
    assert r["conflicting_claimed_basis_pairs"] == [[2, 4]]
    assert r["adjacent_comparable_count"] == 0
    assert r["anchor_comparable_count"] == 2
    assert [x["status"] for x in r["targets"][0]["revision_results"]] == [
        "outside", "withheld", "met", "withheld"]
    for i in (1, 3):
        p = r["anchor_comparisons"][i]
        assert p["changes"] == []
        assert "history_conflicting_claimed_basis" in p["refusal_reasons"]


def test_anchor_conflict_withholds_all_targets_and_anchor_arithmetic(store):
    wid = history(store, [4, 6, 8], sources="aba")
    r = review(store, person(), wid, request_from(preview(store, person(), wid),
               targets=[target()]))
    assert r["anchor_comparable_count"] == 0
    assert r["targets"][0]["counts"] == {"met": 0, "outside": 0, "withheld": 3}
    assert all(x["value"] is None for x in r["targets"][0]["revision_results"])


def test_grouped_and_truncated_snapshots_never_expose_target_values(store):
    first = store.publish(person(), published(snap=grouped(4)))
    wid = first["view_id"]
    store.publish(person(), published(expected=1, snap=grouped(6)), wid)
    p = preview(store, person(), wid)
    r = review(store, person(), wid, request_from(p, targets=[target()]))
    assert r["anchor_comparable_count"] == 0
    assert "G1_1" not in json.dumps(r)
    assert r["targets"][0]["counts"]["withheld"] == 2


def test_new_revision_is_excluded_and_prefix_pins_stay_exact(store):
    wid = history(store, [4, 6, 8])
    p = preview(store, person(), wid)
    req = request_from(p, cutoff=2)
    store.publish(person(), published(12, expected=3, source="d"), wid)
    r = review(store, person(), wid, req)
    assert r["reviewed_through_revision"] == 2 and r["latest_revision_at_review"] == 4
    assert r["newer_revisions_excluded"] == 2
    assert len(r["revisions"]) == 2
    assert r["anchor_comparisons"][1]["changes"][0]["after"] == 6


def test_all_revisions_integrity_and_digest_pin_failure_are_atomic(store):
    wid = history(store, [4, 6, 8])
    p = preview(store, person(), wid)
    data = request_from(p).model_dump(mode="json")
    data["pins"][1]["expected_content_sha256"] = "0"*64
    with pytest.raises(PublicationError) as exc:
        review(store, person(), wid, HistoryRequest.model_validate(data))
    assert exc.value.status == 409
    with store.db.connect() as conn:
        store.q(conn, "UPDATE gateway_analysis_revisions SET content_sha256=? WHERE organization_id=? AND view_id=? AND revision=?",
                ("0"*64, "acme", wid, 3))
    with pytest.raises(PublicationError, match="integrity"):
        review(store, person(), wid, request_from(p))
    with pytest.raises(PublicationError, match="integrity"):
        preview(store, person(), wid)
    assert not any(a["action"] == "analysis.history_reviewed" for a in store.db.audit_rows("acme", 100))


def test_acl_revocation_and_expiry_prevent_replay(store):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store.now = lambda: now
    wid = history(store, [4, 6])
    reader = person("bob", scopes={"org:analysis:read"})
    for p in (reader, person("eve", org="foreign")):
        with pytest.raises(PublicationError) as exc:
            preview(store, p, wid)
        assert exc.value.status == 404
    store.access(person(), wid, Access(expected_access_revision=1,
                 members=[{"actor_id": "bob", "role": "viewer"}], user_confirmed=True))
    req = request_from(preview(store, reader, wid))
    assert review(store, reader, wid, req)["anchor_comparable_count"] == 2
    store.access(person(), wid, Access(expected_access_revision=2, members=[], user_confirmed=True))
    with pytest.raises(PublicationError) as exc:
        review(store, reader, wid, req)
    assert exc.value.status == 404
    now += timedelta(days=8)
    with pytest.raises(PublicationError) as exc:
        review(store, person(), wid, req)
    assert exc.value.status == 404


def test_invalid_prefix_anchor_targets_and_exact_decimal_strings():
    pin = lambda i: {"revision": i, "expected_content_sha256": "a"*64}
    base = {"pins": [pin(1), pin(2)], "anchor_revision": 1}
    for bad in (
        {"pins": [pin(1), pin(3)]}, {"pins": [pin(2), pin(1)]},
        {"pins": [pin(1), pin(1)]}, {"anchor_revision": 3}, {"anchor_revision": True},
        {"pins": [pin(i) for i in range(1, 12)]},
        {"targets": [target(), target()]}, {"targets": [target(threshold=6)]},
        {"targets": [target(threshold="NaN")]}, {"targets": [target(threshold="1e3")]},
        {"targets": [target("between", "9", "2")]}, {"targets": [target("between", "9")]},
        {"targets": [target("at_most", "9", "10")]}, {"unexpected": True},
    ):
        with pytest.raises(ValidationError):
            HistoryRequest.model_validate({**base, **bad})
    assert _fraction_text(Fraction(Decimal("-0.000000000000000000000000000001"))) == "-0.000000000000000000000000000001"


def test_unknown_anchor_heading_refused_without_audit(store):
    wid = history(store, [4, 6])
    req = request_from(preview(store, person(), wid), targets=[{**target(), "heading": "Measure 6"}])
    with pytest.raises(PublicationError) as exc:
        review(store, person(), wid, req)
    assert exc.value.status == 422
    assert not any(a["action"] == "analysis.history_reviewed" for a in store.db.audit_rows("acme", 100))


def test_real_http_auth_rechecked_strict_schema_and_no_store(tmp_path):
    headers = lambda who: {"Authorization": "Bearer " + who}
    with TestClient(app_factory(tmp_path)) as client:
        base = "/v1/human/analyses"
        first = client.post(base, headers=headers("alice"), json=published().model_dump(mode="json"))
        assert first.status_code == 200
        wid = first.json()["view_id"]
        second = client.post(base+"/"+wid, headers=headers("alice"),
                             json=published(6, expected=1, source="c").model_dump(mode="json"))
        assert second.status_code == 200
        path = base+"/"+wid+"/history-review"
        p = client.get(path, headers=headers("alice"))
        assert p.status_code == 200 and p.headers["cache-control"] == "no-store"
        req = request_from(p.json(), targets=[target()]).model_dump(mode="json")
        for who, status in [("read-only",404),("process-only",403),("bob",404),("foreign",404),("bad",401)]:
            assert client.get(path, headers=headers(who)).status_code == status
            assert client.post(path, headers=headers(who), json=req).status_code == status
        r = client.post(path, headers=headers("alice"), json=req)
        assert r.status_code == 200, r.text
        assert r.headers["cache-control"] == "no-store"
        assert r.json()["targets"][0]["counts"] == {"met": 1, "outside": 1, "withheld": 0}
        assert client.post(path, headers=headers("alice"), json={**req, "extra": True}).status_code == 422
        assert client.post(path, headers={**headers("alice"), "Content-Type":"application/json"},
                           content=b"{}"+b" "*9000).status_code == 413
