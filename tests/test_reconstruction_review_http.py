"""Grant and evidence-bound HTTP scoring regression tests (R1/R2)."""
from contextlib import contextmanager

import pytest
from fastapi import HTTPException

from server import history_routes


@contextmanager
def _connection():
    yield object()


def _setup(monkeypatch, *, grant=None, generation=7):
    from shared import history_policy
    state = {"grant": grant if grant is not None else {"mode": "all_saved"}, "generation": generation}
    monkeypatch.setattr(history_routes, "active_ai_history_access", lambda: dict(state["grant"]))
    monkeypatch.setattr(history_policy, "history_generation", lambda: state["generation"])
    monkeypatch.setattr(history_routes, "connect", _connection)
    return state


def _request():
    return history_routes.ReconstructionReviewSubmission(
        evidence_sha256="a" * 64, annotations=[]
    )


def test_live_scoring_route_uses_host_grant_not_request_lease(monkeypatch):
    _setup(monkeypatch)
    from server import work_resumption

    def score(connection, envelope, *, lease, authorize_review):
        assert lease == {"mode": "all_saved"}
        assert envelope == {"evidence_sha256": "a" * 64, "annotations": []}
        assert authorize_review() is True
        return {"precision": None, "recall": None, "f1": None, "held_out": False}

    monkeypatch.setattr(work_resumption, "score_live_reconstruction_review", score)
    response = history_routes.score_reconstruction_review_submission(_request())
    assert response.headers["cache-control"] == "no-store"
    assert b'"held_out": false' in response.body


def test_live_scoring_route_denies_missing_grant_before_database(monkeypatch):
    _setup(monkeypatch, grant={"mode": "off"})
    monkeypatch.setattr(history_routes, "connect", lambda: pytest.fail("database read before grant"))
    with pytest.raises(HTTPException) as error:
        history_routes.score_reconstruction_review_submission(_request())
    assert error.value.status_code == 403


def test_live_scoring_route_denies_revocation_during_serialization(monkeypatch):
    state = _setup(monkeypatch)
    from server import work_resumption

    def score(connection, envelope, *, lease, authorize_review):
        assert authorize_review()
        state["generation"] += 1
        return {"rows": []}

    monkeypatch.setattr(work_resumption, "score_live_reconstruction_review", score)
    with pytest.raises(HTTPException) as error:
        history_routes.score_reconstruction_review_submission(_request())
    assert error.value.status_code == 403


def test_live_scoring_route_rejects_stale_evidence(monkeypatch):
    _setup(monkeypatch)
    from server import work_resumption

    def score(connection, envelope, *, lease, authorize_review):
        raise ValueError("annotation evidence digest mismatch")

    monkeypatch.setattr(work_resumption, "score_live_reconstruction_review", score)
    with pytest.raises(HTTPException) as error:
        history_routes.score_reconstruction_review_submission(_request())
    assert error.value.status_code == 422


def test_dashboard_review_workbench_uses_live_authenticated_routes_without_persistence():
    """Operator entrypoint must stay tied to live evidence and explicit labels."""
    from server.main import ROOT
    source = (ROOT / "dashboard" / "history_retention.js").read_text(encoding="utf-8")
    assert "reconstructionWorkbench" in source
    assert "reconstructionChoices.some(c=>!c.done.checked)" in source
    assert "'/v1/history/reconstruction-review'" in source
    assert "'/v1/history/reconstruction-review/score'" in source
    assert "evidence_sha256:reconstructionEvidence.evidence_sha256" in source
    assert "true_resource_pairs:" in source
    assert "clearReconstructionEvidence('Review refused:" in source
    assert "held_out_verified:result.held_out_verified" in source
    assert "cross_case_contamination_measured:result.cross_case_contamination_measured" in source
    assert "localStorage.setItem('reconstruction" not in source
    assert "sessionStorage.setItem('reconstruction" not in source
