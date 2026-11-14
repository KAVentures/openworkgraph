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


def test_dashboard_invalidates_in_memory_review_after_grant_or_history_mutation():
    """A successful consent change or session deletion must discard old labels.

    Both mutations occur in History, outside the reconstruction workbench.
    This guards against displaying or scoring stale evidence after the user
    revokes access or deletes retained source events.
    """
    from server.main import ROOT
    source = (ROOT / "dashboard" / "history_retention.js").read_text(encoding="utf-8")
    for route, message in (
        ("/v1/history/ai-access", "History access changed. Reload authorized evidence."),
        ("/v1/history/delete-session", "Saved history changed. Reload authorized evidence."),
    ):
        start = source.index("await call('" + route + "'")
        invalidation = source.index("clearReconstructionEvidence('" + message + "')", start)
        assert invalidation > start
        # Clear immediately after the successful request, before toast/refresh.
        assert "});clearReconstructionEvidence(" in source[start:invalidation]
        assert "await refresh()" not in source[start:invalidation]



def test_dashboard_rejects_scores_from_superseded_reviewer_labels():
    """A response for old checkbox selections must not be displayed as current."""
    from server.main import ROOT
    source = (ROOT / "dashboard" / "history_retention.js").read_text(encoding="utf-8")
    assert "let reconstructionLabelEpoch=0;" in source
    assert "reconstructionLabelEpoch++;" in source
    assert "const labelEpoch=reconstructionLabelEpoch;" in source
    assert "if(labelEpoch!==reconstructionLabelEpoch){" in source
    assert source.index("if(labelEpoch!==reconstructionLabelEpoch){") < source.index(
        "root.querySelector('#reconstructionMetrics').textContent=JSON.stringify({"
    )
    assert "Labels changed while scoring. Submit the current review again." in source
    assert "root.querySelector('#reconstructionMetrics').textContent='';" in source


def test_live_review_read_denies_missing_grant_before_database(monkeypatch):
    """Read endpoint must reject absent consent before any event access."""
    _setup(monkeypatch, grant={"mode": "off"})
    monkeypatch.setattr(history_routes, "connect", lambda: pytest.fail("database read before grant"))
    with pytest.raises(HTTPException) as error:
        history_routes.get_reconstruction_review()
    assert error.value.status_code == 403


def test_live_review_read_rechecks_revocation_after_build(monkeypatch):
    """Revoking the lease while a queue is built must suppress its entire payload."""
    state = _setup(monkeypatch)
    from server import work_resumption, reconstruction_review_cli

    def candidates(connection, *, lease):
        assert lease == {"mode": "all_saved"}
        state["generation"] += 1
        return {"coverage": {}, "identity_timelines": [], "notice": None}

    monkeypatch.setattr(work_resumption, "resumption_candidates", candidates)
    monkeypatch.setattr(reconstruction_review_cli, "build_review_queue", lambda result: {"event_reviews": []})
    with pytest.raises(HTTPException) as error:
        history_routes.get_reconstruction_review()
    assert error.value.status_code == 403


def test_live_review_read_is_uncacheable_and_non_authoritative(monkeypatch):
    """A valid read is explicitly provisional and must never be cached."""
    _setup(monkeypatch)
    from server import work_resumption, reconstruction_review_cli

    monkeypatch.setattr(work_resumption, "resumption_candidates", lambda connection, *, lease: {
        "coverage": {"complete": True}, "identity_timelines": [], "notice": None
    })
    monkeypatch.setattr(reconstruction_review_cli, "build_review_queue", lambda result: {
        "event_reviews": [], "evidence_sha256": "b" * 64
    })
    response = history_routes.get_reconstruction_review()
    assert response.headers["cache-control"] == "no-store"
    import json
    payload = json.loads(response.body)
    assert payload["derived"] is True
    assert payload["authoritative"] is False
    assert payload["review"]["evidence_sha256"] == "b" * 64
